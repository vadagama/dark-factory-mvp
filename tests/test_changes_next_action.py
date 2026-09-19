"""NextAction is a closed union: exhaustive handling is checked by mypy (ADR-005 p.2).

This module also documents the handler pattern that T-004 stage handlers must
use: cover every variant, then ``assert_never`` in the fallback branch. Adding a
variant without updating the handler fails typecheck.
"""

from typing import assert_never

import pytest

from dark_factory.changes.enums import (
    ChangeRequestStatus,
    Gate,
    Phase,
    Provider,
    Role,
    Stage,
    StopOutcome,
)
from dark_factory.changes.next_action import (
    ExecuteStageAction,
    MergeAction,
    NextAction,
    PhaseRoundAction,
    ReleaseAction,
    RequestApprovalAction,
    ReworkAction,
    StopAction,
    WaitForCIAction,
    WaitForInputAction,
)
from dark_factory.changes.refs import ChangeRequestRef, RepositoryRef


def _change_request() -> ChangeRequestRef:
    return ChangeRequestRef(
        repository=RepositoryRef(provider=Provider.GITHUB, slug="small/pilot"),
        number=12,
        status=ChangeRequestStatus.OPEN,
    )


def _describe(action: NextAction) -> str:
    """Handle every NextAction variant; the wildcard is unreachable by types."""
    match action:
        case ExecuteStageAction():
            return "execute_stage"
        case WaitForInputAction():
            return "wait_for_input"
        case WaitForCIAction():
            return "wait_for_ci"
        case ReworkAction():
            return "rework"
        case PhaseRoundAction():
            return "phase_round"
        case RequestApprovalAction():
            return "request_approval"
        case MergeAction():
            return "merge"
        case ReleaseAction():
            return "release"
        case StopAction():
            return "stop"
        case _:
            assert_never(action)


CASES: list[tuple[NextAction, str]] = [
    (ExecuteStageAction(next_stage=Stage.CONSTRUCTION), "execute_stage"),
    (WaitForInputAction(reason="need input"), "wait_for_input"),
    (WaitForCIAction(reason="ci running"), "wait_for_ci"),
    (ReworkAction(round=1, max_rounds=3, reason="findings"), "rework"),
    (PhaseRoundAction(phase=Phase.ARCHITECTURE, reason="requirements approved"), "phase_round"),
    (RequestApprovalAction(gate=Gate.RELEASE, requested_from=Role.OPERATION), "request_approval"),
    (MergeAction(change_request=_change_request()), "merge"),
    (ReleaseAction(), "release"),
    (StopAction(outcome=StopOutcome.FAILED, reason="attempts exhausted"), "stop"),
]
CASE_IDS = [kind for _, kind in CASES]


@pytest.mark.parametrize(("action", "kind"), CASES, ids=CASE_IDS)
def test_handler_covers_every_variant(action: NextAction, kind: str) -> None:
    assert _describe(action) == kind
