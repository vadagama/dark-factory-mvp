"""Plane tracker adapter behind ``TrackerPort`` (T-033, ADR-013).

Three pieces live here: the REST adapter for a self-hosted Plane
(``adapter.py`` over ``client.py``), its environment configuration
(``config.py``) and the webhook guard (``webhook.py``). ``NoOpTracker`` is the
documented stand-in for deployments without a tracker (ADR-013 p.5, FR-020):
a missing tracker degrades intake, it never blocks CLI/Console.
"""

from dark_factory.adapters.tracker.adapter import (
    APPROVAL_COMMENT_TEMPLATE,
    DEFAULT_RISK_CLASS,
    IDEMPOTENCY_MARKER_PREFIX,
    STATUS_COMMENT_TEMPLATE,
    PlaneTrackerAdapter,
    idempotency_marker,
)
from dark_factory.adapters.tracker.client import DEFAULT_TIMEOUT_SECONDS, PlaneAPIError, PlaneClient
from dark_factory.adapters.tracker.config import PlaneConfig
from dark_factory.adapters.tracker.noop import NoOpTracker
from dark_factory.adapters.tracker.webhook import (
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
    WebhookVerification,
    compute_signature,
    verify_webhook,
)

__all__ = [
    "ACCEPTED",
    "APPROVAL_COMMENT_TEMPLATE",
    "DEFAULT_RISK_CLASS",
    "DEFAULT_TIMEOUT_SECONDS",
    "DELIVERY_ID_HEADER",
    "DUPLICATE_EVENT",
    "EVENT_HEADER",
    "IDEMPOTENCY_MARKER_PREFIX",
    "INVALID_SIGNATURE",
    "MALFORMED_TIMESTAMP",
    "MISSING_EVENT_ID",
    "MISSING_SIGNATURE",
    "MISSING_TIMESTAMP",
    "SIGNATURE_HEADER",
    "STALE_TIMESTAMP",
    "STATUS_COMMENT_TEMPLATE",
    "TIMESTAMP_HEADER",
    "NoOpTracker",
    "PlaneAPIError",
    "PlaneClient",
    "PlaneConfig",
    "PlaneTrackerAdapter",
    "PlaneWebhookGuard",
    "WebhookDeduplicator",
    "WebhookVerification",
    "compute_signature",
    "idempotency_marker",
    "verify_webhook",
]
