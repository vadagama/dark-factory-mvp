"""Repositories over the operational state, encoding the ADR-006/016 invariants.

The repositories are thin: they translate the durable-state rules into
transactional SQL and raise explicit errors. Cross-cutting guarantees:

- ``operation_key`` uniqueness — one logical stage operation per input revision,
  retries append attempts (ADR-006 p.3);
- ``state_revision`` + ``fencing_token`` guard every execution mutation
  (ADR-006 p.4/p.6);
- ``effect_key`` uniqueness plus marker lookup give effectively-once side
  effects (ADR-006 p.3);
- ``outbox`` + ``event_delivery`` are written in the caller's transaction
  together with the state change (ADR-016 p.1/p.5).
"""

from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from dark_factory.changes.enums import Provider, Route, RunStatus, Stage, StageStatus
from dark_factory.changes.keys import attempt_id as compose_attempt_id
from dark_factory.changes.keys import operation_key as compose_operation_key
from dark_factory.changes.run import RUN_TERMINAL_STATUSES
from dark_factory.orchestration.state.enums import DeliveryStatus, EffectStatus
from dark_factory.orchestration.state.models import (
    Attempt,
    EffectLedgerEntry,
    EventDelivery,
    Execution,
    ExecutionLease,
    OutboxEvent,
)
from dark_factory.orchestration.state.models import (
    Stage as StageRow,
)


class StateError(RuntimeError):
    """Base error of the operational state store."""


class StateConflictError(StateError):
    """Optimistic-concurrency or missing-row conflict."""


class StaleFencingTokenError(StateError):
    """A writer presented an outdated fencing token (ADR-006 p.6)."""


class LeaseLostError(StateError):
    """A lease could not be acquired or renewed; another owner holds it."""


def _now() -> datetime:
    return datetime.now(UTC)


