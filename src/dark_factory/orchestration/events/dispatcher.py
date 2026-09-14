"""Outbox Dispatcher: one idempotent delivery pass (T028, ADR-016 p.2/p.4/p.5/p.6/p.9).

The dispatcher is the single owner of outbox delivery — nothing else writes
delivery outcomes (the CronJob in ``deploy/events/`` invokes it through
``factory outbox dispatch``). One :meth:`OutboxDispatcher.run_pass`:

1. reserves a batch of due deliveries with ``FOR UPDATE SKIP LOCKED`` and a
   short :data:`~dark_factory.orchestration.events.repository.LEASE_SECONDS`
   lease inside a short transaction, with the per-stream ordering gate
   (ADR-016 p.6): a candidate behind its stream's smallest undelivered
   sequence is deferred without any state change;
2. calls the consumer handler *outside* any transaction (ADR-016 p.4 — the
   external/internal work never runs inside a held transaction);
3. commits each outcome in its own short transaction: success →
   ``delivered`` (``attempts += 1``); failure → ``attempts += 1``,
   ``last_error`` (truncated), exponential backoff into ``next_attempt_at``,
   ``dead`` when :data:`~dark_factory.orchestration.events.rules.MAX_ATTEMPTS`
   is exhausted (ADR-016 p.5);
4. optionally runs the retention cleanup step (ADR-016 p.9) in its own
   transaction — delivery and cleanup never mix in one transaction.

Crash semantics (at-least-once, ADR-016 p.2): a crash after the handler call
but before the outcome commit leaves the delivery reserved-with-lease; the
lease expiry makes the next pass deliver it again. Exactly-once is not
promised — consumers deduplicate by ``event_id`` (and the effect ledger for
internal effects, ADR-006 p.3).
"""

from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy.orm import Session, sessionmaker

from dark_factory.orchestration.events.cleanup import cleanup_expired_events
from dark_factory.orchestration.events.handlers import HandlerRegistry
from dark_factory.orchestration.events.models import (
    DeliveryOutcome,
    DeliveryOutcomeKind,
    DispatchReport,
    sort_outcomes,
)
from dark_factory.orchestration.events.repository import (
    DeliveryRepository,
    ReservedDelivery,
)
from dark_factory.orchestration.events.rules import backoff_delay_seconds, is_dead
from dark_factory.orchestration.state.engine import session_scope
from dark_factory.orchestration.state.enums import DeliveryStatus

DISPATCH_BATCH_SIZE: Final[int] = 100
"""Upper bound of deliveries reserved per pass: a comfortable ceiling for the
2-minute CronJob cadence (ADR-016 p.4); backpressure comes from the next
pass, not from long-running single passes."""

MAX_ERROR_LENGTH: Final[int] = 1024
"""Width of the ``event_delivery.last_error`` column — errors are truncated to it."""


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _error_text(error: BaseException) -> str:
    """Single-line error text for the journal; class name when the message is empty."""
    text = " ".join(str(error).split())
    return text or type(error).__name__


