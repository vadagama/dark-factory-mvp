"""Exhaustive coverage of the Factory Flow transition table (T-004 DoD, ADR-005 p.2).

The three flow guarantees are checked here:

- the table is complete and consistent with the closed ``NextAction`` union;
- every ``(stage, NextAction)`` pair is either in the table or rejected by the
  single transition point (``apply_result``) at runtime — a transition outside
  the table is impossible;
- no dead edges: every table entry starts in a stage reachable from the
  route's initial stage, and every ``NextAction`` variant is used by the table.
"""

from typing import assert_never, get_args

import pytest

from dark_factory.changes.enums import (
    ChangeRequestStatus,
    Gate,
    GateStatus,
    Provider,
    Route,
    Stage,
    StageStatus,
    StopOutcome,
)
from dark_factory.changes.findings import GateResult
from dark_factory.changes.next_action import (
    ExecuteStageAction,
    MergeAction,
    NextAction,
    ReleaseAction,
    RequestApprovalAction,
    ReworkAction,
    StopAction,
    WaitForCIAction,
    WaitForInputAction,
)
from dark_factory.changes.refs import ChangeRequestRef, RepositoryRef
from dark_factory.changes.run import StageResult
from dark_factory.flows.routes import route_profile
from dark_factory.orchestration.flow import (
    FLOW_TRANSITIONS,
    REWORK_TARGET,
    InvalidFlowTransition,
    NextActionType,
    allowed_actions,
    apply_result,
    expected_result_status,
)
from dark_factory.rules.gates import required_gates
from tests.changes_factories import make_run

UNION_DISCRIMINATORS: frozenset[str] = frozenset(
    model.model_fields["type"].default for model in get_args(get_args(NextAction)[0])
)
"""Discriminators of the closed union, derived from the models themselves."""

ALL_STAGES: list[Stage] = list(Stage)
# PEP 695 aliases are not unwrapped by ``get_args``: the literal members live
# on ``__value__``. Without this, the pair traversal below would be empty.
ALL_ACTION_TYPES: list[NextActionType] = sorted(get_args(NextActionType.__value__))
PAIRS: list[tuple[Stage, NextActionType]] = [
    (stage, action_type) for stage in ALL_STAGES for action_type in ALL_ACTION_TYPES
]
PAIR_IDS: list[str] = [f"{stage.value}:{action_type}" for stage, action_type in PAIRS]

_APPROVAL_GATES: dict[Stage, Gate] = {
    Stage.SPECIFICATION: Gate.SPECIFICATION,
    Stage.PLANNING: Gate.PLANNING,
    Stage.CONSTRUCTION: Gate.CODE,
    Stage.REVIEW_VERIFICATION: Gate.REVIEW,
    Stage.RELEASE: Gate.RELEASE,
}


def _change_request() -> ChangeRequestRef:
    return ChangeRequestRef(
        repository=RepositoryRef(provider=Provider.GITHUB, slug="small/pilot"),
        number=12,
        status=ChangeRequestStatus.OPEN,
    )


def _action_for(stage: Stage, action_type: NextActionType, route: Route) -> NextAction:
    """Build the action payload for one (stage, action type) pair."""
    match action_type:
        case "execute_stage":
            next_stage = route_profile(route).next_stage(stage)
            if next_stage is None:
                # Out-of-table pair (last stage has no route successor): the
                # payload is irrelevant, the table check rejects the pair
                # before the target stage is validated.
                return ExecuteStageAction(next_stage=stage)
            return ExecuteStageAction(next_stage=next_stage)
        case "wait_for_input":
            return WaitForInputAction(reason="waiting for human input")
        case "wait_for_ci":
            return WaitForCIAction(reason="pipeline running against the change request")
        case "rework":
            return ReworkAction(round=1, max_rounds=3, reason="blocker findings")
        case "request_approval":
            return RequestApprovalAction(gate=_APPROVAL_GATES[stage])
        case "merge":
            return MergeAction(change_request=_change_request())
        case "release":
            return ReleaseAction()
        case "stop":
            return StopAction(outcome=StopOutcome.BLOCKED, reason="stop condition")
        case _:
            assert_never(action_type)


