"""Integration tests of the Outbox Dispatcher against PostgreSQL (T028, ADR-016).

Requires ``DARK_FACTORY_TEST_DATABASE_URL`` (the shared fixtures build the
schema through the real Alembic migration); skipped without it. Every pass
runs against a truncated schema; events are seeded through the real
``OutboxRepository.publish`` (T-006), deliveries are inspected and, where a
test needs a specific status, adjusted directly.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from dark_factory.cli.main import (
    EXIT_ERROR,
    EXIT_OK,
    OutboxDispatchArgs,
    OutboxReplayArgs,
    OutboxSkipArgs,
)
from dark_factory.cli.outbox import run_dispatch_command, run_replay_command, run_skip_command
from dark_factory.orchestration.events.dispatcher import OutboxDispatcher
from dark_factory.orchestration.events.handlers import HandlerRegistry
from dark_factory.orchestration.events.models import DeliveryOutcomeKind
from dark_factory.orchestration.events.repository import LEASE_SECONDS, DeliveryRepository
from dark_factory.orchestration.events.rules import MAX_ATTEMPTS
from dark_factory.orchestration.state.engine import session_scope
from dark_factory.orchestration.state.enums import DeliveryStatus
from dark_factory.orchestration.state.models import EventDelivery, OutboxEvent
from dark_factory.orchestration.state.repositories import OutboxRepository
from dark_factory.ports import DomainEvent

T0 = datetime(2026, 9, 14, 12, 0, 0, tzinfo=UTC)
LEASE_PLUS_ONE = timedelta(seconds=LEASE_SECONDS + 1)


class _RecordingHandler:
    """Handler that records calls, deduplicates effects by event id and can fail."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.effects: set[str] = set()
        self.failing: set[str] = set()

    async def deliver(self, event: DomainEvent, consumer_id: str, /) -> None:
        self.calls.append((event.event_id, consumer_id))
        if event.event_id in self.failing:
            raise RuntimeError("boom")
        self.effects.add(event.event_id)


def _publish(
    factory: sessionmaker[Session],
    event_id: str,
    *,
    aggregate_id: str = "run-1",
    consumers: tuple[str, ...] = ("tracker",),
    occurred_at: datetime | None = None,
) -> None:
    """Seed one outbox event with its per-consumer deliveries (sequences auto-increment)."""
    with session_scope(factory) as session:
        event = OutboxRepository(session).publish(
            event_id=event_id,
            event_type="run.started",
            change_id="chg-1",
            run_id="run-1",
            aggregate_id=aggregate_id,
            aggregate_version=1,
            consumers=consumers,
        )
        if occurred_at is not None:
            event.occurred_at = occurred_at


def _delivery(factory: sessionmaker[Session], event_id: str, consumer_id: str) -> EventDelivery:
    with session_scope(factory) as session:
        row = session.get(EventDelivery, (event_id, consumer_id))
        assert row is not None
        return row


def _set_delivery_status(
    factory: sessionmaker[Session], event_id: str, consumer_id: str, status: DeliveryStatus
) -> None:
    with session_scope(factory) as session:
        row = session.get(EventDelivery, (event_id, consumer_id))
        assert row is not None
        row.status = status.value


def _dispatcher(
    factory: sessionmaker[Session], handler: _RecordingHandler, **kwargs: Any
) -> OutboxDispatcher:
    registry = HandlerRegistry()
    registry.register("tracker", handler)
    registry.register("console", handler)
    return OutboxDispatcher(factory, handlers=registry, **kwargs)


