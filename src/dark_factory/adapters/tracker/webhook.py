"""Plane webhook guard: HMAC signature, signed-timestamp window, replay dedup (T-033).

A webhook is an *accelerator*, never the source of truth (ADR-013 p.2,
ADR-016 p.8): the factory re-reads state before acting, so a rejected delivery
costs latency, not correctness. The guard therefore fails closed and stays
dumb — verify, then hand the payload to the caller.

Signature contract: ``X-Plane-Signature`` is the hex HMAC-SHA256 of
``<timestamp>.<raw body>`` keyed by the webhook secret, and
``X-Plane-Timestamp`` carries the same Unix seconds value. Binding the
timestamp into the signed material is what makes the window meaningful — a
captured request cannot be replayed with a refreshed timestamp, because
changing the timestamp invalidates the signature. Stock Plane signs the body
alone, so a deployment wires this contract through the webhook proxy that
receives Plane's delivery (the payload itself is forwarded untouched).

Rotation keeps two secrets active at once (ADR-013 p.6): the current one and
the previous one, so deliveries signed just before a rotation still verify.
``PlaneWebhookGuard.rotate`` moves the current secret into the previous slot.

Verification order is fixed and the first failure wins: signature presence,
timestamp presence, timestamp format, window, signature match — and only then
does an authentic delivery claim its event id in the replay window.
"""

import hmac
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Final

SIGNATURE_HEADER: Final[str] = "X-Plane-Signature"
"""Hex HMAC-SHA256 of ``<timestamp>.<body>`` under the webhook secret."""

TIMESTAMP_HEADER: Final[str] = "X-Plane-Timestamp"
"""Unix seconds value that is part of the signed material."""

DELIVERY_ID_HEADER: Final[str] = "X-Plane-Delivery"
"""Delivery id used for replay deduplication."""

EVENT_HEADER: Final[str] = "X-Plane-Event"
"""Plane event type of the delivery (``issue``, ``issue_comment``, …)."""

DEFAULT_TOLERANCE_SECONDS: Final[int] = 300
"""Accepted clock skew between the signed timestamp and the moment of verification."""

DEFAULT_DEDUP_CAPACITY: Final[int] = 4096
"""How many recent delivery ids the replay window remembers."""

MAX_ACTIVE_SECRETS: Final[int] = 2
"""Secrets accepted simultaneously during a rotation (ADR-013 p.6)."""

ACCEPTED: Final[str] = "accepted"
MISSING_SIGNATURE: Final[str] = "missing_signature"
MISSING_TIMESTAMP: Final[str] = "missing_timestamp"
MALFORMED_TIMESTAMP: Final[str] = "malformed_timestamp"
STALE_TIMESTAMP: Final[str] = "stale_timestamp"
INVALID_SIGNATURE: Final[str] = "invalid_signature"
MISSING_EVENT_ID: Final[str] = "missing_event_id"
DUPLICATE_EVENT: Final[str] = "duplicate_event"


def compute_signature(secret: str, *, timestamp: str, body: bytes) -> str:
    """Hex HMAC-SHA256 of ``<timestamp>.<body>`` under ``secret``."""
    signed = f"{timestamp}.".encode() + body
    return hmac.new(secret.encode(), signed, sha256).hexdigest()


@dataclass(frozen=True, slots=True)
class WebhookVerification:
    """Outcome of one delivery check; ``reason`` is machine-readable, never a secret."""

    accepted: bool
    reason: str
    event_id: str | None = None
    event_type: str | None = None


