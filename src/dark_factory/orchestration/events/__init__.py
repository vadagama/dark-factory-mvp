"""Outbox event delivery (T028, ADR-016 p.2/p.4/p.5/p.6/p.9).

The dispatcher is the single owner of outbox delivery: it reserves due
deliveries with ``FOR UPDATE SKIP LOCKED`` plus a short lease, applies the
per-stream (``aggregate_id``, ADR-016 p.6) ordering gate, calls consumer
handlers outside any transaction, commits outcomes in ``event_delivery`` with
exponential backoff and ``dead`` on exhaustion, and — as an explicit step —
deletes past-retention events whose deliveries are all terminal-accepted.
Delivery is at-least-once: consumers deduplicate by ``event_id``.
"""

from dark_factory.orchestration.events.cleanup import cleanup_expired_events
from dark_factory.orchestration.events.dispatcher import (
    DISPATCH_BATCH_SIZE,
    MAX_ERROR_LENGTH,
    OutboxDispatcher,
)
from dark_factory.orchestration.events.handlers import (
    DeliveryHandler,
    HandlerRegistry,
    NoOpHandler,
)
from dark_factory.orchestration.events.models import (
    DeliveryOutcome,
    DeliveryOutcomeKind,
    DispatchReport,
    sort_outcomes,
)
from dark_factory.orchestration.events.repository import (
    LEASE_SECONDS,
    DeferredDelivery,
    DeliveryRepository,
    ReservationBatch,
    ReservedDelivery,
)

__all__ = [
    "DISPATCH_BATCH_SIZE",
    "LEASE_SECONDS",
    "MAX_ERROR_LENGTH",
    "DeferredDelivery",
    "DeliveryHandler",
    "DeliveryOutcome",
    "DeliveryOutcomeKind",
    "DeliveryRepository",
    "DispatchReport",
    "HandlerRegistry",
    "NoOpHandler",
    "OutboxDispatcher",
    "ReservationBatch",
    "ReservedDelivery",
    "cleanup_expired_events",
    "sort_outcomes",
]
