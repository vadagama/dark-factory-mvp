"""Core change entities and status invariants (T-003).

``ChangeRun``/``StageRun`` carry authoritative operational state (ADR-004):
transitions are validated against explicit tables, ``state_revision`` guards
concurrent writers (ADR-006 p.4), and ``StageResult`` is an immutable artifact
used as transport and recovery input between CI jobs (ADR-005 p.2, ADR-006 p.4).
The stage-level flow table (stage x NextAction) is built on top of this in T-004.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from dark_factory.changes.clock import utc_now
from dark_factory.changes.conversations import QuestionDraft, ReworkSummary
from dark_factory.changes.enums import (
    ChangeSource,
    FindingSeverity,
    FindingStatus,
    Provider,
    RiskClass,
    Route,
    RunStatus,
    Scenario,
    Stage,
    StageStatus,
)
from dark_factory.changes.errors import InvalidStatusTransition as InvalidStatusTransition
from dark_factory.changes.escalations import EscalationViolation
from dark_factory.changes.findings import Finding, GateResult
from dark_factory.changes.implementation_contract import ImplementationContract
from dark_factory.changes.intake import IntakeBrief, SpendLimit
from dark_factory.changes.next_action import NextAction
from dark_factory.changes.refs import ArtifactRef, ChangeRequestRef, Evidence, RepositoryRef
from dark_factory.changes.release_records import ReleaseEvidence
from dark_factory.changes.usage import BudgetSnapshot, Usage

SCHEMA_VERSION: Final = 1
"""Schema version of the versioned contracts RunRecord/StageResult (ADR-015 p.3)."""

# Literal contract type of SCHEMA_VERSION; keep the two in sync.
type SchemaVersion = Literal[1]

RUN_STATUS_TRANSITIONS: Final[dict[RunStatus, frozenset[RunStatus]]] = {
    RunStatus.PENDING: frozenset({RunStatus.RUNNING, RunStatus.CANCELED, RunStatus.SUPERSEDED}),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.WAITING,
            RunStatus.BLOCKED,
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.CANCELED,
            RunStatus.SUPERSEDED,
        }
    ),
    RunStatus.WAITING: frozenset(
        {
            RunStatus.RUNNING,
            RunStatus.BLOCKED,
            RunStatus.FAILED,
            RunStatus.CANCELED,
            RunStatus.SUPERSEDED,
        }
    ),
    RunStatus.BLOCKED: frozenset(
        {RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELED, RunStatus.SUPERSEDED}
    ),
    RunStatus.SUCCEEDED: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.CANCELED: frozenset(),
    RunStatus.SUPERSEDED: frozenset(),
}

STAGE_STATUS_TRANSITIONS: Final[dict[StageStatus, frozenset[StageStatus]]] = {
    StageStatus.PENDING: frozenset(
        {
            StageStatus.IN_PROGRESS,
            StageStatus.SKIPPED,
            StageStatus.CANCELED,
            StageStatus.SUPERSEDED,
        }
    ),
    StageStatus.IN_PROGRESS: frozenset(
        {
            StageStatus.WAITING,
            StageStatus.SUCCEEDED,
            StageStatus.FAILED,
            StageStatus.BLOCKED,
            StageStatus.CANCELED,
            StageStatus.SUPERSEDED,
        }
    ),
    StageStatus.WAITING: frozenset(
        {
            StageStatus.IN_PROGRESS,
            StageStatus.FAILED,
            StageStatus.BLOCKED,
            StageStatus.CANCELED,
            StageStatus.SUPERSEDED,
        }
    ),
    StageStatus.BLOCKED: frozenset(
        {
            StageStatus.IN_PROGRESS,
            StageStatus.FAILED,
            StageStatus.CANCELED,
            StageStatus.SUPERSEDED,
        }
    ),
    # A stage failure is not terminal: a retry starts a new attempt of the same
    # logical operation (ADR-006 p.3/p.7).
    StageStatus.FAILED: frozenset(
        {StageStatus.IN_PROGRESS, StageStatus.CANCELED, StageStatus.SUPERSEDED}
    ),
    # A new input revision (for example after a rework round) is a new logical
    # operation and gets its own StageRun (ADR-006 p.3); the succeeded one is final.
    StageStatus.SUCCEEDED: frozenset(),
    StageStatus.SKIPPED: frozenset(),
    StageStatus.SUPERSEDED: frozenset(),
    StageStatus.CANCELED: frozenset(),
}

RUN_TERMINAL_STATUSES: Final[frozenset[RunStatus]] = frozenset(
    status for status, targets in RUN_STATUS_TRANSITIONS.items() if not targets
)
STAGE_TERMINAL_STATUSES: Final[frozenset[StageStatus]] = frozenset(
    status for status, targets in STAGE_STATUS_TRANSITIONS.items() if not targets
)

RETRYABLE_STAGE_STATUSES: Final[frozenset[StageStatus]] = frozenset(
    {StageStatus.FAILED, StageStatus.BLOCKED}
)
"""Statuses an attempt may be retried from: FAILED/BLOCKED -> IN_PROGRESS (ADR-006 p.7).

