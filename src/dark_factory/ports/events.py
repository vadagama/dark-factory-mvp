"""Domain event envelope of the transactional outbox (ADR-016, contracts/events.md)."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from dark_factory.changes.enums import Stage
from dark_factory.changes.refs import ArtifactRef


class EventType(StrEnum):
    """MVP event types (contracts/events.md)."""

    CHANGE_INTAKEN = "change.intaken"
    RUN_STARTED = "run.started"
    RUN_STAGE_COMPLETED = "run.stage_completed"
    RUN_STATUS_CHANGED = "run.status_changed"
    GATE_EVALUATED = "gate.evaluated"
    APPROVAL_RECORDED = "approval.recorded"
    MERGE_COMPLETED = "merge.completed"
    RELEASE_COMPLETED = "release.completed"
    USAGE_RECORDED = "usage.recorded"


class DomainEvent(BaseModel):
    """Immutable event envelope (contracts/events.md).

    Field names mirror the envelope keys in snake_case. Producers write the
    event to the outbox in the same transaction as the state change
    (ADR-016 p.7); consumers deduplicate by ``event_id``.
    """

    model_config = ConfigDict(frozen=True)

    event_id: str = Field(min_length=1)
    event_type: EventType
    event_version: int = Field(default=1, ge=1)
    occurred_at: datetime
    change_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    stage: Stage | None = None
    aggregate_id: str = Field(min_length=1)
    aggregate_version: int = Field(ge=1)
    correlation_id: str | None = None
    causation_id: str | None = None
    artifact_refs: list[ArtifactRef] = []
    payload: dict[str, Any] = {}
