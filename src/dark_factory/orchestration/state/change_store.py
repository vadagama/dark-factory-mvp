"""Repositories over change intake, products, approvals and audit (T035/T065, ADR-009 p.7).

The repositories are thin and idempotent: intake dedupes by change id and by
the tracker ``external_ref`` (partial unique index), decisions replay by the
``Idempotency-Key`` (same key → same decision), product registration dedupes by
product id, and every mutating API operation appends one audit row inside the
caller's transaction (ADR-009 p.7).
"""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from dark_factory.changes.enums import DecisionOutcome, DecisionSource, Gate, ProductStatus, Role
from dark_factory.changes.findings import Decision
from dark_factory.changes.product import Product
from dark_factory.changes.run import Change
from dark_factory.orchestration.state.models import (
    AuditLogEntry,
)
from dark_factory.orchestration.state.models import (
    Change as ChangeRow,
)
from dark_factory.orchestration.state.models import (
    Decision as DecisionRow,
)
from dark_factory.orchestration.state.models import (
    Product as ProductRow,
)
from dark_factory.orchestration.state.repositories import StateConflictError

CHANGE_INTAKE_ACTION: str = "change.intake"
PRODUCT_ADD_ACTION: str = "product.add"
PRODUCT_VALIDATE_ACTION: str = "product.validate"
APPROVAL_RECORD_ACTION: str = "approval.record"


class ChangeAlreadyExistsError(RuntimeError):
    """A change with the same id or external_ref already exists."""


class ProductAlreadyExistsError(RuntimeError):
    """A product with the same id already exists but could not be read back."""


def _now() -> datetime:
    return datetime.now(UTC)


def _change_from_row(row: ChangeRow) -> Change:
    return Change.model_validate(row.payload)


def _product_from_row(row: ProductRow) -> Product:
    return Product.model_validate(row.payload)


def _role_from_value(value: str | None) -> Role | None:
    """Parse an agent role; API roles (``operator``/``service``) are not agent roles."""
    if value is None:
        return None
    try:
        return Role(value)
    except ValueError:
        return None


def _decision_from_row(row: DecisionRow) -> Decision:
    return Decision(
        id=row.id,
        gate=Gate(row.gate),
        outcome=DecisionOutcome(row.outcome),
        decided_by=DecisionSource(row.decided_by),
        role=_role_from_value(row.role),
        decided_at=row.decided_at,
        commit_sha=row.commit_sha,
        comment=row.comment,
        evidence_ids=list(row.evidence_ids),
    )


class ChangeRepository:
    """Idempotent intake of Change documents with external_ref dedup (FR-017)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, change: Change) -> tuple[Change, bool]:
        """Insert one change; ``(existing, False)`` when the id already exists."""
        payload = change.model_dump(mode="json")
        stmt = (
            pg_insert(ChangeRow)
            .values(
                id=change.id,
                title=change.title,
                description=change.description,
                source=change.source.value,
                external_ref=change.external_ref,
                risk_class=change.risk_class.value,
                product=change.product.model_dump(mode="json"),
                product_id=change.product_id,
                payload=payload,
                state_revision=1,
                created_at=change.created_at,
            )
            .on_conflict_do_nothing(index_elements=[ChangeRow.id])
            .returning(ChangeRow.id)
        )
        inserted = self._session.execute(stmt).scalar_one_or_none()
        if inserted is not None:
            return change, True
        existing = self.get(change.id)
        if existing is None:  # pragma: no cover - defensive
            raise ChangeAlreadyExistsError(f"change {change.id!r} was not created")
        return existing, False

    def get(self, change_id: str) -> Change | None:
        row = self._session.get(ChangeRow, change_id)
        return _change_from_row(row) if row is not None else None

    def get_raw(self, change_id: str) -> ChangeRow | None:
        """The ORM row, e.g. for optimistic-concurrency revision checks (ADR-006 p.4)."""
        return self._session.get(ChangeRow, change_id)

    def find_by_external_ref(self, external_ref: str) -> Change | None:
        row = self._session.execute(
            select(ChangeRow).where(ChangeRow.external_ref == external_ref).limit(1)
        ).scalar_one_or_none()
        return _change_from_row(row) if row is not None else None

    def list(self, *, limit: int = 50, offset: int = 0) -> list[Change]:
        rows = self._session.execute(
            select(ChangeRow)
            .order_by(ChangeRow.created_at, ChangeRow.id)
            .limit(limit)
            .offset(offset)
        ).scalars()
        return [_change_from_row(row) for row in rows]


class ProductRepository:
    """Idempotent registration and status transitions of products (T065, ADR-030)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, product: Product) -> tuple[Product, bool]:
        """Insert one product; ``(existing, False)`` when the id already exists."""
        payload = product.model_dump(mode="json")
        stmt = (
            pg_insert(ProductRow)
            .values(
                id=product.id,
                name=product.name,
                description=product.description,
                repository=product.repository.model_dump(mode="json"),
                repository_url=product.repository_url,
                baseline_ref=product.baseline_ref,
                dev_env_ref=product.dev_env_ref,
                status=product.status.value,
                status_reason=product.status_reason,
                payload=payload,
                state_revision=product.state_revision,
                created_at=product.created_at,
            )
            .on_conflict_do_nothing(index_elements=[ProductRow.id])
            .returning(ProductRow.id)
        )
        inserted = self._session.execute(stmt).scalar_one_or_none()
        if inserted is not None:
            return product, True
        existing = self.get(product.id)
        if existing is None:  # pragma: no cover - defensive
            raise ProductAlreadyExistsError(f"product {product.id!r} was not created")
        return existing, False

    def get(self, product_id: str) -> Product | None:
        row = self._session.get(ProductRow, product_id)
        return _product_from_row(row) if row is not None else None

    def list(self, *, limit: int = 50, offset: int = 0) -> list[Product]:
        rows = self._session.execute(
            select(ProductRow)
            .order_by(ProductRow.created_at, ProductRow.id)
            .limit(limit)
            .offset(offset)
        ).scalars()
        return [_product_from_row(row) for row in rows]

    def update_status(
        self,
        product_id: str,
        target: ProductStatus,
        *,
        expected_revision: int,
        reason: str | None = None,
    ) -> Product:
        """Move one product to ``target`` under the optimistic revision (ADR-006 p.4).

        The target must be reachable from the persisted status through
        ``PRODUCT_STATUS_TRANSITIONS``; anything else raises
        ``InvalidStatusTransition``, so no persisted status can drift from the
        domain table (ADR-024, условие 3). ``reason`` is required for ``error``
        and rejected otherwise: the failure cause belongs in ``status_reason``.
        """
        row = self._session.get(ProductRow, product_id, with_for_update=True)
        if row is None:
            raise StateConflictError(f"product {product_id!r} does not exist")
        if row.state_revision != expected_revision:
            raise StateConflictError(
                f"product {product_id!r} is at revision {row.state_revision}, "
                f"expected {expected_revision}"
            )
        product = _product_from_row(row)
        product.apply_status(target, reason=reason)
        row.status = product.status.value
        row.status_reason = product.status_reason
        row.state_revision = product.state_revision
        row.payload = product.model_dump(mode="json")
        self._session.flush()
        return product


