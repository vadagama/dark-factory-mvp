"""Contract tests for TrackerPort."""

import asyncio
from collections.abc import Callable

from dark_factory.ports import Change, Gate, TrackerPort


def test_adapter_satisfies_protocol(tracker_port: TrackerPort) -> None:
    assert isinstance(tracker_port, TrackerPort)


def test_get_change_resolves_seeded_external_ref(
    tracker_port: TrackerPort, tracked_change: Change
) -> None:
    assert asyncio.run(tracker_port.get_change("PLANE-42")) == tracked_change


def test_get_change_unknown_ref_is_none(tracker_port: TrackerPort) -> None:
    assert asyncio.run(tracker_port.get_change("PLANE-404")) is None


def test_publish_status_is_idempotent_by_key(
    tracker_port: TrackerPort,
    published_statuses: Callable[[str], tuple[str, ...]],
) -> None:
    asyncio.run(tracker_port.publish_status("chg-001", "in_progress", idempotency_key="s1"))
    asyncio.run(tracker_port.publish_status("chg-001", "in_progress", idempotency_key="s1"))
    assert published_statuses("chg-001") == ("in_progress",)
    asyncio.run(tracker_port.publish_status("chg-001", "in_review", idempotency_key="s2"))
    assert published_statuses("chg-001") == ("in_progress", "in_review")


def test_request_approval_is_idempotent_by_key(
    tracker_port: TrackerPort,
    requested_approvals: Callable[[str], tuple[Gate, ...]],
) -> None:
    asyncio.run(tracker_port.request_approval("chg-001", Gate.CODE, idempotency_key="a1"))
    asyncio.run(tracker_port.request_approval("chg-001", Gate.CODE, idempotency_key="a1"))
    assert requested_approvals("chg-001") == (Gate.CODE,)
    asyncio.run(tracker_port.request_approval("chg-001", Gate.REVIEW, idempotency_key="a2"))
    assert requested_approvals("chg-001") == (Gate.CODE, Gate.REVIEW)
