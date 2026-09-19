"""Product aggregate: the top-level unit of factory work (T065, ADR-030).

A product is a domain entity, not a field of a change (ADR-030 p.1): it owns the
readiness of its repository (``status``), the reference to its canonical Product
Baseline (``baseline_ref``, ADR-020) and the reference to its dev delivery
environment (``dev_env_ref``). ``Change.product_id`` groups changes under it
(ADR-030 p.2) and stays optional for pre-T065 changes.
"""

from datetime import UTC, datetime
from typing import Final

from pydantic import BaseModel, Field

from dark_factory.changes.enums import ProductStatus
from dark_factory.changes.refs import RepositoryRef
from dark_factory.changes.run import InvalidStatusTransition

PRODUCT_STATUS_TRANSITIONS: Final[dict[ProductStatus, frozenset[ProductStatus]]] = {
    ProductStatus.CREATED: frozenset({ProductStatus.VALIDATING}),
    ProductStatus.VALIDATING: frozenset({ProductStatus.READY, ProductStatus.ERROR}),
    # Both outcomes are final: a failed validation is not silently retried, a new
    # attempt would be an explicit operator action (ADR-030 p.4).
    ProductStatus.READY: frozenset(),
    ProductStatus.ERROR: frozenset(),
}

PRODUCT_TERMINAL_STATUSES: Final[frozenset[ProductStatus]] = frozenset(
    status for status, targets in PRODUCT_STATUS_TRANSITIONS.items() if not targets
)


def _now() -> datetime:
    return datetime.now(UTC)


class Product(BaseModel):
    """A product under factory management (ADR-030 p.1).

    ``status`` is the readiness of the repository; ``status_reason`` carries the
    cause of an ``error`` status instead of encoding it into the status value.
    ``state_revision`` is the optimistic concurrency guard, like ``ChangeRun``
    (ADR-006 p.4).
    """

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str | None = None
    repository: RepositoryRef
    repository_url: str | None = None
    baseline_ref: str | None = None
    dev_env_ref: str | None = None
    status: ProductStatus = ProductStatus.CREATED
    status_reason: str | None = None
    state_revision: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=_now)

    def apply_status(self, target: ProductStatus, *, reason: str | None = None) -> None:
        """Move to ``target``; raises InvalidStatusTransition outside the table.

        ``reason`` is required for ``error`` and rejected otherwise: the failure
        cause belongs in ``status_reason``, never in the status value.
        """
        if target not in PRODUCT_STATUS_TRANSITIONS[self.status]:
            raise InvalidStatusTransition(
                f"Product transition {self.status.value} -> {target.value} is not allowed"
            )
        if target is ProductStatus.ERROR and reason is None:
            raise ValueError("ProductStatus.ERROR requires a reason")
        if reason is not None and target is not ProductStatus.ERROR:
            raise ValueError("a reason is only allowed for ProductStatus.ERROR")
        self.status = target
        self.status_reason = reason
        self.state_revision += 1