class DecisionRepository:
    """Version-bound approval decisions with Idempotency-Key replay (ADR-009 p.7)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def record(
        self,
        decision: Decision,
        *,
        change_id: str,
        idempotency_key: str | None = None,
        actor_role: str | None = None,
    ) -> tuple[Decision, bool]:
        """Record one decision; ``(same decision, False)`` when the key was seen before.

        ``actor_role`` is the API role of the caller (``operator``/``service``);
        it is stored raw because it is not an agent role (ADR-007).
        """
        if idempotency_key is not None:
            replayed = self.find_by_idempotency_key(idempotency_key)
            if replayed is not None:
                return replayed, False
        self._session.add(
            DecisionRow(
                id=decision.id,
                change_id=change_id,
                gate=decision.gate.value,
                outcome=decision.outcome.value,
                decided_by=decision.decided_by.value,
                role=actor_role,
                commit_sha=decision.commit_sha,
                comment=decision.comment,
                decided_at=decision.decided_at,
                evidence_ids=list(decision.evidence_ids),
                idempotency_key=idempotency_key,
            )
        )
        self._session.flush()
        return decision, True

    def find_by_idempotency_key(self, idempotency_key: str) -> Decision | None:
        row = self._session.execute(
            select(DecisionRow).where(DecisionRow.idempotency_key == idempotency_key).limit(1)
        ).scalar_one_or_none()
        return _decision_from_row(row) if row is not None else None

    def get(self, decision_id: str) -> Decision | None:
        row = self._session.get(DecisionRow, decision_id)
        return _decision_from_row(row) if row is not None else None

    def list_for_change(self, change_id: str) -> list[Decision]:
        rows = self._session.execute(
            select(DecisionRow)
            .where(DecisionRow.change_id == change_id)
            .order_by(DecisionRow.decided_at, DecisionRow.id)
        ).scalars()
        return [_decision_from_row(row) for row in rows]


class AuditRepository:
    """Append-only audit of mutating API operations (ADR-009 p.7)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def append(
        self,
        *,
        actor: str,
        action: str,
        resource_type: str,
        resource_id: str,
        outcome: str,
        role: str | None = None,
        idempotency_key: str | None = None,
        details: dict[str, object] | None = None,
    ) -> AuditLogEntry:
        """Append one audit row inside the caller's transaction; never log secrets."""
        entry = AuditLogEntry(
            id=uuid4().hex,
            actor=actor,
            role=role,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            idempotency_key=idempotency_key,
            outcome=outcome,
            details=details or {},
        )
        self._session.add(entry)
        self._session.flush()
        return entry

    def list_for(self, resource_type: str, resource_id: str) -> list[AuditLogEntry]:
        return list(
            self._session.execute(
                select(AuditLogEntry)
                .where(
                    AuditLogEntry.resource_type == resource_type,
                    AuditLogEntry.resource_id == resource_id,
                )
                .order_by(AuditLogEntry.occurred_at, AuditLogEntry.id)
            ).scalars()
        )
