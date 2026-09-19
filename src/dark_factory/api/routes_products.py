"""Product registry endpoints (T066, ADR-030, contract api.md).

Registration and validation are operator actions (ADR-030 p.6): both require a
bearer token with the ``products:write`` scope and the operator role, and both
append one audit row inside the caller's transaction (ADR-009 p.7). Registration
replays by product id, so a retry cannot create a second product (FR-017).

Validation observes the repository through ``RepositoryProvisioningPort``
(ADR-031 p.1) and records the outcome in the product status: the state machine
passes through ``validating`` and lands in ``ready`` or ``error`` — a repository
the factory cannot reach is ``error``, with the cause in ``status_reason``
(ADR-030 p.4). Without a configured provisioning port the endpoint refuses with
503 and leaves the status untouched: the factory never reports readiness it did
not observe (ADR-031 p.6).
"""

import asyncio
from collections.abc import Callable, Iterator
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Response
from sqlalchemy.orm import Session

from dark_factory.api.auth import (
    SCOPE_PRODUCTS_WRITE,
    ApiToken,
    ApiTokenStore,
    require_write,
)
from dark_factory.api.dto import (
    ProductCreateRequest,
    ProductValidateRequest,
    ProductValidationView,
)
from dark_factory.changes.enums import ProductStatus
from dark_factory.changes.product import Product
from dark_factory.orchestration.state.change_store import (
    PRODUCT_ADD_ACTION,
    PRODUCT_VALIDATE_ACTION,
    AuditRepository,
    ProductRepository,
)
from dark_factory.orchestration.state.repositories import StateConflictError
from dark_factory.ports import (
    RepositoryProvisioningPort,
    RepositoryState,
    RepositoryValidation,
)

PROVISIONING_UNCONFIGURED_DETAIL = (
    "repository provisioning is not configured in this contour, so readiness cannot"
    " be observed; the product status is unchanged"
)


def _outcome(validation: RepositoryValidation) -> tuple[ProductStatus, str | None]:
    """Readiness of one observation (ADR-030 p.4, ADR-031 p.4/p.6).

    ``ready`` means "the factory reached the repository": an empty repository and
    a missing baseline are normal states in which the baseline is created later
    (ADR-031 p.4), so neither is a failure. Only an unreachable repository is
    ``error``; the cause goes to ``status_reason``.
    """
    if validation.state is RepositoryState.UNAVAILABLE:
        return ProductStatus.ERROR, "the repository is not available to the factory"
    return ProductStatus.READY, None


def create_products_router(
    session_dependency: Callable[..., Iterator[Session]],
    token_store: ApiTokenStore,
    provisioning: RepositoryProvisioningPort | None = None,
) -> APIRouter:
    """Build the ``/products`` router with its auth dependencies."""
    SessionDep = Annotated[Session, Depends(session_dependency)]
    products_write = require_write(token_store, SCOPE_PRODUCTS_WRITE, require_operator_role=True)
    ProductTokenDep = Annotated[ApiToken, Depends(products_write)]
    IdempotencyKey = Annotated[str | None, Header()]
    RouterBody = Annotated[ProductValidateRequest | None, Body()]
    router = APIRouter()

    def _audit(
        token: ApiToken,
        action: str,
        product_id: str,
        outcome: str,
        idempotency_key: str | None,
        session: Session,
    ) -> None:
        AuditRepository(session).append(
            actor=token.actor,
            role=token.role,
            action=action,
            resource_type="product",
            resource_id=product_id,
            idempotency_key=idempotency_key,
            outcome=outcome,
        )

    @router.get("/products", response_model=list[Product])
    def list_products(
        session: SessionDep,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> list[Product]:
        return ProductRepository(session).list(limit=limit, offset=offset)

    @router.get("/products/{product_id}", response_model=Product)
    def get_product(product_id: str, session: SessionDep) -> Product:
        product = ProductRepository(session).get(product_id)
        if product is None:
            raise HTTPException(status_code=404, detail=f"Product {product_id!r} does not exist")
        return product

    @router.post("/products", status_code=201, response_model=Product)
    def create_product(
        token: ProductTokenDep,
        session: SessionDep,
        body: ProductCreateRequest,
        response: Response,
        idempotency_key: IdempotencyKey = None,
    ) -> Product:
        """Register one product (ADR-030 p.1): create it, or replay an existing id (FR-017)."""
        repository = ProductRepository(session)
        existing = repository.get(body.id)
        if existing is not None:
            _audit(token, PRODUCT_ADD_ACTION, existing.id, "replayed", idempotency_key, session)
            response.status_code = 200
            return existing
        product = Product(
            id=body.id,
            name=body.name,
            description=body.description,
            repository=body.repository,
            repository_url=body.repository_url,
            baseline_ref=body.baseline_ref,
            dev_env_ref=body.dev_env_ref,
        )
        created, _ = repository.create(product)
        _audit(token, PRODUCT_ADD_ACTION, created.id, "created", idempotency_key, session)
        return created

    @router.post("/products/{product_id}/validate", response_model=ProductValidationView)
    def validate_product(
        token: ProductTokenDep,
        session: SessionDep,
        product_id: str,
        body: RouterBody = None,
        idempotency_key: IdempotencyKey = None,
    ) -> ProductValidationView:
        """Observe the product repository and record readiness (ADR-030/ADR-031)."""
        repository = ProductRepository(session)
        product = repository.get(product_id)
        if product is None:
            raise HTTPException(status_code=404, detail=f"Product {product_id!r} does not exist")
        if provisioning is None:
            # Fail-closed: answering 200 with an invented status would fabricate
            # evidence the factory never observed (ADR-031 p.6).
            raise HTTPException(status_code=503, detail=PROVISIONING_UNCONFIGURED_DETAIL)
        if (
            body is not None
            and body.expected_state_revision is not None
            and body.expected_state_revision != product.state_revision
        ):
            raise HTTPException(status_code=409, detail="state_revision mismatch")

        validation = asyncio.run(provisioning.validate(product.repository))
        target, reason = _outcome(validation)
        try:
            # The state machine passes through ``validating`` (ADR-030 p.4). Both
            # writes share the request transaction, so an observer sees the outcome.
            if product.status is not ProductStatus.VALIDATING:
                product = repository.update_status(
                    product_id, ProductStatus.VALIDATING, expected_revision=product.state_revision
                )
            product = repository.update_status(
                product_id, target, expected_revision=product.state_revision, reason=reason
            )
        except StateConflictError:
            raise HTTPException(status_code=409, detail="state_revision mismatch") from None
        _audit(token, PRODUCT_VALIDATE_ACTION, product_id, "created", idempotency_key, session)
        return ProductValidationView(**product.model_dump(), validation=validation)

    return router
