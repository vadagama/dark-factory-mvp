"""Unit tests of the Plane webhook guard (T-033, ADR-013 p.6).

The negative cases carry the point of the guard: an unsigned, stale, tampered
or replayed delivery must never be accepted, and an unauthenticated caller must
not be able to poison the replay window.
"""

from datetime import UTC, datetime, timedelta

import pytest

from dark_factory.adapters.tracker import (
    ACCEPTED,
    DELIVERY_ID_HEADER,
    DUPLICATE_EVENT,
    EVENT_HEADER,
    INVALID_SIGNATURE,
    MALFORMED_TIMESTAMP,
    MISSING_EVENT_ID,
    MISSING_SIGNATURE,
    MISSING_TIMESTAMP,
    SIGNATURE_HEADER,
    STALE_TIMESTAMP,
    TIMESTAMP_HEADER,
    PlaneWebhookGuard,
    WebhookDeduplicator,
    compute_signature,
    verify_webhook,
)

NOW = datetime(2026, 9, 13, 9, 30, tzinfo=UTC)
BODY = b'{"event":"issue","action":"create","data":{"id":"issue-1"}}'
SECRET = "plane-webhook-secret"
ROTATED = "plane-webhook-secret-v2"

VALID_SECRETS = (SECRET,)


def _seconds(moment: datetime) -> int:
    return int(moment.timestamp())


def _headers(
    secret: str = SECRET,
    *,
    timestamp: int | None = None,
    event_id: str | None = "evt-1",
    event_type: str = "issue",
    body: bytes = BODY,
) -> dict[str, str]:
    """Headers of a delivery signed exactly as the deployment signs it."""
    moment = _seconds(NOW) if timestamp is None else timestamp
    headers = {
        TIMESTAMP_HEADER: str(moment),
        SIGNATURE_HEADER: compute_signature(secret, timestamp=str(moment), body=body),
        EVENT_HEADER: event_type,
    }
    if event_id is not None:
        headers[DELIVERY_ID_HEADER] = event_id
    return headers


# --- signature and timestamp -----------------------------------------------


def test_accepts_a_properly_signed_delivery() -> None:
    result = verify_webhook(_headers(), BODY, VALID_SECRETS, now=NOW)

    assert result.accepted is True
    assert result.reason == ACCEPTED
    assert result.event_id == "evt-1"
    assert result.event_type == "issue"


def test_accepts_lowercase_header_names() -> None:
    headers = {name.lower(): value for name, value in _headers().items()}

    assert verify_webhook(headers, BODY, VALID_SECRETS, now=NOW).accepted is True


def test_rejects_a_missing_signature() -> None:
    headers = _headers()
    del headers[SIGNATURE_HEADER]

    result = verify_webhook(headers, BODY, VALID_SECRETS, now=NOW)

    assert result.accepted is False
    assert result.reason == MISSING_SIGNATURE


def test_rejects_a_blank_signature() -> None:
    headers = {**_headers(), SIGNATURE_HEADER: "   "}

    assert verify_webhook(headers, BODY, VALID_SECRETS, now=NOW).reason == MISSING_SIGNATURE


def test_rejects_a_missing_timestamp() -> None:
    headers = _headers()
    del headers[TIMESTAMP_HEADER]

    assert verify_webhook(headers, BODY, VALID_SECRETS, now=NOW).reason == MISSING_TIMESTAMP


def test_rejects_a_malformed_timestamp() -> None:
    raw = "not-a-number"
    headers = {
        **_headers(),
        TIMESTAMP_HEADER: raw,
        SIGNATURE_HEADER: compute_signature(SECRET, timestamp=raw, body=BODY),
    }

    assert verify_webhook(headers, BODY, VALID_SECRETS, now=NOW).reason == MALFORMED_TIMESTAMP


def test_rejects_a_stale_timestamp() -> None:
    headers = _headers(timestamp=_seconds(NOW - timedelta(seconds=301)))

    result = verify_webhook(headers, BODY, VALID_SECRETS, now=NOW)

    assert result.accepted is False
    assert result.reason == STALE_TIMESTAMP
    # A rejected delivery still names its event, so an operator can correlate it.
    assert result.event_id == "evt-1"
    assert result.event_type == "issue"


def test_rejects_a_timestamp_from_the_future() -> None:
    headers = _headers(timestamp=_seconds(NOW + timedelta(seconds=301)))

    assert verify_webhook(headers, BODY, VALID_SECRETS, now=NOW).reason == STALE_TIMESTAMP


def test_accepts_the_window_boundary() -> None:
    headers = _headers(timestamp=_seconds(NOW - timedelta(seconds=300)))

    assert verify_webhook(headers, BODY, VALID_SECRETS, now=NOW).accepted is True


def test_accepts_a_narrower_tolerance_when_configured() -> None:
    headers = _headers(timestamp=_seconds(NOW - timedelta(seconds=30)))

    result = verify_webhook(headers, BODY, VALID_SECRETS, now=NOW, tolerance_seconds=10)

    assert result.reason == STALE_TIMESTAMP


def test_rejects_a_signature_made_with_another_secret() -> None:
    assert verify_webhook(_headers(ROTATED), BODY, VALID_SECRETS, now=NOW).reason == (
        INVALID_SIGNATURE
    )