def verify_webhook(
    headers: Mapping[str, str],
    body: bytes,
    secrets: Sequence[str],
    *,
    now: datetime,
    tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
) -> WebhookVerification:
    """Verify one delivery without touching state.

    ``now`` is an explicit parameter (deterministic tests, no hidden clock). An
    empty secret ring rejects everything: a webhook that cannot be authenticated
    is not a webhook.
    """
    normalized = {name.lower(): value for name, value in headers.items()}
    signature = (normalized.get(SIGNATURE_HEADER.lower()) or "").strip()
    if not signature:
        return _rejected(MISSING_SIGNATURE, normalized)
    timestamp_raw = (normalized.get(TIMESTAMP_HEADER.lower()) or "").strip()
    if not timestamp_raw:
        return _rejected(MISSING_TIMESTAMP, normalized)
    try:
        timestamp = int(timestamp_raw)
    except ValueError:
        return _rejected(MALFORMED_TIMESTAMP, normalized)
    if abs(int(now.timestamp()) - timestamp) > tolerance_seconds:
        return _rejected(STALE_TIMESTAMP, normalized)
    matches = False
    for secret in secrets:
        expected = compute_signature(secret, timestamp=timestamp_raw, body=body)
        # All secrets are compared, so no early exit reveals which one matched.
        matches |= hmac.compare_digest(expected, signature)
    if not matches:
        return _rejected(INVALID_SIGNATURE, normalized)
    return WebhookVerification(
        accepted=True,
        reason=ACCEPTED,
        event_id=_header(normalized, DELIVERY_ID_HEADER),
        event_type=_header(normalized, EVENT_HEADER),
    )


class WebhookDeduplicator:
    """Bounded replay window keyed by delivery id (ADR-013 p.6).

    ``register`` reports whether an id is new and remembers it; the oldest id is
    evicted past ``capacity``. Only authentic deliveries are registered, so an
    unauthenticated caller cannot poison the window.
    """

    def __init__(self, *, capacity: int = DEFAULT_DEDUP_CAPACITY) -> None:
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self._capacity = capacity
        self._seen: OrderedDict[str, None] = OrderedDict()

    def register(self, event_id: str) -> bool:
        """``True`` when ``event_id`` is new (and now recorded), ``False`` on a replay."""
        if event_id in self._seen:
            return False
        self._seen[event_id] = None
        while len(self._seen) > self._capacity:
            self._seen.popitem(last=False)
        return True

    def __len__(self) -> int:
        """Number of ids currently remembered."""
        return len(self._seen)


class PlaneWebhookGuard:
    """Webhook guard of one Plane deployment: secret ring, window and replay window."""

    def __init__(
        self,
        secrets: Sequence[str],
        *,
        tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
        dedup_capacity: int = DEFAULT_DEDUP_CAPACITY,
    ) -> None:
        cleaned = tuple(secret for secret in secrets if secret)
        if not cleaned:
            raise ValueError("at least one webhook secret is required")
        self.tolerance_seconds = tolerance_seconds
        self._secrets = cleaned
        self._dedup = WebhookDeduplicator(capacity=dedup_capacity)

    @property
    def secrets(self) -> tuple[str, ...]:
        """Active secrets, current first (secrets — never log or echo them)."""
        return self._secrets

    def rotate(self, secret: str) -> None:
        """Make ``secret`` current, keeping the previous one active during the rotation."""
        if not secret:
            raise ValueError("secret must not be blank")
        self._secrets = (secret, *self._secrets[: MAX_ACTIVE_SECRETS - 1])

    def verify(
        self, headers: Mapping[str, str], body: bytes, *, now: datetime
    ) -> WebhookVerification:
        """Authenticate a delivery and consume its event id.

        An authentic delivery without a delivery id cannot be deduplicated and is
        rejected (``missing_event_id``); a repeated id is rejected
        (``duplicate_event``). Signature and timestamp are checked first, so only
        an authentic delivery ever claims an id.
        """
        verification = verify_webhook(
            headers,
            body,
            self._secrets,
            now=now,
            tolerance_seconds=self.tolerance_seconds,
        )
        if not verification.accepted:
            return verification
        event_id = verification.event_id
        if event_id is None:
            return WebhookVerification(
                accepted=False,
                reason=MISSING_EVENT_ID,
                event_type=verification.event_type,
            )
        if not self._dedup.register(event_id):
            return WebhookVerification(
                accepted=False,
                reason=DUPLICATE_EVENT,
                event_id=event_id,
                event_type=verification.event_type,
            )
        return verification


def _header(headers: Mapping[str, str], name: str) -> str | None:
    """A non-blank header value (header lookup is case-insensitive), or ``None``."""
    return (headers.get(name.lower()) or "").strip() or None


def _rejected(reason: str, headers: Mapping[str, str]) -> WebhookVerification:
    """A rejected verification that still carries the event headers for logging."""
    return WebhookVerification(
        accepted=False,
        reason=reason,
        event_id=_header(headers, DELIVERY_ID_HEADER),
        event_type=_header(headers, EVENT_HEADER),
    )
