"""NextAction: the closed discriminated union returned by every stage (ADR-005 p.2).

Variant names follow hld-mvp 8: execute_stage, wait_for_input, wait_for_ci,
rework, request_approval, merge, release, stop — plus ``phase_round`` (M3,
ADR-039): the next operator phase of the *same* stage. The union is closed:
stage handlers (T-004) must cover every variant and end with ``assert_never``.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from dark_factory.changes.enums import Gate, Phase, Role, Stage, StopOutcome
from dark_factory.changes.refs import ChangeRequestRef


class ExecuteStageAction(BaseModel):
    """Proceed to the next stage of the route."""

    type: Literal["execute_stage"] = "execute_stage"
    next_stage: Stage
    reason: str | None = None


class WaitForInputAction(BaseModel):
    """Wait for human input; the StageResult is persisted before the job ends (ADR-006 p.8)."""

    type: Literal["wait_for_input"] = "wait_for_input"
    reason: str


class WaitForCIAction(BaseModel):
    """Wait for an external CI pipeline running against the change request."""

    type: Literal["wait_for_ci"] = "wait_for_ci"
    reason: str
    change_request: ChangeRequestRef | None = None


class ReworkAction(BaseModel):
    """Rework round bounded by the rework limit (vision 3.1: at most 3 rounds)."""

    type: Literal["rework"] = "rework"
    round: int = Field(ge=1)
    max_rounds: int = Field(ge=1)
    reason: str


class PhaseRoundAction(BaseModel):
    """Re-enter the same stage for its next operator phase (ADR-039, ADR-032 p.3).

    The ``specification`` stage runs requirements → architecture → interface
    as *rounds* of one stage: the approval of a non-final phase ends the
    attempt and starts a new operation of the same stage at the revision the
    approval was bound to — without spending a rework round (a phase round is
    progress, not a send-back). ``phase`` is the phase the next round prepares.
    """

    type: Literal["phase_round"] = "phase_round"
    phase: Phase
    reason: str | None = None


class RequestApprovalAction(BaseModel):
    """Wait for a human approval on a gate (ADR-018)."""

    type: Literal["request_approval"] = "request_approval"
    gate: Gate
    requested_from: Role | None = None
    reason: str | None = None


class MergeAction(BaseModel):
    """Merge the change request per merge policy (ADR-011)."""

    type: Literal["merge"] = "merge"
    change_request: ChangeRequestRef
    reason: str | None = None


class ReleaseAction(BaseModel):
    """Proceed with release to the target environment (dev in MVP)."""

    type: Literal["release"] = "release"
    target_environment: str = "dev"
    reason: str | None = None


class StopAction(BaseModel):
    """Terminal stop: a stop condition was hit (T-004)."""

    type: Literal["stop"] = "stop"
    outcome: StopOutcome
    reason: str


NextAction = Annotated[
    ExecuteStageAction
    | WaitForInputAction
    | WaitForCIAction
    | ReworkAction
    | PhaseRoundAction
    | RequestApprovalAction
    | MergeAction
    | ReleaseAction
    | StopAction,
    Field(discriminator="type"),
]