def test_rejects_a_refreshed_timestamp_under_the_original_signature() -> None:
    """The timestamp is signed, so a captured request cannot be replayed forward."""
    headers = {**_headers(), TIMESTAMP_HEADER: str(_seconds(NOW) + 60)}

    assert verify_webhook(headers, BODY, VALID_SECRETS, now=NOW).reason == INVALID_SIGNATURE


def test_rejects_a_body_the_signature_does_not_cover() -> None:
    signature = compute_signature(SECRET, timestamp=str(_seconds(NOW)), body=b"{}")
    headers = {**_headers(), SIGNATURE_HEADER: signature}

    assert verify_webhook(headers, BODY, VALID_SECRETS, now=NOW).reason == INVALID_SIGNATURE


def test_rejects_everything_when_no_secret_is_configured() -> None:
    assert verify_webhook(_headers(), BODY, (), now=NOW).reason == INVALID_SIGNATURE


def test_accepts_a_delivery_signed_with_the_previous_secret() -> None:
    # Every secret is compared, so a rotation keeps in-flight deliveries valid.
    result = verify_webhook(_headers(ROTATED), BODY, (SECRET, ROTATED), now=NOW)

    assert result.accepted is True


def test_signature_changes_with_the_timestamp() -> None:
    first = compute_signature(SECRET, timestamp="1000", body=BODY)
    second = compute_signature(SECRET, timestamp="1001", body=BODY)

    assert first != second


# --- replay window ---------------------------------------------------------


def test_guard_accepts_a_delivery_then_rejects_its_replay() -> None:
    guard = PlaneWebhookGuard([SECRET])

    first = guard.verify(_headers(), BODY, now=NOW)
    replay = guard.verify(_headers(), BODY, now=NOW)

    assert first.accepted is True
    assert replay.accepted is False
    assert replay.reason == DUPLICATE_EVENT
    assert replay.event_id == "evt-1"


def test_guard_accepts_distinct_event_ids() -> None:
    guard = PlaneWebhookGuard([SECRET])

    assert guard.verify(_headers(event_id="evt-1"), BODY, now=NOW).accepted is True
    assert guard.verify(_headers(event_id="evt-2"), BODY, now=NOW).accepted is True


def test_guard_requires_a_delivery_id() -> None:
    guard = PlaneWebhookGuard([SECRET])

    result = guard.verify(_headers(event_id=None), BODY, now=NOW)

    assert result.accepted is False
    assert result.reason == MISSING_EVENT_ID
    assert result.event_type == "issue"


def test_an_unauthenticated_delivery_does_not_claim_its_event_id() -> None:
    """Only authentic deliveries reach the replay window, so it cannot be poisoned."""
    guard = PlaneWebhookGuard([SECRET])

    forged = guard.verify(_headers(ROTATED), BODY, now=NOW)
    genuine = guard.verify(_headers(), BODY, now=NOW)

    assert forged.reason == INVALID_SIGNATURE
    assert genuine.accepted is True


def test_guard_rotates_keeping_the_previous_secret_active() -> None:
    guard = PlaneWebhookGuard([SECRET])

    before = guard.verify(_headers(ROTATED, event_id="evt-1"), BODY, now=NOW)
    guard.rotate(ROTATED)
    after = guard.verify(_headers(ROTATED, event_id="evt-2"), BODY, now=NOW)
    previous = guard.verify(_headers(SECRET, event_id="evt-3"), BODY, now=NOW)

    assert before.reason == INVALID_SIGNATURE
    assert guard.secrets == (ROTATED, SECRET)
    assert after.accepted is True
    assert previous.accepted is True


def test_guard_drops_the_oldest_secret_after_the_next_rotation() -> None:
    guard = PlaneWebhookGuard([SECRET])
    guard.rotate(ROTATED)
    guard.rotate("plane-webhook-secret-v3")

    dropped = guard.verify(_headers(SECRET, event_id="evt-1"), BODY, now=NOW)
    kept = guard.verify(_headers(ROTATED, event_id="evt-2"), BODY, now=NOW)

    assert guard.secrets == ("plane-webhook-secret-v3", ROTATED)
    assert dropped.reason == INVALID_SIGNATURE
    assert kept.accepted is True


def test_guard_rejects_construction_without_secrets() -> None:
    with pytest.raises(ValueError, match="at least one webhook secret"):
        PlaneWebhookGuard([])
    with pytest.raises(ValueError, match="at least one webhook secret"):
        PlaneWebhookGuard(["", "  "][:0])


def test_guard_rotate_rejects_a_blank_secret() -> None:
    guard = PlaneWebhookGuard([SECRET])

    with pytest.raises(ValueError, match="must not be blank"):
        guard.rotate("")


def test_deduplicator_remembers_and_evicts_ids() -> None:
    dedup = WebhookDeduplicator(capacity=2)

    assert dedup.register("evt-1") is True
    assert dedup.register("evt-2") is True
    assert dedup.register("evt-1") is False
    assert dedup.register("evt-3") is True
    assert len(dedup) == 2
    # evt-1 fell out of the bounded window and is admissible again.
    assert dedup.register("evt-1") is True


def test_deduplicator_rejects_a_non_positive_capacity() -> None:
    with pytest.raises(ValueError, match="capacity must be positive"):
        WebhookDeduplicator(capacity=0)
