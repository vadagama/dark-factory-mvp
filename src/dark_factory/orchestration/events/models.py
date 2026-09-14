"""Models of one dispatch pass: per-delivery outcomes and the report (T028, ADR-016 p.5/p.9).

The pass output is a journal entry: every touched delivery gets one
:class:`DeliveryOutcome` and every cleaned event one event-level outcome
(``cleaned``, the only kind with ``consumer_id=None``). The models are frozen
pydantic so a pass report serializes to JSON for ``factory outbox dispatch
--json`` and stays comparable between passes; :func:`sort_outcomes` fixes a
deterministic record order for the journal.
"""

from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class DeliveryOutcomeKind(StrEnum):
    """What one dispatch pass did with one delivery (or event, for ``cleaned``)."""

    DELIVERED = "delivered"
    FAILED = "failed"
    DEFERRED = "deferred"
    DEAD = "dead"
    REPLAYED = "replayed"
    SKIPPED = "skipped"
    CLEANED = "cleaned"


class DeliveryOutcome(BaseModel):
    """Outcome of one pass for one delivery.

    ``consumer_id`` is ``None`` only for the event-level ``cleaned`` outcome;
    every delivery-level outcome carries it. ``next_attempt_at`` is set for
    ``failed`` (the backoff instant) and ``None`` for terminal kinds.
    """

    model_config = ConfigDict(frozen=True)

    event_id: str = Field(min_length=1)
    consumer_id: str | None = None
    outcome: DeliveryOutcomeKind
    attempts: int = Field(default=0, ge=0)
    next_attempt_at: datetime | None = None
    error: str | None = None


class DispatchReport(BaseModel):
    """Journal entry of one dispatch pass.

    ``reserved`` counts the deliveries this pass actually reserved (lease
    taken); deferred and cleaned records appear only in ``outcomes``.
    """

    model_config = ConfigDict(frozen=True)

    schema_version: int = 1
    reserved: int = Field(ge=0)
    outcomes: tuple[DeliveryOutcome, ...] = ()


def sort_outcomes(outcomes: Iterable[DeliveryOutcome]) -> tuple[DeliveryOutcome, ...]:
    """Deterministic journal order: by ``event_id``, then ``consumer_id`` (``None`` first)."""
    return tuple(sorted(outcomes, key=lambda entry: (entry.event_id, entry.consumer_id or "")))
