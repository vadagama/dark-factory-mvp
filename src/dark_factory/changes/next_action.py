"""NextAction: the closed discriminated union returned by every stage (ADR-005 p.2).

Variant names follow hld-mvp 8: execute_stage, wait_for_input, wait_for_ci,
rework, request_approval, merge, release, stop. The union is closed: stage
handlers (T-004) must cover every variant and end with ``assert_never``.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from dark_factory.changes.enums import Gate, Role, Stage, StopOutcome
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
    | RequestApprovalAction
    | MergeAction
    | ReleaseAction
    | StopAction,
    Field(discriminator="type"),
]
