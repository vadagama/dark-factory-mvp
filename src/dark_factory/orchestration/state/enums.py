"""Statuses specific to the operational state (ADR-006/016, T-006).

These are persistence-layer statuses: they do not travel as wire contracts, so
they live here rather than in ``dark_factory.changes``.
"""

from enum import StrEnum


class EffectStatus(StrEnum):
    """Lifecycle of one external side effect in the durable effect ledger.

    ``unknown`` means a crash happened between the external call and the commit
    that would have recorded the outcome; a retry must look the effect up by its
    deterministic marker before calling again (ADR-006 p.3).
    """

    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    SUCCEEDED = "succeeded"
    UNKNOWN = "unknown"


class DeliveryStatus(StrEnum):
    """Per-consumer delivery state of one outbox event (ADR-016 p.5)."""

    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"
    DEAD = "dead"
    WAIVED = "waived"