def _happy_result(stage: Stage, action_type: NextActionType, route: Route) -> StageResult:
    """A stage result the engine honors: gates passed, default budget, no findings."""
    action = _action_for(stage, action_type, route)
    return StageResult(
        stage=stage,
        run_id="run-001",
        change_id="chg-001",
        status=expected_result_status(action),
        next_action=action,
        gate_results=[
            GateResult(gate=gate, status=GateStatus.PASSED, sha="731ac91")
            for gate in sorted(required_gates(route, stage), key=lambda g: g.value)
        ],
    )


def test_next_action_type_alias_matches_closed_union() -> None:
    """The Literal alias and the table can only talk about real union members."""
    assert frozenset(get_args(NextActionType.__value__)) == UNION_DISCRIMINATORS


def test_table_covers_every_stage_with_nonempty_action_sets() -> None:
    assert set(FLOW_TRANSITIONS) == set(Stage)
    assert all(len(actions) > 0 for actions in FLOW_TRANSITIONS.values())


def test_table_actions_are_valid_union_discriminators() -> None:
    for stage, actions in FLOW_TRANSITIONS.items():
        assert actions <= UNION_DISCRIMINATORS, f"unknown action at {stage.value}"


def test_every_next_action_variant_is_used_by_the_table() -> None:
    """No dead action class: each of the 8 variants is allowed somewhere."""
    used: set[str] = set()
    for actions in FLOW_TRANSITIONS.values():
        used |= actions
    assert used == UNION_DISCRIMINATORS


@pytest.mark.parametrize(("stage", "action_type"), PAIRS, ids=PAIR_IDS)
def test_every_stage_action_pair_is_table_governed(
    stage: Stage, action_type: NextActionType
) -> None:
    """Full traversal of state x NextAction: out-of-table pairs cannot be applied."""
    run = make_run(route=Route.STANDARD)
    result = _happy_result(stage, action_type, Route.STANDARD)
    if action_type in allowed_actions(stage):
        decision = apply_result(run, result)
        assert decision.action.type == action_type
        assert run.status == decision.run_status
    else:
        with pytest.raises(InvalidFlowTransition):
            apply_result(run, result)


def _successors(stage: Stage, action_type: NextActionType, route: Route) -> set[Stage]:
    """Flow-level successor states of one table edge (for reachability)."""
    profile = route_profile(route)
    match action_type:
        case "execute_stage" | "merge":
            target = profile.next_stage(stage)
            return {target} if target is not None else set()
        case "wait_for_input" | "wait_for_ci" | "request_approval":
            return {stage}  # the stage waits, then resumes in place
        case "rework":
            return {REWORK_TARGET[stage]}
        case "release" | "stop":
            return set()  # terminal
        case _:
            assert_never(action_type)


def _reachable_stages(route: Route) -> set[Stage]:
    profile = route_profile(route)
    seen = {profile.initial_stage}
    frontier = [profile.initial_stage]
    while frontier:
        stage = frontier.pop()
        for action_type in sorted(allowed_actions(stage)):
            for target in _successors(stage, action_type, route):
                if target not in seen:
                    seen.add(target)
                    frontier.append(target)
    return seen


@pytest.mark.parametrize("route", list(Route), ids=[route.value for route in Route])
def test_no_dead_edges(route: Route) -> None:
    """Every table edge starts in a stage reachable from the route's initial stage."""
    reachable = _reachable_stages(route)
    assert set(FLOW_TRANSITIONS) <= reachable, (
        "unreachable stages in the transition table make their edges dead"
    )
    for stage, actions in FLOW_TRANSITIONS.items():
        for action_type in sorted(actions):
            assert action_type in allowed_actions(stage)


def test_honored_execute_stage_succeeds_the_stage() -> None:
    run = make_run(route=Route.STANDARD)
    decision = apply_result(
        run, _happy_result(Stage.SPECIFICATION, "execute_stage", Route.STANDARD)
    )
    assert decision.stage_status == StageStatus.SUCCEEDED
    assert decision.next_stage == Stage.PLANNING