def test_happy_path_delivers_to_all_consumers_once(session_factory: sessionmaker[Session]) -> None:
    _publish(session_factory, "evt-1", consumers=("tracker", "console"))
    handler = _RecordingHandler()

    report = asyncio.run(_dispatcher(session_factory, handler).run_pass(now=T0))

    assert report.reserved == 2
    assert [(o.event_id, o.consumer_id, o.outcome) for o in report.outcomes] == [
        ("evt-1", "console", DeliveryOutcomeKind.DELIVERED),
        ("evt-1", "tracker", DeliveryOutcomeKind.DELIVERED),
    ]
    assert all(o.attempts == 1 for o in report.outcomes)
    assert handler.effects == {"evt-1"}
    row = _delivery(session_factory, "evt-1", "tracker")
    assert (row.status, row.attempts, row.next_attempt_at, row.last_error) == (
        DeliveryStatus.DELIVERED.value,
        1,
        None,
        None,
    )

    # A repeated pass on unchanged data does nothing (idempotence).
    second = asyncio.run(
        _dispatcher(session_factory, handler).run_pass(now=T0 + timedelta(seconds=1))
    )
    assert second.reserved == 0
    assert second.outcomes == ()
    assert handler.calls == [("evt-1", "console"), ("evt-1", "tracker")]


