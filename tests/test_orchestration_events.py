"""Unit tests of the Outbox Dispatcher pure rules, models and handlers (T028, ADR-016).

Pure decisions only: no database, no clock — ``now`` is injected and identical
facts yield identical decisions. The PostgreSQL-backed behaviour of a full
pass lives in ``tests/integration/test_events_dispatcher.py``.
"""

import asyncio
import random
from datetime import UTC, datetime, timedelta

import pytest

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
from dark_factory.orchestration.events.rules import (
    BACKOFF_BASE_SECONDS,
    BACKOFF_CAP_SECONDS,
    MAX_ATTEMPTS,
    RETENTION_DAYS,
    backoff_delay_seconds,
    is_cleanup_eligible,
    is_dead,
    is_undelivered,
    min_undelivered_sequence,
    ordering_eligible,
)
from dark_factory.orchestration.state.enums import DeliveryStatus
from dark_factory.ports import DomainEvent, EventType

NOW = datetime(2026, 9, 14, 12, 0, 0, tzinfo=UTC)

BACKOFF_SCHEDULE = [
    (1, BACKOFF_BASE_SECONDS),  # 30 s
    (2, 120),  # 2 min
    (3, 480),  # 8 min
    (4, 1920),  # 32 min
    (5, BACKOFF_CAP_SECONDS),  # capped (would be 128 min)
    (6, BACKOFF_CAP_SECONDS),
]
"""Delay after the Nth failed attempt: 30 s base, x4 exponent, 1 h cap."""


def _domain_event(event_id: str = "evt-1") -> DomainEvent:
    return DomainEvent(
        event_id=event_id,
        event_type=EventType.RUN_STARTED,
        occurred_at=NOW,
        change_id="chg-1",
        run_id="run-1",
        aggregate_id="run-1",
        aggregate_version=1,
    )


def test_backoff_schedule_grows_exponentially_and_caps() -> None:
    for attempts, expected in BACKOFF_SCHEDULE:
        assert backoff_delay_seconds(attempts) == expected, attempts


@pytest.mark.parametrize("attempts", [0, -1])
def test_backoff_rejects_attempts_below_one(attempts: int) -> None:
    with pytest.raises(ValueError, match="attempts"):
        backoff_delay_seconds(attempts)


@pytest.mark.parametrize(
    ("attempts", "expected"),
    [(MAX_ATTEMPTS - 1, False), (MAX_ATTEMPTS, True), (MAX_ATTEMPTS + 1, True)],
)
def test_dead_rule_exhausts_after_max_attempts(attempts: int, expected: bool) -> None:
    assert is_dead(attempts) is expected


def test_undelivered_covers_everything_except_delivered_and_waived() -> None:
    assert [status for status in DeliveryStatus if is_undelivered(status)] == [
        DeliveryStatus.PENDING,
        DeliveryStatus.FAILED,
        DeliveryStatus.DEAD,
    ]


def test_min_undelivered_sequence_skips_terminal_and_returns_none_when_clear() -> None:
    mixed = [(1, DeliveryStatus.DELIVERED), (2, DeliveryStatus.WAIVED), (3, DeliveryStatus.PENDING)]

    assert min_undelivered_sequence(mixed) == 3
    assert min_undelivered_sequence([(1, DeliveryStatus.DELIVERED)]) is None
    assert min_undelivered_sequence([]) is None


def test_ordering_gap_defers_the_candidate() -> None:
    # seq=1 is undelivered (failed): seq=2 must wait.
    stream = [(1, DeliveryStatus.FAILED), (2, DeliveryStatus.PENDING)]
    minimum = min_undelivered_sequence(stream)

    assert ordering_eligible(candidate_sequence=2, min_undelivered_sequence=minimum) is False
    assert ordering_eligible(candidate_sequence=1, min_undelivered_sequence=minimum) is True


def test_ordering_dead_blocks_the_stream_until_replay_or_skip() -> None:
    stream = [(1, DeliveryStatus.DEAD), (2, DeliveryStatus.PENDING)]

    assert (
        ordering_eligible(
            candidate_sequence=2, min_undelivered_sequence=min_undelivered_sequence(stream)
        )
        is False
    )


