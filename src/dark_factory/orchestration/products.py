"""Product readiness: the one core rule the API and the CLI share (T066/T070, ADR-030).

Validating a product means observing its repository through
``RepositoryProvisioningPort`` (ADR-031 p.1) and recording the outcome in the
product status: the state machine passes through ``validating`` and lands in
``ready`` or ``error`` (ADR-030 p.4). Both operator surfaces — ``POST
/products/{id}/validate`` and ``factory product validate`` — apply exactly this
module, so the CLI and the Console cannot disagree on what "ready" means.

``ready`` means "the factory reached the repository": an empty repository and a
missing baseline are normal states in which the baseline is created later
(ADR-031 p.4), so neither is a failure. Only an unreachable repository is
``error``, with the cause in ``status_reason`` rather than in the status value.
Without a configured provisioning port readiness cannot be observed, and the
callers refuse instead of inventing a status (ADR-031 p.6).
"""

import asyncio
from typing import Final

from dark_factory.changes.enums import ProductStatus
from dark_factory.changes.product import Product
from dark_factory.orchestration.state.change_store import ProductRepository
from dark_factory.ports import RepositoryProvisioningPort, RepositoryState, RepositoryValidation

__all__ = [
    "PROVISIONING_UNCONFIGURED_DETAIL",
    "UNAVAILABLE_REASON",
    "observe_repository",
    "product_readiness",
    "record_validation",
]

PROVISIONING_UNCONFIGURED_DETAIL: Final[str] = (
    "repository provisioning is not configured in this contour, so readiness cannot"
    " be observed; the product status is unchanged"
)
"""Refusal of a validation without a provisioning port (ADR-031 p.6): shared wording."""

UNAVAILABLE_REASON: Final[str] = "the repository is not available to the factory"
"""``status_reason`` of an ``error`` outcome: the only failure validation knows."""


def product_readiness(validation: RepositoryValidation) -> tuple[ProductStatus, str | None]:
    """Readiness of one observation (ADR-030 p.4, ADR-031 p.4/p.6).

    ``UNAVAILABLE`` is ``error`` with :data:`UNAVAILABLE_REASON`; every observable
    state is ``ready`` without a reason — the fine-grained state (empty, baseline
    absent/current/stale) travels in the observation itself, not in the status.
    """
    if validation.state is RepositoryState.UNAVAILABLE:
        return ProductStatus.ERROR, UNAVAILABLE_REASON
    return ProductStatus.READY, None


def observe_repository(
    provisioning: RepositoryProvisioningPort, product: Product
) -> RepositoryValidation:
    """Observe the product repository once; validation mutates nothing (ADR-031 p.5)."""
    return asyncio.run(provisioning.validate(product.repository))


def record_validation(
    repository: ProductRepository, product: Product, validation: RepositoryValidation
) -> Product:
    """Record one observation in the product status inside the caller's transaction.

    The state machine passes through ``validating`` (ADR-030 p.4) — a product
    already ``validating`` continues from there — and lands in the outcome of
    :func:`product_readiness`. Every write is guarded by the optimistic revision
    of the product the caller read, so a concurrent transition surfaces as
    ``StateConflictError`` instead of a silent overwrite (ADR-006 p.4).
    """
    target, reason = product_readiness(validation)
    if product.status is not ProductStatus.VALIDATING:
        product = repository.update_status(
            product.id, ProductStatus.VALIDATING, expected_revision=product.state_revision
        )
    return repository.update_status(
        product.id, target, expected_revision=product.state_revision, reason=reason
    )
