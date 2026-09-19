"""Product registry endpoints (T066, ADR-030, contract api.md).

Registration and validation are operator actions (ADR-030 p.6): both require a
bearer token with the ``products:write`` scope and the operator role, and both
append one audit row inside the caller's transaction (ADR-009 p.7). Registration
replays by product id, so a retry cannot create a second product (FR-017).

Validation observes the repository through ``RepositoryProvisioningPort``
(ADR-031 p.1) and records the outcome in the product status through the core
rule of ``orchestration.products`` — the same rule ``factory product validate``
applies (T070), so the API and the CLI agree on readiness: the state machine
passes through ``validating`` and lands in ``ready`` or ``error`` — a repository
the factory cannot reach is ``error``, with the cause in ``status_reason``
(ADR-030 p.4). Without a configured provisioning port the endpoint refuses with
503 and leaves the status untouched: the factory never reports readiness it did
not observe (ADR-031 p.6).
"""

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
    ProductBootstrapRequest,
    ProductBootstrapView,
    ProductCreateRequest,
    ProductValidateRequest,
    ProductValidationView,
)
from dark_factory.changes.product import Product
from dark_factory.orchestration.guidance import Guidance
from dark_factory.orchestration.products import (
    PROVISIONING_UNCONFIGURED_DETAIL,
    bootstrap_baseline,
    observe_repository,
    record_validation,
)
from dark_factory.orchestration.state.change_store import (
    PRODUCT_ADD_ACTION,
    PRODUCT_BOOTSTRAP_ACTION,
    PRODUCT_VALIDATE_ACTION,
    AuditRepository,
    ProductRepository,
)
from dark_factory.orchestration.state.guidance import build_product_guidance
from dark_factory.orchestration.state.repositories import StateConflictError
from dark_factory.ports import ProvisioningOperationUnsupportedError, RepositoryProvisioningPort

__all__ = ["PROVISIONING_UNCONFIGURED_DETAIL", "create_products_router"]


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

    @router.get("/products/{product_id}/guidance", response_model=Guidance)
    def get_product_guidance(product_id: str, session: SessionDep) -> Guidance:
        """The operator's next step for the product (T074, ADR-033) — a read model."""
        product = ProductRepository(session).get(product_id)
        if product is None:
            raise HTTPException(status_code=404, detail=f"Product {product_id!r} does not exist")
        return build_product_guidance(session, product)

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

        validation = observe_repository(provisioning, product)
        try:
            # Both writes share the request transaction, so an observer sees the
            # outcome, never the intermediate ``validating`` (ADR-030 p.4).
            product = record_validation(repository, product, validation)
        except StateConflictError:
            raise HTTPException(status_code=409, detail="state_revision mismatch") from None
        _audit(token, PRODUCT_VALIDATE_ACTION, product_id, "created", idempotency_key, session)
        return ProductValidationView(**product.model_dump(), validation=validation)

    @router.post("/products/{product_id}/bootstrap", response_model=ProductBootstrapView)
    def bootstrap_product(
        token: ProductTokenDep,
        session: SessionDep,
        product_id: str,
        body: ProductBootstrapRequest | None = None,
        idempotency_key: IdempotencyKey = None,
    ) -> ProductBootstrapView:
        """Apply the baseline packs to the product repository (T069/ADR-031 p.3, M2 intake).

        The operator's «Подготовить baseline»: an empty or baseline-less
        repository gets its ``.factory/`` skeleton as one commit on the default
        branch, replay-safe by ``Idempotency-Key``. 503 without a provisioning
        port, 409 when the bound adapter cannot perform a bootstrap (the
        operator-prepared local mirror is read-only, ADR-031 p.2) — the factory
        never reports a baseline it did not create (ADR-031 p.6).
        """
        product = ProductRepository(session).get(product_id)
        if product is None:
            raise HTTPException(status_code=404, detail=f"Product {product_id!r} does not exist")
        if provisioning is None:
            raise HTTPException(status_code=503, detail=PROVISIONING_UNCONFIGURED_DETAIL)
        packs = body.packs if body is not None and body.packs else ["product-baseline"]
        key = idempotency_key or f"bootstrap:{product_id}:{','.join(packs)}"
        try:
            result = bootstrap_baseline(provisioning, product, packs=packs, idempotency_key=key)
        except ProvisioningOperationUnsupportedError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        _audit(token, PRODUCT_BOOTSTRAP_ACTION, product_id, "created", idempotency_key, session)
        return ProductBootstrapView(product=product, result=result)

    return router