A retry is a new *physical* attempt of the same logical operation: the input
revision does not change, so the operation key is unchanged and only the attempt
number advances (``changes.keys.attempt_id``). ``waiting`` is not retryable — a
waiting attempt is resumed with the same number, the result already committed
(ADR-006 p.8) — and ``succeeded`` is final (ADR-006 p.3).
"""

# Statuses a StageResult may carry: an attempt still in progress has no result.
_RESULT_STATUSES: Final[frozenset[StageStatus]] = frozenset(
    {
        StageStatus.WAITING,
        StageStatus.SUCCEEDED,
        StageStatus.FAILED,
        StageStatus.BLOCKED,
    }
)


class Change(BaseModel):
    """A unit of work flowing through the factory (intake output, hld-mvp 8)."""

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str | None = None
    source: ChangeSource
    external_ref: str | None = None
    product: RepositoryRef
    product_id: str | None = None
    """Optional owning product (ADR-030 p.2); ``None`` for pre-T065 changes."""
    risk_class: RiskClass
    brief: IntakeBrief | None = None
    """Structured brief of the intake (T071); ``None`` for pre-T071 changes."""
    scenario: Scenario = Scenario.FULL
    """Scope the operator chose at intake (T071); ``full`` for pre-T071 changes."""
    spend_limit: SpendLimit | None = None
    """Hard spend limit of the change (T071); ``None`` — no operator limit."""
    change_request: ChangeRequestRef | None = None
    created_at: datetime = Field(default_factory=utc_now)


class StageRun(BaseModel):
    """Operational state of one stage of a run."""

    id: str = Field(min_length=1)
    stage: Stage
    status: StageStatus = StageStatus.PENDING
    attempt_number: int = Field(default=1, ge=1)
    input_revision: str | None = None
    state_revision: int = Field(default=1, ge=1)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def apply_status(self, target: StageStatus) -> None:
        """Move to ``target``; raises InvalidStatusTransition outside the table."""
        if target not in STAGE_STATUS_TRANSITIONS[self.status]:
            raise InvalidStatusTransition(
                f"Stage transition {self.status.value} -> {target.value} is not allowed"
            )
        self.status = target
        self.state_revision += 1
        if target in STAGE_TERMINAL_STATUSES:
            self.finished_at = utc_now()

    def begin_retry(self, attempt_number: int) -> None:
        """Advance the stage run to a retry attempt of the same logical operation.

        A retry is allowed only from :data:`RETRYABLE_STAGE_STATUSES` and only to
        the very next attempt number (ADR-006 p.7): the operation identity — the
        input revision — is deliberately unchanged, so re-entering the stage is a
        new physical attempt of one logical operation, never a new operation. The
        status walk that follows (to ``in_progress``) is validated separately by
        :meth:`apply_status`, which stays the only status transition point.
        """
        if self.status not in RETRYABLE_STAGE_STATUSES:
            raise InvalidStatusTransition(
                f"Stage status {self.status.value} does not allow a retry"
            )
        expected = self.attempt_number + 1
        if attempt_number != expected:
            raise InvalidStatusTransition(
                f"Retry of attempt {self.attempt_number} must be attempt {expected}, "
                f"got {attempt_number}"
            )
        self.attempt_number = attempt_number


class ChangeRun(BaseModel):
    """One execution of a change through the factory.

    ``provider`` is fixed at run start and never changes during the run
    (exclusive execution, ADR-019 p.5). ``state_revision`` is the optimistic
    concurrency guard (ADR-006 p.4).

    ``implementation_contract`` is the approved Implementation Contract
    (ADR-018 p.3): the runner copies it from the change when creating the run.
    A run without an approved contract does not enter construction (T-016).
    """

    id: str = Field(min_length=1)
    change_id: str = Field(min_length=1)
    route: Route
    provider: Provider
    status: RunStatus = RunStatus.PENDING
    state_revision: int = Field(default=1, ge=1)
    stages: list[StageRun] = []
    budget: BudgetSnapshot = Field(default_factory=BudgetSnapshot)
    implementation_contract: ImplementationContract | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None

    def apply_status(self, target: RunStatus) -> None:
        """Move to ``target``; raises InvalidStatusTransition outside the table."""
        if target not in RUN_STATUS_TRANSITIONS[self.status]:
            raise InvalidStatusTransition(
                f"Run transition {self.status.value} -> {target.value} is not allowed"
            )
        self.status = target
        self.state_revision += 1
        self.updated_at = utc_now()
        if target in RUN_TERMINAL_STATUSES:
            self.finished_at = self.updated_at


class StageResult(BaseModel):
    """Immutable result of a stage attempt (versioned contract, ADR-015 p.3).

    ``status`` must be a result status, and the result is persisted before any
    external wait (ADR-006 p.8).

    ``escalations`` lists machine-detected escalation conditions for this
    attempt (ADR-018 p.5): the flow stops autonomous progress while any are
    declared.
    """

    model_config = ConfigDict(frozen=True)

    schema_version: SchemaVersion = SCHEMA_VERSION
    stage: Stage
    run_id: str = Field(min_length=1)
    change_id: str = Field(min_length=1)
    attempt_number: int = Field(default=1, ge=1)
    input_revision: str | None = None
    status: StageStatus
    next_action: NextAction
    artifacts: list[ArtifactRef] = []
    evidence: list[Evidence] = []
    gate_results: list[GateResult] = []
    findings: list[Finding] = []
    escalations: list[EscalationViolation] = []
    release: ReleaseEvidence | None = None
    """Additive optional release-verification section, like ``RunRecord.release``
    (T034 precedent): records written before it parse unchanged (the field
    defaults to ``None``) and older readers ignore the new key, so
    ``schema_version`` stays ``1``."""
    usage: Usage | None = None
    questions: list[QuestionDraft] = []
    """Questions the agent stated in this attempt (T078, ADR-034 p.5): additive and
    optional like ``release`` — the runner stores them as ``Question`` entities of
    the change; records written before it parse unchanged (schema version 1)."""
    rework_summary: ReworkSummary | None = None
    """The agent's "what changed / what remains" report after a rework round (T081)."""
    conversation_errors: list[str] = []
    """Malformed entries of the agent's structured blocks — observable, never dropped silently."""
    produced_at: datetime = Field(default_factory=utc_now)

    @field_validator("status")
    @classmethod
    def _status_is_a_result(cls, value: StageStatus) -> StageStatus:
        if value not in _RESULT_STATUSES:
            allowed = ", ".join(sorted(status.value for status in _RESULT_STATUSES))
            raise ValueError(f"StageResult.status must be one of: {allowed}; got {value.value!r}")
        return value


def completion_violations(run: ChangeRun, stage_results: Sequence[StageResult]) -> list[str]:
    """Invariants gating a successful terminal status; empty list means completion is allowed.

    - every required evidence must be available (ADR-009 p.9);
    - no open blocker findings may remain (finalizer contract, hld-mvp 8).

    Non-successful runs are not checked: these invariants only gate success.
    """
    if run.status is not RunStatus.SUCCEEDED:
        return []
    violations: list[str] = []
    for result in stage_results:
        for item in result.evidence:
            if item.required and not item.available:
                violations.append(
                    f"required evidence {item.id!r} of stage {result.stage.value} is unavailable"
                )
        for finding in result.findings:
            if finding.severity is FindingSeverity.BLOCKER and finding.status is FindingStatus.OPEN:
                violations.append(
                    f"open blocker finding {finding.id!r} of stage {result.stage.value}"
                )
    return violations