class ExecutionRepository:
    """Execution and stage/attempt rows with idempotent creation."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        *,
        execution_id: str,
        change_id: str,
        route: Route,
        provider: Provider,
    ) -> Execution:
        """Create an execution; a repeated call with the same id returns the existing row."""
        stmt = (
            pg_insert(Execution)
            .values(
                id=execution_id,
                change_id=change_id,
                route=route.value,
                provider=provider.value,
                status=RunStatus.PENDING.value,
                state_revision=1,
            )
            .on_conflict_do_nothing(index_elements=[Execution.id])
        )
        self._session.execute(stmt)
        execution = self._session.get(Execution, execution_id)
        if execution is None:  # pragma: no cover - defensive
            raise StateConflictError(f"execution {execution_id!r} was not created")
        return execution

    def get(self, execution_id: str) -> Execution | None:
        return self._session.get(Execution, execution_id)

    def get_or_create_stage(
        self,
        *,
        execution_id: str,
        stage: Stage,
        input_revision: str,
    ) -> StageRow:
        """Return the logical operation row for one stage + input revision (idempotent)."""
        key = compose_operation_key(execution_id, stage, input_revision)
        stmt = (
            pg_insert(StageRow)
            .values(
                id=key,
                execution_id=execution_id,
                stage=stage.value,
                status=StageStatus.PENDING.value,
                input_revision=input_revision,
                operation_key=key,
                state_revision=1,
                attempt_count=0,
            )
            .on_conflict_do_nothing(index_elements=[StageRow.operation_key])
        )
        self._session.execute(stmt)
        return self._session.execute(
            select(StageRow).where(StageRow.operation_key == key)
        ).scalar_one()

    def append_attempt(self, stage_row: StageRow, *, attempt_number: int) -> Attempt:
        """Append a physical attempt; repeated numbers are idempotent (``attempt_id`` PK)."""
        attempt_key = compose_attempt_id(stage_row.operation_key, attempt_number)
        stmt = (
            pg_insert(Attempt)
            .values(
                id=attempt_key,
                operation_key=stage_row.operation_key,
                stage_id=stage_row.id,
                attempt_number=attempt_number,
                status=StageStatus.IN_PROGRESS.value,
            )
            .on_conflict_do_nothing(index_elements=[Attempt.id])
        )
        self._session.execute(stmt)
        stage_row.attempt_count = max(stage_row.attempt_count, attempt_number)
        attempt = self._session.get(Attempt, attempt_key)
        if attempt is None:  # pragma: no cover - defensive
            raise StateConflictError(f"attempt {attempt_key!r} was not created")
        return attempt

    def update_status(
        self,
        execution_id: str,
        target: RunStatus,
        *,
        expected_revision: int,
        fencing_token: int,
    ) -> Execution:
        """Change status under both the optimistic revision and the lease fencing token."""
        execution = self._session.get(Execution, execution_id, with_for_update=True)
        if execution is None:
            raise StateConflictError(f"execution {execution_id!r} does not exist")
        lease = self._session.get(ExecutionLease, ("execution", execution_id), with_for_update=True)
        if lease is None or lease.fencing_token != fencing_token:
            raise StaleFencingTokenError(
                f"execution {execution_id!r} expects fencing_token "
                f"{None if lease is None else lease.fencing_token}, got {fencing_token}"
            )
        if execution.state_revision != expected_revision:
            raise StateConflictError(
                f"execution {execution_id!r} is at revision {execution.state_revision}, "
                f"expected {expected_revision}"
            )
        execution.status = target.value
        execution.state_revision += 1
        execution.updated_at = _now()
        if target in RUN_TERMINAL_STATUSES:
            execution.finished_at = execution.updated_at
        return execution


class LeaseRepository:
    """Leases with monotonic fencing tokens (ADR-006 p.6)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def acquire(
        self,
        *,
        resource_type: str,
        resource_id: str,
        owner_id: str,
        ttl: timedelta,
    ) -> int:
        """Acquire or steal an expired lease and return the new fencing token.

        The token increases monotonically on every (re)acquisition, so an older
        holder can never mutate state again. ``LeaseLostError`` means a live
        lease is held by another owner.
        """
        now = _now()
        expires = now + ttl
        table = ExecutionLease.__table__.c
        stmt = (
            pg_insert(ExecutionLease)
            .values(
                resource_type=resource_type,
                resource_id=resource_id,
                owner_id=owner_id,
                fencing_token=1,
                acquired_at=now,
                expires_at=expires,
                heartbeat_at=now,
            )
            .on_conflict_do_update(
                index_elements=[table.resource_type, table.resource_id],
                set_={
                    "owner_id": owner_id,
                    "fencing_token": table.fencing_token + 1,
                    "acquired_at": now,
                    "expires_at": expires,
                    "heartbeat_at": now,
                },
                where=(table.expires_at <= now) | (table.owner_id == owner_id),
            )
            .returning(table.fencing_token)
        )
        token = self._session.execute(stmt).scalar_one_or_none()
        if token is None:
            raise LeaseLostError(f"lease {resource_type}/{resource_id} is held by another owner")
        return cast("int", token)

    def renew(
        self,
        *,
        resource_type: str,
        resource_id: str,
        owner_id: str,
        fencing_token: int,
        ttl: timedelta,
    ) -> int:
        """Extend a lease held by ``owner_id``; a stale token is rejected."""
        now = _now()
        table = ExecutionLease.__table__.c
        stmt = (
            update(ExecutionLease)
            .where(
                table.resource_type == resource_type,
                table.resource_id == resource_id,
                table.owner_id == owner_id,
                table.fencing_token == fencing_token,
            )
            .values(expires_at=now + ttl, heartbeat_at=now)
            .returning(table.fencing_token)
        )
        token = self._session.execute(stmt).scalar_one_or_none()
        if token is None:
            raise LeaseLostError(
                f"lease {resource_type}/{resource_id} is not held by {owner_id!r} "
                f"with token {fencing_token}"
            )
        return cast("int", token)

    def release(
        self, *, resource_type: str, resource_id: str, owner_id: str, fencing_token: int
    ) -> bool:
        """Release a lease; only the current owner with the current token may release."""
        table = ExecutionLease.__table__.c
        stmt = (
            delete(ExecutionLease)
            .where(
                table.resource_type == resource_type,
                table.resource_id == resource_id,
                table.owner_id == owner_id,
                table.fencing_token == fencing_token,
            )
            .returning(table.fencing_token)
        )
        return self._session.execute(stmt).scalar_one_or_none() is not None

    def get(self, resource_type: str, resource_id: str) -> ExecutionLease | None:
        return self._session.get(ExecutionLease, (resource_type, resource_id))


