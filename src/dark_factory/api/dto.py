"""Request/response DTOs of the factory API (T035, contract api.md).

Domain Pydantic models (``Change``, ``StageResult``, ``Decision``, ``Finding``,
``GateResult``, ``Evidence``) are reused directly as request/response schemas
so the API wire format equals the versioned run-record contracts (ADR-015).
"""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from dark_factory.changes.enums import Gate
from dark_factory.changes.findings import GateResult
from dark_factory.changes.refs import ArtifactRef
from dark_factory.changes.run import Change
from dark_factory.ci.stages import CiStage, CiStageGroup, CiStageWeight


class ErrorBody(BaseModel):
    """RFC 7807-like error body of every failed response (contract api.md)."""

    type: str = "about:blank"
    title: str
    status: int
    detail: str


class ApprovalRequest(BaseModel):
    """Body of ``POST /changes/{change_id}/approvals`` (contract api.md).

    ``subject_revision`` is required: the decision is version-bound to it
    (FR-003, ADR-009 p.7). ``expected_state_revision`` turns the write into an
    optimistic-concurrency check (ADR-006 p.4).
    """

    gate: Gate
    outcome: Literal["approved", "rejected", "waived"]
    subject_revision: str = Field(min_length=1)
    comment: str | None = None
    expected_state_revision: int | None = None


class RunSummary(BaseModel):
    """Item of ``GET /runs``."""

    run_id: str
    change_id: str
    route: str
    provider: str
    status: str
    state_revision: int
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None


class StageSummary(BaseModel):
    """Logical stage operation of a run (the ``stage`` table)."""

    stage: str
    status: str
    input_revision: str | None
    attempt_count: int


class UsageAggregate(BaseModel):
    """Usage sums over the run's attempts (FR-018/FR-024, SC-003)."""

    prompt_tokens: int
    completion_tokens: int
    cost: Decimal | None
    manual_interventions: int


class RunCard(BaseModel):
    """Response of ``GET /runs/{run_id}``: state, stages, gates, blockers, cost."""

    run_id: str
    change_id: str
    route: str
    provider: str
    status: str
    state_revision: int
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None
    stages: list[StageSummary]
    usage: UsageAggregate
    gates: list[GateResult]
    open_blockers: int


class TraceStage(BaseModel):
    """One stage link of the SC-007 trace chain."""

    stage: str
    status: str
    input_revision: str | None
    attempt_number: int
    produced_at: datetime
    artifacts: list[ArtifactRef]


class RunTrace(BaseModel):
    """Response of ``GET /runs/{run_id}/trace`` (SC-007)."""

    change_id: str
    run_id: str
    status: str
    chain: list[TraceStage]


class ChangeRunRef(BaseModel):
    """One run of a change on the change card."""

    run_id: str
    status: str


class ChangeCard(Change):
    """Response of ``GET /changes/{change_id}``: the Change document plus links."""

    runs: list[ChangeRunRef] = Field(default_factory=list)
    decisions_count: int = 0


class ChangeTrace(BaseModel):
    """Response of ``GET /changes/{change_id}/trace``: the full SC-007 chain."""

    change_id: str
    runs: list[RunTrace]


class CiStageView(BaseModel):
    """One CI stage of ``GET /ci/stages`` (T059, ADR-027).

    ``enabled`` is ``True`` when the stage runs, ``False`` when its toggle
    variable switches it off, and ``None`` when this contour cannot reach the
    repository (``available=false``): an unknown state is reported as unknown
    rather than guessed (fail-closed).
    """

    job: str
    title: str
    group: CiStageGroup
    summary: str
    local_command: str
    weight: CiStageWeight
    variable: str
    enabled: bool | None = None

    @classmethod
    def of(cls, stage: CiStage, enabled: bool | None) -> "CiStageView":
        """View of one catalog entry with its current (or unknown) state."""
        return cls(
            job=stage.job,
            title=stage.title,
            group=stage.group,
            summary=stage.summary,
            local_command=stage.local_command,
            weight=stage.weight,
            variable=stage.variable,
            enabled=enabled,
        )


class CiStagesView(BaseModel):
    """Response of ``GET /ci/stages``: the catalog plus the current toggle state."""

    schema_version: int = 1
    repository: str | None = None
    available: bool
    reason: str | None = None
    stages: list[CiStageView]


class CiStageToggleRequest(BaseModel):
    """Body of ``PUT /ci/stages/{job}``: the wanted state, not an event (idempotent).

    ``strict=True``: a toggle is a boolean, and pydantic must not coerce
    ``"yes"`` or ``1`` into it — the state of a pipeline gate is not a place for
    a lenient parse.
    """

    enabled: bool = Field(strict=True)
