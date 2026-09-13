"""Contract tests for EventPublisherPort."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime

from dark_factory.ports import DomainEvent, EventPublisherPort, EventType


def _event(event_id: str = "evt-001") -> DomainEvent:
    return DomainEvent(
        event_id=event_id,
        event_type=EventType.RUN_STAGE_COMPLETED,
        occurred_at=datetime(2026, 9, 13, 12, 0, 0, tzinfo=UTC),
        change_id="chg-001",
        run_id="run-001",
        stage=None,
        aggregate_id="run-001",
        aggregate_version=7,
        correlation_id="corr-001",
        causation_id="cmd-001",
    )


def test_adapter_satisfies_protocol(event_publisher_port: EventPublisherPort) -> None:
    assert isinstance(event_publisher_port, EventPublisherPort)


def test_publish_records_the_event(
    event_publisher_port: EventPublisherPort,
    recorded_events: Callable[[], tuple[DomainEvent, ...]],
) -> None:
    event = _event()
    asyncio.run(event_publisher_port.publish(event))
    assert recorded_events() == (event,)


def test_publish_deduplicates_by_event_id(
    event_publisher_port: EventPublisherPort,
    recorded_events: Callable[[], tuple[DomainEvent, ...]],
) -> None:
    asyncio.run(event_publisher_port.publish(_event()))
    asyncio.run(event_publisher_port.publish(_event()))
    assert len(recorded_events()) == 1
    asyncio.run(event_publisher_port.publish(_event("evt-002")))
    assert len(recorded_events()) == 2