class OutboxDispatcher:
    """One dispatch pass over the PostgreSQL outbox (ADR-016 p.4, in the spirit of ADR-006).

    ``handlers`` maps consumer ids to their delivery SPI; unknown consumers
    fall back to the no-op handler. ``batch_size`` bounds the reservation;
    ``now`` is injectable for deterministic tests (production passes use the
    wall clock).
    """

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        handlers: HandlerRegistry | None = None,
        batch_size: int = DISPATCH_BATCH_SIZE,
    ) -> None:
        self._session_factory = session_factory
        self._handlers = handlers if handlers is not None else HandlerRegistry()
        self._batch_size = batch_size

    async def run_pass(
        self, *, now: datetime | None = None, cleanup: bool = False
    ) -> DispatchReport:
        """Run one delivery pass (plus the optional cleanup step) and return its report."""
        moment = _now_utc() if now is None else now
        with session_scope(self._session_factory) as session:
            batch = DeliveryRepository(session).reserve_batch(limit=self._batch_size, now=moment)
        outcomes: list[DeliveryOutcome] = [
            DeliveryOutcome(
                event_id=item.event_id,
                consumer_id=item.consumer_id,
                outcome=DeliveryOutcomeKind.DEFERRED,
                attempts=item.attempts,
            )
            for item in batch.deferred
        ]
        for item in batch.reserved:
            outcomes.append(await self._deliver_one(item, moment))
        if cleanup:
            with session_scope(self._session_factory) as session:
                cleaned = cleanup_expired_events(session, now=moment)
            outcomes.extend(
                DeliveryOutcome(event_id=event_id, outcome=DeliveryOutcomeKind.CLEANED)
                for event_id in cleaned
            )
        return DispatchReport(reserved=len(batch.reserved), outcomes=sort_outcomes(outcomes))

    async def _deliver_one(self, item: ReservedDelivery, moment: datetime) -> DeliveryOutcome:
        """Call the handler outside any transaction, then commit the outcome.

        Delivery failures are expected events, not pass aborts: any handler
        or envelope error marks the delivery failed (backoff, eventually
        ``dead``) and the pass continues with the next reserved delivery.
        """
        try:
            event = item.to_domain_event()
            await self._handlers.handler_for(item.consumer_id).deliver(event, item.consumer_id)
        except Exception as error:  # a failed delivery must not abort the pass
            return self._fix_failure(item, error, moment)
        return self._fix_success(item)

    def _fix_success(self, item: ReservedDelivery) -> DeliveryOutcome:
        """Record a delivered outcome (attempts += 1) in its own short transaction."""
        attempts = item.attempts + 1
        kind = self._commit_outcome(
            item,
            status=DeliveryStatus.DELIVERED,
            attempts=attempts,
            next_attempt_at=None,
            last_error=None,
        )
        return DeliveryOutcome(
            event_id=item.event_id,
            consumer_id=item.consumer_id,
            outcome=kind,
            attempts=attempts,
        )

    def _fix_failure(
        self, item: ReservedDelivery, error: Exception, moment: datetime
    ) -> DeliveryOutcome:
        """Record a failed outcome with backoff, or ``dead`` on the last allowed attempt."""
        attempts = item.attempts + 1
        dead = is_dead(attempts)
        status = DeliveryStatus.DEAD if dead else DeliveryStatus.FAILED
        next_attempt_at = (
            None if dead else moment + timedelta(seconds=backoff_delay_seconds(attempts))
        )
        error_text = _error_text(error)[:MAX_ERROR_LENGTH]
        kind = self._commit_outcome(
            item,
            status=status,
            attempts=attempts,
            next_attempt_at=next_attempt_at,
            last_error=error_text,
        )
        return DeliveryOutcome(
            event_id=item.event_id,
            consumer_id=item.consumer_id,
            outcome=kind,
            attempts=attempts,
            next_attempt_at=next_attempt_at,
            error=error_text,
        )

    def _commit_outcome(
        self,
        item: ReservedDelivery,
        *,
        status: DeliveryStatus,
        attempts: int,
        next_attempt_at: datetime | None,
        last_error: str | None,
    ) -> DeliveryOutcomeKind:
        """Persist one outcome; a lost race with an operator action reads as deferred."""
        with session_scope(self._session_factory) as session:
            committed = DeliveryRepository(session).commit_outcome(
                event_id=item.event_id,
                consumer_id=item.consumer_id,
                status=status,
                attempts=attempts,
                next_attempt_at=next_attempt_at,
                last_error=last_error,
            )
        if not committed:
            return DeliveryOutcomeKind.DEFERRED
        return _OUTCOME_BY_STATUS[status]


_OUTCOME_BY_STATUS: Final[dict[DeliveryStatus, DeliveryOutcomeKind]] = {
    DeliveryStatus.DELIVERED: DeliveryOutcomeKind.DELIVERED,
    DeliveryStatus.FAILED: DeliveryOutcomeKind.FAILED,
    DeliveryStatus.DEAD: DeliveryOutcomeKind.DEAD,
}
