"""Delivery repository: reservation, outcome commit, replay and skip over ``event_delivery``
(T028, ADR-016 p.4/p.5/p.6).

Reservation mechanics (ADR-016 p.4): the dispatcher is the single delivery
owner; concurrent passes cannot take the same delivery because the candidate
query runs ``FOR UPDATE OF event_delivery SKIP LOCKED`` inside a *short*
transaction, and each reservation also extends ``next_attempt_at`` by
:data:`LEASE_SECONDS` — a lease that lets a crashed pass's row be retried at
most one lease period later instead of blocking the stream. The handler call
itself happens strictly outside any transaction (the dispatcher commits the
reservation first); only the outcome is written in its own short transaction.

Ordering (ADR-016 p.6): the ordering gate is evaluated inside the reservation
transaction, so a candidate whose stream still has an undelivered row with a
smaller sequence (a gap, ``dead`` included) is deferred with its row left
*literally untouched* — no lease, no status change, no side effect. Deferred
rows are re-examined on every pass; a blocked stream occupies candidate slots
until an operator replays or skips the gap (documented MVP limitation).

Outcome commit (ADR-016 p.5): the ``UPDATE ... WHERE status IN (pending,
failed)`` guard never overwrites a terminal or operator state (``dead``,
``waived``) that raced in between reservation and commit — such an outcome is
reported as not committed and the row is re-examined next pass.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from dark_factory.changes.enums import Stage
from dark_factory.changes.refs import ArtifactRef
from dark_factory.orchestration.events.rules import ordering_eligible
from dark_factory.orchestration.state.enums import DeliveryStatus
from dark_factory.orchestration.state.models import EventDelivery, OutboxEvent
from dark_factory.ports import DomainEvent, EventType

LEASE_SECONDS: Final[int] = 120
"""Reservation lease: longer than any handler + commit window of the CronJob
pass (``activeDeadlineSeconds`` 300 with one batch), shorter than two CronJob
intervals; a pass that dies after the handler call releases the delivery at
most one lease later (at-least-once, ADR-016 p.2)."""

_RESERVABLE_STATUSES: Final[tuple[DeliveryStatus, ...]] = (
    DeliveryStatus.PENDING,
    DeliveryStatus.FAILED,
)
"""Statuses a dispatcher may reserve: everything undelivered except ``dead``
(replay/skip only, ADR-016 p.5) and terminal accepted ones."""


@dataclass(frozen=True, slots=True)
class ReservedDelivery:
    """One reserved delivery with the event snapshot needed to call the handler.

    ``to_domain_event`` reassembles the :class:`DomainEvent` envelope from the
    row snapshot; a malformed envelope raises (pydantic/enum) and the
    dispatcher treats it as a delivery failure — visibly dead-lettered, never
    silently dropped.
    """

    event_id: str
    consumer_id: str
    attempts: int
    sequence: int
    event_type: str
    event_version: int
    occurred_at: datetime
    change_id: str
    run_id: str
    stage: str | None
    aggregate_id: str
    aggregate_version: int
    correlation_id: str | None
    causation_id: str | None
    artifact_refs: tuple[dict[str, Any], ...]
    payload: dict[str, Any]

    @classmethod
    def from_rows(cls, event: OutboxEvent, delivery: EventDelivery) -> "ReservedDelivery":
        """Snapshot one ``(outbox, event_delivery)`` row pair."""
        return cls(
            event_id=event.event_id,
            consumer_id=delivery.consumer_id,
            attempts=delivery.attempts,
            sequence=event.sequence,
            event_type=event.event_type,
            event_version=event.event_version,
            occurred_at=event.occurred_at,
            change_id=event.change_id,
            run_id=event.run_id,
            stage=event.stage,
            aggregate_id=event.aggregate_id,
            aggregate_version=event.aggregate_version,
            correlation_id=event.correlation_id,
            causation_id=event.causation_id,
            artifact_refs=tuple(event.artifact_refs),
            payload=dict(event.payload),
        )

    def to_domain_event(self) -> DomainEvent:
        """Rebuild the frozen event envelope for the handler call."""
        return DomainEvent(
            event_id=self.event_id,
            event_type=EventType(self.event_type),
            event_version=self.event_version,
            occurred_at=self.occurred_at,
            change_id=self.change_id,
            run_id=self.run_id,
            stage=Stage(self.stage) if self.stage is not None else None,
            aggregate_id=self.aggregate_id,
            aggregate_version=self.aggregate_version,
            correlation_id=self.correlation_id,
            causation_id=self.causation_id,
            artifact_refs=[ArtifactRef.model_validate(ref) for ref in self.artifact_refs],
            payload=dict(self.payload),
        )


@dataclass(frozen=True, slots=True)
class DeferredDelivery:
    """One candidate deferred by the ordering gate; its row was not touched."""

    event_id: str
    consumer_id: str
    attempts: int


@dataclass(frozen=True, slots=True)
class ReservationBatch:
    """Result of one reservation step: taken deliveries plus ordering-deferred ones."""

    reserved: tuple[ReservedDelivery, ...]
    deferred: tuple[DeferredDelivery, ...]


class DeliveryRepository:
    """Queries and mutations of per-consumer delivery state (ADR-016 p.5)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def min_undelivered_sequence(self, *, consumer_id: str, aggregate_id: str) -> int | None:
        """Smallest undelivered sequence of one consumer within one stream (ADR-016 p.6)."""
        value = self._session.execute(
            select(func.min(OutboxEvent.sequence))
            .join(EventDelivery, EventDelivery.event_id == OutboxEvent.event_id)
            .where(
                EventDelivery.consumer_id == consumer_id,
                OutboxEvent.aggregate_id == aggregate_id,
                EventDelivery.status.notin_(
                    [DeliveryStatus.DELIVERED.value, DeliveryStatus.WAIVED.value]
                ),
            )
        ).scalar_one_or_none()
        return int(value) if value is not None else None

    def reserve_batch(self, *, limit: int, now: datetime) -> ReservationBatch:
        """Reserve up to ``limit`` due deliveries with ``SKIP LOCKED`` and the ordering gate.

        Candidates are due deliveries in a reservable status; each one is
        taken only when its stream has no undelivered row with a smaller
        sequence. Reserved rows get a lease through ``next_attempt_at``;
        deferred rows are reported but never modified. The whole step runs in
        the caller's short transaction (the dispatcher commits before calling
        handlers, ADR-016 p.4).
        """
        rows = self._session.execute(
            select(OutboxEvent, EventDelivery)
            .join(EventDelivery, EventDelivery.event_id == OutboxEvent.event_id)
            .where(
                EventDelivery.status.in_([status.value for status in _RESERVABLE_STATUSES]),
                or_(
                    EventDelivery.next_attempt_at.is_(None),
                    EventDelivery.next_attempt_at <= now,
                ),
            )
            .order_by(
                OutboxEvent.occurred_at,
                OutboxEvent.sequence,
                OutboxEvent.event_id,
                EventDelivery.consumer_id,
            )
            .limit(limit)
            .with_for_update(of=EventDelivery, skip_locked=True)
        ).all()
        reserved: list[ReservedDelivery] = []
        deferred: list[DeferredDelivery] = []
        for event, delivery in rows:
            minimum = self.min_undelivered_sequence(
                consumer_id=delivery.consumer_id, aggregate_id=event.aggregate_id
            )
            if not ordering_eligible(
                candidate_sequence=event.sequence, min_undelivered_sequence=minimum
            ):
                deferred.append(
                    DeferredDelivery(
                        event_id=delivery.event_id,
                        consumer_id=delivery.consumer_id,
                        attempts=delivery.attempts,
                    )
                )
                continue
            delivery.next_attempt_at = now + timedelta(seconds=LEASE_SECONDS)
            reserved.append(ReservedDelivery.from_rows(event, delivery))
        return ReservationBatch(reserved=tuple(reserved), deferred=tuple(deferred))

    def commit_outcome(
        self,
        *,
        event_id: str,
        consumer_id: str,
        status: DeliveryStatus,
        attempts: int,
        next_attempt_at: datetime | None,
        last_error: str | None,
    ) -> bool:
        """Record one delivery outcome; ``False`` when the row is no longer reservable.

        The guard keeps a race with an operator action (replay/skip) from
        overwriting a terminal or administrative state (ADR-016 p.6).
        """
        stmt = (
            update(EventDelivery)
            .where(
                EventDelivery.event_id == event_id,
                EventDelivery.consumer_id == consumer_id,
                EventDelivery.status.in_([status.value for status in _RESERVABLE_STATUSES]),
            )
            .values(
                status=status.value,
                attempts=attempts,
                next_attempt_at=next_attempt_at,
                last_error=last_error,
            )
            .returning(EventDelivery.consumer_id)
        )
        return self._session.execute(stmt).scalar_one_or_none() is not None

    def replay(self, *, event_id: str, consumer_id: str | None = None) -> list[tuple[str, str]]:
        """Reset ``dead``/``failed`` deliveries to ``pending`` (manual replay, ADR-016 p.5).

        Attempts, backoff and the last error are cleared so the delivery
        starts a fresh attempt budget. Returns the
        ``(consumer_id, previous_status)`` pairs actually replayed (sorted);
        an empty list means nothing matched.
        """
        conditions: list[Any] = [
            EventDelivery.event_id == event_id,
            EventDelivery.status.in_([DeliveryStatus.DEAD.value, DeliveryStatus.FAILED.value]),
        ]
        if consumer_id is not None:
            conditions.append(EventDelivery.consumer_id == consumer_id)
        rows = self._session.execute(
            select(EventDelivery.consumer_id, EventDelivery.status).where(*conditions)
        ).all()
        if not rows:
            return []
        self._session.execute(
            update(EventDelivery)
            .where(*conditions)
            .values(
                status=DeliveryStatus.PENDING.value,
                attempts=0,
                next_attempt_at=None,
                last_error=None,
            )
        )
        return sorted((consumer_id_, status) for consumer_id_, status in rows)

    def skip(self, *, event_id: str, consumer_id: str) -> str | None:
        """Administratively waive one delivery (ADR-016 p.6: explicit operator skip).

        Unblocks a stream whose head delivery is stuck (``dead`` included) by
        recording the operator decision as ``waived`` — a terminal accepted
        status for ordering and cleanup. Returns the previous status, or
        ``None`` when no pending/failed/dead delivery matches.
        """
        previous = self._session.execute(
            select(EventDelivery.status)
            .where(
                EventDelivery.event_id == event_id,
                EventDelivery.consumer_id == consumer_id,
                EventDelivery.status.in_(
                    [
                        DeliveryStatus.PENDING.value,
                        DeliveryStatus.FAILED.value,
                        DeliveryStatus.DEAD.value,
                    ]
                ),
            )
            .with_for_update()
        ).scalar_one_or_none()
        if previous is None:
            return None
        self._session.execute(
            update(EventDelivery)
            .where(
                EventDelivery.event_id == event_id,
                EventDelivery.consumer_id == consumer_id,
            )
            .values(status=DeliveryStatus.WAIVED.value)
        )
        return str(previous)