class EffectLedger:
    """Durable effect ledger giving effectively-once side effects (ADR-006 p.3)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def plan(self, effect_key: str) -> EffectLedgerEntry:
        """Create the ledger entry if absent and return it (idempotent)."""
        now = _now()
        stmt = (
            pg_insert(EffectLedgerEntry)
            .values(
                effect_key=effect_key,
                status=EffectStatus.PLANNED.value,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=[EffectLedgerEntry.effect_key])
        )
        self._session.execute(stmt)
        entry = self._session.get(EffectLedgerEntry, effect_key, with_for_update=True)
        if entry is None:  # pragma: no cover - defensive
            raise StateConflictError(f"effect {effect_key!r} was not planned")
        return entry

    def lookup(self, effect_key: str) -> EffectLedgerEntry | None:
        return self._session.get(EffectLedgerEntry, effect_key)

    def mark_in_progress(self, entry: EffectLedgerEntry) -> None:
        entry.status = EffectStatus.IN_PROGRESS.value
        entry.updated_at = _now()

    def mark_unknown(self, entry: EffectLedgerEntry, external_ref: str | None = None) -> None:
        entry.status = EffectStatus.UNKNOWN.value
        if external_ref is not None:
            entry.external_ref = external_ref
        entry.updated_at = _now()

    def succeed(self, entry: EffectLedgerEntry, external_ref: str) -> None:
        entry.status = EffectStatus.SUCCEEDED.value
        entry.external_ref = external_ref
        entry.updated_at = _now()


def ensure_effect(
    session: Session,
    effect_key: str,
    *,
    lookup_external: Callable[[str], str | None],
    call: Callable[[], str],
) -> str:
    """Perform an external effect at most once under crash/retry (ADR-006 p.3).

    ``lookup_external`` searches the external system by the deterministic marker
    (the effect key). It runs before ``call`` so that a crash between the external
    call and the commit does not create a second effect on retry.
    """
    ledger = EffectLedger(session)
    entry = ledger.plan(effect_key)
    if entry.status == EffectStatus.SUCCEEDED.value and entry.external_ref is not None:
        return entry.external_ref
    existing = lookup_external(effect_key)
    if existing is not None:
        ledger.succeed(entry, existing)
        return existing
    ledger.mark_in_progress(entry)
    external_ref = call()
    ledger.succeed(entry, external_ref)
    return external_ref


class OutboxRepository:
    """Transactional outbox writer (ADR-016 p.1/p.5)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def next_sequence(self, aggregate_id: str) -> int:
        """Next monotonic sequence within one aggregate (ordering, ADR-016 p.6)."""
        current = self._session.execute(
            select(func.max(OutboxEvent.sequence)).where(OutboxEvent.aggregate_id == aggregate_id)
        ).scalar_one_or_none()
        return int(current or 0) + 1

    def publish(
        self,
        *,
        event_id: str,
        event_type: str,
        change_id: str,
        run_id: str,
        aggregate_id: str,
        aggregate_version: int,
        stage: Stage | None = None,
        event_version: int = 1,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        artifact_refs: Sequence[dict[str, Any]] = (),
        payload: dict[str, Any] | None = None,
        consumers: Iterable[str] = (),
    ) -> OutboxEvent:
        """Append an event and its per-consumer deliveries inside the caller's transaction."""
        event = OutboxEvent(
            event_id=event_id,
            event_type=event_type,
            event_version=event_version,
            change_id=change_id,
            run_id=run_id,
            stage=stage.value if stage is not None else None,
            aggregate_id=aggregate_id,
            aggregate_version=aggregate_version,
            correlation_id=correlation_id,
            causation_id=causation_id,
            artifact_refs=list(artifact_refs),
            payload=dict(payload or {}),
            sequence=self.next_sequence(aggregate_id),
        )
        self._session.add(event)
        # Flush the event first: deliveries carry a foreign key to it, and being
        # explicit keeps the write order independent of unit-of-work sorting.
        self._session.flush()
        for consumer in consumers:
            self._session.add(
                EventDelivery(
                    event_id=event_id,
                    consumer_id=consumer,
                    status=DeliveryStatus.PENDING.value,
                )
            )
        self._session.flush()
        return event