@pytest.mark.parametrize("head_status", [DeliveryStatus.DELIVERED, DeliveryStatus.WAIVED])
def test_ordering_delivered_or_waived_head_unlocks_the_next_sequence(
    head_status: DeliveryStatus,
) -> None:
    stream = [(1, head_status), (2, DeliveryStatus.PENDING)]

    assert (
        ordering_eligible(
            candidate_sequence=2, min_undelivered_sequence=min_undelivered_sequence(stream)
        )
        is True
    )


def test_ordering_without_undelivered_rows_admits_any_candidate() -> None:
    assert ordering_eligible(candidate_sequence=7, min_undelivered_sequence=None) is True


def test_cleanup_matrix() -> None:
    old = NOW - timedelta(days=RETENTION_DAYS + 1)
    recent = NOW - timedelta(days=1)
    exact = NOW - timedelta(days=RETENTION_DAYS)

    assert is_cleanup_eligible(
        delivery_statuses=[DeliveryStatus.DELIVERED], occurred_at=old, now=NOW
    )
    assert is_cleanup_eligible(delivery_statuses=[DeliveryStatus.WAIVED], occurred_at=old, now=NOW)
    assert is_cleanup_eligible(delivery_statuses=[], occurred_at=old, now=NOW)  # vacuous
    assert is_cleanup_eligible(
        delivery_statuses=[DeliveryStatus.DELIVERED], occurred_at=exact, now=NOW
    )
    assert not is_cleanup_eligible(
        delivery_statuses=[DeliveryStatus.DELIVERED], occurred_at=recent, now=NOW
    )
    # dead blocks deletion until replay or a recorded operator decision.
    assert not is_cleanup_eligible(
        delivery_statuses=[DeliveryStatus.DEAD], occurred_at=old, now=NOW
    )
    assert not is_cleanup_eligible(
        delivery_statuses=[DeliveryStatus.DELIVERED, DeliveryStatus.PENDING],
        occurred_at=old,
        now=NOW,
    )


def test_sort_outcomes_is_deterministic_and_none_consumer_comes_first() -> None:
    outcomes = [
        DeliveryOutcome(event_id="evt-2", consumer_id="tracker", outcome=DeliveryOutcomeKind.DEAD),
        DeliveryOutcome(event_id="evt-1", outcome=DeliveryOutcomeKind.CLEANED),
        DeliveryOutcome(
            event_id="evt-1", consumer_id="tracker", outcome=DeliveryOutcomeKind.DELIVERED
        ),
        DeliveryOutcome(event_id="evt-2", consumer_id="tracker", outcome=DeliveryOutcomeKind.DEAD),
    ]
    shuffled = [*outcomes]
    random.Random(42).shuffle(shuffled)

    assert sort_outcomes(shuffled) == (
        outcomes[1],  # evt-1, consumer None
        outcomes[2],  # evt-1/tracker
        outcomes[0],  # evt-2/tracker
        outcomes[3],  # evt-2/tracker
    )
    assert sort_outcomes(outcomes) == sort_outcomes(shuffled)


def test_dispatch_report_defaults_and_schema_version() -> None:
    report = DispatchReport(reserved=0)

    assert report.schema_version == 1
    assert report.outcomes == ()


def test_registry_defaults_to_the_noop_handler() -> None:
    registry = HandlerRegistry()

    assert isinstance(registry.handler_for("unknown-consumer"), NoOpHandler)
    assert isinstance(registry.handler_for("unknown-consumer"), DeliveryHandler)


def test_registry_returns_the_registered_handler_and_accepts_overrides() -> None:
    registry = HandlerRegistry()
    first = NoOpHandler()
    second = NoOpHandler()

    registry.register("tracker", first)
    registry.register("tracker", second)

    assert registry.handler_for("tracker") is second


def test_noop_handler_delivers_without_effect() -> None:
    asyncio.run(NoOpHandler().deliver(_domain_event(), "tracker"))