def test_crash_between_handler_and_commit_redelivers_at_least_once(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    _publish(session_factory, "evt-1", consumers=("tracker",))
    handler = _RecordingHandler()
    dispatcher = _dispatcher(session_factory, handler)

    def _crash(self: OutboxDispatcher, item: Any) -> Any:
        raise RuntimeError("simulated crash before outcome commit")

    with monkeypatch.context() as scoped:
        scoped.setattr(OutboxDispatcher, "_fix_success", _crash)
        with pytest.raises(RuntimeError, match="simulated crash"):
            asyncio.run(dispatcher.run_pass(now=T0))
    # The handler ran (the effect happened), but the outcome was never committed.
    assert handler.calls == [("evt-1", "tracker")]
    assert handler.effects == {"evt-1"}

    # The lease expired: the next pass redelivers the same eventId to the same consumer.
    report = asyncio.run(dispatcher.run_pass(now=T0 + LEASE_PLUS_ONE))
    assert report.reserved == 1
    assert handler.calls == [("evt-1", "tracker"), ("evt-1", "tracker")]
    # Consumer-side deduplication by event_id: the repeat did not create a second effect.
    assert handler.effects == {"evt-1"}
    assert report.outcomes[0].outcome is DeliveryOutcomeKind.DELIVERED
    row = _delivery(session_factory, "evt-1", "tracker")
    assert (row.status, row.attempts, row.next_attempt_at) == (
        DeliveryStatus.DELIVERED.value,
        1,
        None,
    )


def test_two_overlapping_passes_never_reserve_the_same_delivery(
    session_factory: sessionmaker[Session],
) -> None:
    _publish(session_factory, "evt-1", consumers=("tracker",))
    handler = _RecordingHandler()
    dispatcher = _dispatcher(session_factory, handler)

    with session_scope(session_factory) as session:
        batch = DeliveryRepository(session).reserve_batch(limit=10, now=T0)
        assert len(batch.reserved) == 1
        # A concurrent pass cannot see the row-locked delivery (SKIP LOCKED).
        concurrent = asyncio.run(dispatcher.run_pass(now=T0))
        assert concurrent.reserved == 0
        assert concurrent.outcomes == ()
        assert handler.calls == []
    # The committed reservation holds a lease: the row is not due before it expires.
    during_lease = asyncio.run(dispatcher.run_pass(now=T0))
    assert during_lease.reserved == 0
    after_lease = asyncio.run(dispatcher.run_pass(now=T0 + LEASE_PLUS_ONE))
    assert after_lease.reserved == 1
    assert handler.calls == [("evt-1", "tracker")]


def test_failure_schedules_exponential_backoff_and_skips_undue_rows(
    session_factory: sessionmaker[Session],
) -> None:
    _publish(session_factory, "evt-1", consumers=("tracker",))
    handler = _RecordingHandler()
    handler.failing = {"evt-1"}

    report = asyncio.run(_dispatcher(session_factory, handler).run_pass(now=T0))

    entry = report.outcomes[0]
    assert entry.outcome is DeliveryOutcomeKind.FAILED
    assert entry.attempts == 1
    assert entry.next_attempt_at == T0 + timedelta(seconds=30)
    assert entry.error == "boom"
    row = _delivery(session_factory, "evt-1", "tracker")
    assert (row.status, row.attempts, row.last_error, row.next_attempt_at) == (
        DeliveryStatus.FAILED.value,
        1,
        "boom",
        T0 + timedelta(seconds=30),
    )

    # A pass before next_attempt_at takes nothing.
    undue = asyncio.run(
        _dispatcher(session_factory, _RecordingHandler()).run_pass(now=T0 + timedelta(seconds=10))
    )
    assert undue.reserved == 0
    assert undue.outcomes == ()


def test_attempts_exhaustion_marks_the_delivery_dead(
    session_factory: sessionmaker[Session],
) -> None:
    _publish(session_factory, "evt-1", consumers=("tracker",))
    handler = _RecordingHandler()
    handler.failing = {"evt-1"}
    dispatcher = _dispatcher(session_factory, handler)

    now = T0
    report = asyncio.run(dispatcher.run_pass(now=now))
    assert report.outcomes[0].next_attempt_at == T0 + timedelta(seconds=30)
    for attempt in range(2, MAX_ATTEMPTS + 1):
        entry = report.outcomes[0]
        assert entry.outcome is DeliveryOutcomeKind.FAILED
        assert entry.attempts == attempt - 1
        next_attempt_at = entry.next_attempt_at
        assert next_attempt_at is not None
        now = next_attempt_at + timedelta(seconds=1)
        report = asyncio.run(dispatcher.run_pass(now=now))

    last = report.outcomes[0]
    assert last.outcome is DeliveryOutcomeKind.DEAD
    assert last.attempts == MAX_ATTEMPTS
    assert last.next_attempt_at is None
    row = _delivery(session_factory, "evt-1", "tracker")
    assert row.status == DeliveryStatus.DEAD.value
    # Dead deliveries are not reservable any more.
    assert asyncio.run(dispatcher.run_pass(now=now)).reserved == 0
    assert len(handler.calls) == MAX_ATTEMPTS


def test_manual_replay_resets_a_dead_delivery(session_factory: sessionmaker[Session]) -> None:
    _publish(session_factory, "evt-1", consumers=("tracker",))
    handler = _RecordingHandler()
    handler.failing = {"evt-1"}
    dispatcher = _dispatcher(session_factory, handler)

    now = T0
    for _ in range(MAX_ATTEMPTS):
        report = asyncio.run(dispatcher.run_pass(now=now))
        next_attempt_at = report.outcomes[0].next_attempt_at
        now = (next_attempt_at or now) + timedelta(seconds=1)
    assert _delivery(session_factory, "evt-1", "tracker").status == DeliveryStatus.DEAD.value

    code = run_replay_command(
        OutboxReplayArgs(event_id="evt-1", consumer=None, json_output=False),
        session_factory=session_factory,
    )
    assert code == EXIT_OK
    row = _delivery(session_factory, "evt-1", "tracker")
    assert (row.status, row.attempts, row.next_attempt_at, row.last_error) == (
        DeliveryStatus.PENDING.value,
        0,
        None,
        None,
    )

    # The replayed delivery is picked up again and succeeds.
    handler.failing = set()
    final = asyncio.run(dispatcher.run_pass(now=now))
    assert final.outcomes[0].outcome is DeliveryOutcomeKind.DELIVERED
    assert final.outcomes[0].attempts == 1


def test_replay_without_a_matching_delivery_fails(
    session_factory: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    _publish(session_factory, "evt-1", consumers=("tracker",))

    unknown_event = run_replay_command(
        OutboxReplayArgs(event_id="evt-missing", consumer=None, json_output=False),
        session_factory=session_factory,
    )
    unknown_consumer = run_replay_command(
        OutboxReplayArgs(event_id="evt-1", consumer="console", json_output=False),
        session_factory=session_factory,
    )

    assert unknown_event == EXIT_ERROR
    assert unknown_consumer == EXIT_ERROR
    captured = capsys.readouterr()
    assert "no dead or failed delivery matches" in captured.err
    assert captured.out == ""


def test_ordering_gap_blocks_the_stream_until_the_head_is_delivered(
    session_factory: sessionmaker[Session],
) -> None:
    _publish(session_factory, "evt-1", consumers=("tracker",))
    _publish(session_factory, "evt-2", consumers=("tracker",))
    handler = _RecordingHandler()
    handler.failing = {"evt-1"}
    dispatcher = _dispatcher(session_factory, handler)

    # Pass 1: evt-1 fails; evt-2 (seq=2) is deferred behind the gap.
    first = asyncio.run(dispatcher.run_pass(now=T0))
    assert [(o.event_id, o.outcome) for o in first.outcomes] == [
        ("evt-1", DeliveryOutcomeKind.FAILED),
        ("evt-2", DeliveryOutcomeKind.DEFERRED),
    ]
    assert handler.calls == [("evt-1", "tracker")]

    # Pass 2: evt-1 fails again; evt-2 still deferred.
    second = asyncio.run(dispatcher.run_pass(now=T0 + timedelta(seconds=31)))
    assert [(o.event_id, o.outcome) for o in second.outcomes] == [
        ("evt-1", DeliveryOutcomeKind.FAILED),
        ("evt-2", DeliveryOutcomeKind.DEFERRED),
    ]
    assert handler.calls == [("evt-1", "tracker"), ("evt-1", "tracker")]

    # Pass 3: evt-1 is delivered; evt-2 is still deferred within the same pass
    # (the reservation gate runs before any outcome of this pass is committed).
    handler.failing = set()
    third = asyncio.run(dispatcher.run_pass(now=T0 + timedelta(seconds=152)))
    assert [(o.event_id, o.outcome) for o in third.outcomes] == [
        ("evt-1", DeliveryOutcomeKind.DELIVERED),
        ("evt-2", DeliveryOutcomeKind.DEFERRED),
    ]
    assert handler.calls[-1] == ("evt-1", "tracker")

    # Pass 4: with the head delivered, seq=2 is unlocked and delivered.
    fourth = asyncio.run(dispatcher.run_pass(now=T0 + timedelta(seconds=153)))
    assert [(o.event_id, o.outcome) for o in fourth.outcomes] == [
        ("evt-2", DeliveryOutcomeKind.DELIVERED)
    ]
    # evt-2 was never delivered while evt-1 was undelivered.
    assert handler.calls == [
        ("evt-1", "tracker"),
        ("evt-1", "tracker"),
        ("evt-1", "tracker"),
        ("evt-2", "tracker"),
    ]
    assert handler.effects == {"evt-1", "evt-2"}
    for event_id in ("evt-1", "evt-2"):
        assert _delivery(session_factory, event_id, "tracker").status == (
            DeliveryStatus.DELIVERED.value
        )


def test_operator_skip_waives_the_blocked_head_and_unlocks_the_stream(
    session_factory: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    _publish(session_factory, "evt-1", consumers=("tracker",))
    _publish(session_factory, "evt-2", consumers=("tracker",))
    _set_delivery_status(session_factory, "evt-1", "tracker", DeliveryStatus.DEAD)
    handler = _RecordingHandler()
    dispatcher = _dispatcher(session_factory, handler)

    # dead is not reservable, but it blocks the stream: evt-2 is deferred.
    blocked = asyncio.run(dispatcher.run_pass(now=T0))
    assert blocked.reserved == 0
    assert [(o.event_id, o.outcome) for o in blocked.outcomes] == [
        ("evt-2", DeliveryOutcomeKind.DEFERRED)
    ]

    code = run_skip_command(
        OutboxSkipArgs(event_id="evt-1", consumer="tracker", json_output=False),
        session_factory=session_factory,
    )
    assert code == EXIT_OK
    assert "dead -> waived" in capsys.readouterr().out
    assert _delivery(session_factory, "evt-1", "tracker").status == DeliveryStatus.WAIVED.value

    # The waiver unblocks the stream: seq=2 is now deliverable.
    unblocked = asyncio.run(dispatcher.run_pass(now=T0 + timedelta(seconds=1)))
    assert [(o.event_id, o.outcome) for o in unblocked.outcomes] == [
        ("evt-2", DeliveryOutcomeKind.DELIVERED)
    ]

    # Skipping again (already waived) and skipping an unknown pair both fail.
    assert (
        run_skip_command(
            OutboxSkipArgs(event_id="evt-1", consumer="tracker", json_output=False),
            session_factory=session_factory,
        )
        == EXIT_ERROR
    )
    assert (
        run_skip_command(
            OutboxSkipArgs(event_id="evt-missing", consumer="tracker", json_output=False),
            session_factory=session_factory,
        )
        == EXIT_ERROR
    )


def test_cleanup_removes_only_past_retention_events_with_terminal_deliveries(
    session_factory: sessionmaker[Session],
) -> None:
    old = T0 - timedelta(days=40)
    recent = T0 - timedelta(days=1)
    _publish(
        session_factory, "evt-a", aggregate_id="agg-a", consumers=("tracker",), occurred_at=old
    )
    _set_delivery_status(session_factory, "evt-a", "tracker", DeliveryStatus.DELIVERED)
    _publish(
        session_factory, "evt-b", aggregate_id="agg-b", consumers=("tracker",), occurred_at=recent
    )
    _set_delivery_status(session_factory, "evt-b", "tracker", DeliveryStatus.DELIVERED)
    _publish(
        session_factory, "evt-c", aggregate_id="agg-c", consumers=("tracker",), occurred_at=old
    )
    _set_delivery_status(session_factory, "evt-c", "tracker", DeliveryStatus.DEAD)
    _publish(
        session_factory,
        "evt-d",
        aggregate_id="agg-d",
        consumers=("tracker", "console"),
        occurred_at=old,
    )
    _set_delivery_status(session_factory, "evt-d", "tracker", DeliveryStatus.DELIVERED)
    _publish(
        session_factory, "evt-e", aggregate_id="agg-e", consumers=("tracker",), occurred_at=old
    )
    _set_delivery_status(session_factory, "evt-e", "tracker", DeliveryStatus.WAIVED)
    handler = _RecordingHandler()
    # evt-d/console fails in the delivery pass and stays non-terminal (failed):
    # one non-terminal delivery must block the deletion of the whole event.
    handler.failing = {"evt-d"}

    report = asyncio.run(_dispatcher(session_factory, handler).run_pass(now=T0, cleanup=True))
    cleaned = {o.event_id for o in report.outcomes if o.outcome is DeliveryOutcomeKind.CLEANED}
    assert cleaned == {"evt-a", "evt-e"}

    with session_scope(session_factory) as session:
        assert session.get(OutboxEvent, "evt-a") is None
        assert session.get(OutboxEvent, "evt-b") is not None  # retention not passed
        assert session.get(OutboxEvent, "evt-c") is not None  # dead blocks deletion
        assert session.get(OutboxEvent, "evt-d") is not None  # console delivery failed
        assert session.get(OutboxEvent, "evt-e") is None
        assert session.get(EventDelivery, ("evt-d", "console")) is not None
        # The FK cascade removed the deliveries of the cleaned events.
        assert session.get(EventDelivery, ("evt-a", "tracker")) is None
        assert session.get(EventDelivery, ("evt-e", "tracker")) is None


def test_cleanup_is_opt_in_and_off_by_default(session_factory: sessionmaker[Session]) -> None:
    _publish(
        session_factory,
        "evt-a",
        aggregate_id="agg-a",
        consumers=("tracker",),
        occurred_at=T0 - timedelta(days=40),
    )
    _set_delivery_status(session_factory, "evt-a", "tracker", DeliveryStatus.DELIVERED)

    report = asyncio.run(_dispatcher(session_factory, _RecordingHandler()).run_pass(now=T0))

    assert report.outcomes == ()
    with session_scope(session_factory) as session:
        assert session.get(OutboxEvent, "evt-a") is not None


def test_cli_dispatch_command_emits_the_json_report(
    session_factory: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    _publish(session_factory, "evt-1", consumers=("tracker",))

    code = run_dispatch_command(
        OutboxDispatchArgs(once=True, json_output=True, limit=None, cleanup=False),
        session_factory=session_factory,
    )

    assert code == EXIT_OK
    payload: dict[str, Any] = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == 1
    assert payload["reserved"] == 1
    assert payload["outcomes"][0]["outcome"] == "delivered"
    assert payload["outcomes"][0]["consumer_id"] == "tracker"
