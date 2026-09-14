"""Retention cleanup of the outbox (T028, ADR-016 p.9).

Executing form of the pure :func:`~dark_factory.orchestration.events.rules.is_cleanup_eligible`
rule: an event is deleted only when every one of its deliveries is
``delivered`` or operator-``waived`` (``dead`` blocks until replay or a
recorded operator decision) *and* the retention period passed. Deletion is a
single ``DELETE ... WHERE event_id IN (...)`` — the FK cascade removes the
delivery rows with the event. Archival (ADR-016 p.9 allows archiving instead
of deletion) is not implemented and stays out of the MVP.
"""

from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from dark_factory.orchestration.events.rules import RETENTION_DAYS, is_cleanup_eligible
from dark_factory.orchestration.state.enums import DeliveryStatus
from dark_factory.orchestration.state.models import EventDelivery, OutboxEvent


def cleanup_expired_events(
    session: Session, *, now: datetime, retention_days: int = RETENTION_DAYS
) -> list[str]:
    """Delete outbox events whose deliveries are all terminal-accepted and past retention.

    Returns the sorted ids of the deleted events (an empty list when nothing
    was eligible). Events are examined only up to the retention cutoff —
    younger events cannot be eligible, so the candidate set shrinks to old
    rows before any lock is taken.
    """
    cutoff = now - timedelta(days=retention_days)
    rows = session.execute(
        select(OutboxEvent.event_id, OutboxEvent.occurred_at, EventDelivery.status)
        .outerjoin(EventDelivery, EventDelivery.event_id == OutboxEvent.event_id)
        .where(OutboxEvent.occurred_at <= cutoff)
    ).all()
    facts: dict[str, tuple[datetime, list[DeliveryStatus]]] = {}
    for event_id, occurred_at, status in rows:
        _, statuses = facts.setdefault(event_id, (occurred_at, []))
        if status is not None:
            statuses.append(DeliveryStatus(status))
    eligible = [
        event_id
        for event_id, (occurred_at, statuses) in facts.items()
        if is_cleanup_eligible(
            delivery_statuses=statuses,
            occurred_at=occurred_at,
            now=now,
            retention_days=retention_days,
        )
    ]
    if not eligible:
        return []
    deleted = (
        session.execute(
            delete(OutboxEvent)
            .where(OutboxEvent.event_id.in_(eligible))
            .returning(OutboxEvent.event_id)
        )
        .scalars()
        .all()
    )
    return sorted(deleted)
