"""Behavior of the flow engine across all NextAction variants, limits and gates (T-004)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from dark_factory.changes.enums import (
    ChangeRequestStatus,
    EvidenceType,
    FindingOrigin,
    FindingSeverity,
    FindingStatus,
    Gate,
    GateStatus,
    Provider,
    Role,
    Route,
    RunStatus,
    Stage,
    StageStatus,
    StopOutcome,
)
from dark_factory.changes.findings import Finding, GateResult
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
from dark_factory.changes.refs import ChangeRequestRef, Evidence, RepositoryRef
from dark_factory.changes.run import ChangeRun, StageResult
from dark_factory.changes.usage import BudgetSnapshot, Usage
from dark_factory.flows.routes import STAGE_SEQUENCE, route_profile
from dark_factory.orchestration.flow import (
    FlowStateError,
    InvalidFlowTransition,
    apply_result,
    expected_result_status,
)
from dark_factory.rules.gates import required_gates
from tests.changes_factories import make_run

NOW = datetime(2026, 9, 13, 12, 0, 0, tzinfo=UTC)


def _change_request() -> ChangeRequestRef:
    return ChangeRequestRef(
        repository=RepositoryRef(provider=Provider.GITHUB, slug="small/pilot"),
        number=12,
        status=ChangeRequestStatus.OPEN,
    )


def _result(
    stage: Stage,
    action: NextAction,
    *,
    status: StageStatus | None = None,
    attempt_number: int = 1,
    gates: list[GateResult] | None = None,
    usage: Usage | None = None,
    evidence: list[Evidence] | None = None,
    findings: list[Finding] | None = None,
) -> StageResult:
    return StageResult(
        stage=stage,
        run_id="run-001",
        change_id="chg-001",
        attempt_number=attempt_number,
        status=status if status is not None else expected_result_status(action),
        next_action=action,
        gate_results=gates if gates is not None else [],
        usage=usage,
        evidence=evidence if evidence is not None else [],
        findings=findings if findings is not None else [],
    )


def _passing_gates(stage: Stage, route: Route, **statuses: GateStatus) -> list[GateResult]:
    return [
        GateResult(gate=gate, status=statuses.get(gate, GateStatus.PASSED), sha="731ac91")
        for gate in sorted(required_gates(route, stage), key=lambda g: g.value)
    ]


def _advance_to(run: ChangeRun, target: Stage) -> None:
    """Complete every stage before ``target`` so its stage run becomes active."""
    prior = STAGE_SEQUENCE[: STAGE_SEQUENCE.index(target)]
    if not prior:
        return
    for stage, following in zip(prior, [*prior[1:], target], strict=True):
        apply_result(
            run,
            _result(
                stage,
                ExecuteStageAction(next_stage=following),
                gates=_passing_gates(stage, run.route),
            ),
        )


def test_full_standard_route_reaches_success() -> None:
    run = make_run(route=Route.STANDARD)
    steps: list[tuple[Stage, NextAction]] = [
        (Stage.SPECIFICATION, ExecuteStageAction(next_stage=Stage.PLANNING)),
        (Stage.PLANNING, ExecuteStageAction(next_stage=Stage.CONSTRUCTION)),
        (Stage.CONSTRUCTION, ExecuteStageAction(next_stage=Stage.REVIEW_VERIFICATION)),
        (Stage.REVIEW_VERIFICATION, MergeAction(change_request=_change_request())),
        (Stage.RELEASE, ReleaseAction()),
    ]
    for stage, action in steps:
        decision = apply_result(
            run, _result(stage, action, gates=_passing_gates(stage, Route.STANDARD))
        )
        assert decision.action == action
    assert run.status == RunStatus.SUCCEEDED
    assert run.finished_at is not None
    assert [s.stage for s in run.stages] == list(Stage)
    assert all(s.status == StageStatus.SUCCEEDED for s in run.stages)


def test_usage_is_accumulated_across_stages() -> None:
    run = make_run(route=Route.STANDARD)
    apply_result(
        run,
        _result(
            Stage.SPECIFICATION,
            ExecuteStageAction(next_stage=Stage.PLANNING),
            gates=_passing_gates(Stage.SPECIFICATION, Route.STANDARD),
            usage=Usage(prompt_tokens=30, completion_tokens=20),
        ),
    )
    assert run.budget.tokens_used == 50
    apply_result(
        run,
        _result(
            Stage.PLANNING,
            ExecuteStageAction(next_stage=Stage.CONSTRUCTION),
            gates=_passing_gates(Stage.PLANNING, Route.STANDARD),
            usage=Usage(
                prompt_tokens=10, completion_tokens=0, total_tokens=25, cost=Decimal("0.5")
            ),
        ),
    )
    assert run.budget.tokens_used == 75
    assert run.budget.cost_used == Decimal("0.5")


@pytest.mark.parametrize(
    ("action", "stage"),
    [
        (WaitForInputAction(reason="requirements need clarification"), Stage.SPECIFICATION),
        (
            WaitForCIAction(reason="pipeline running", change_request=_change_request()),
            Stage.CONSTRUCTION,
        ),
        (
            RequestApprovalAction(gate=Gate.PLANNING, requested_from=Role.PRODUCT),
            Stage.SPECIFICATION,
        ),
    ],
    ids=["wait_for_input", "wait_for_ci", "request_approval"],
)
def test_waiting_actions_hold_stage_and_run(action: NextAction, stage: Stage) -> None:
    run = make_run(route=Route.STANDARD)
    _advance_to(run, stage)
    decision = apply_result(run, _result(stage, action))
    assert decision.run_status == RunStatus.WAITING
    assert decision.stage_status == StageStatus.WAITING
    assert decision.next_stage is None

    # Resuming in the same attempt completes the stage (waiting -> succeeded).
    next_stage = route_profile(run.route).next_stage(stage)
    assert next_stage is not None
    resume = _result(
        stage,
        ExecuteStageAction(next_stage=next_stage),
        gates=_passing_gates(stage, run.route),
    )
    decision = apply_result(run, resume)
    assert decision.run_status == RunStatus.RUNNING
    assert decision.stage_status == StageStatus.SUCCEEDED
    assert decision.next_stage == next_stage


def test_rework_round_counts_budget_and_reenters_construction() -> None:
    run = make_run(route=Route.STANDARD)
    for stage, action in [
        (Stage.SPECIFICATION, ExecuteStageAction(next_stage=Stage.PLANNING)),
        (Stage.PLANNING, ExecuteStageAction(next_stage=Stage.CONSTRUCTION)),
        (Stage.CONSTRUCTION, ExecuteStageAction(next_stage=Stage.REVIEW_VERIFICATION)),
    ]:
        apply_result(run, _result(stage, action, gates=_passing_gates(stage, Route.STANDARD)))

    decision = apply_result(
        run,
        _result(
            Stage.REVIEW_VERIFICATION,
            ReworkAction(round=1, max_rounds=3, reason="blocker findings"),
            gates=_passing_gates(Stage.REVIEW_VERIFICATION, Route.STANDARD),
        ),
    )
    assert decision.action.type == "rework"
    assert decision.next_stage == Stage.CONSTRUCTION
    assert decision.run_status == RunStatus.RUNNING
    assert run.budget.used_rework_rounds == 1
    # The review attempt ended as failed; a fresh construction run was queued.
    review_runs = [s for s in run.stages if s.stage == Stage.REVIEW_VERIFICATION]
    assert review_runs[0].status == StageStatus.FAILED
    construction_runs = [s for s in run.stages if s.stage == Stage.CONSTRUCTION]
    assert [s.status for s in construction_runs] == [
        StageStatus.SUCCEEDED,
        StageStatus.PENDING,
    ]
    assert construction_runs[1].id == "run-001:construction:2"


def test_rework_limit_blocks_deterministically() -> None:
    run = make_run(route=Route.STANDARD)
    run.budget = BudgetSnapshot(max_rework_rounds=1)
    first = apply_result(
        run,
        _result(
            Stage.SPECIFICATION,
            ReworkAction(round=1, max_rounds=1, reason="first round"),
        ),
    )
    assert first.run_status == RunStatus.RUNNING
    assert run.budget.used_rework_rounds == 1

    decision = apply_result(
        run,
        _result(
            Stage.SPECIFICATION,
            ReworkAction(round=2, max_rounds=1, reason="second round"),
        ),
    )
    assert decision.action.type == "stop"
    assert decision.action.outcome == StopOutcome.BLOCKED
    assert decision.run_status == RunStatus.BLOCKED
    assert decision.stage_status == StageStatus.BLOCKED


def test_rework_beyond_limit_is_rejected_upfront() -> None:
    run = make_run(route=Route.STANDARD)
    decision = apply_result(
        run,
        _result(Stage.SPECIFICATION, ReworkAction(round=4, max_rounds=3, reason="too far")),
    )
    assert decision.action.type == "stop"
    assert run.status == RunStatus.BLOCKED
    assert run.budget.used_rework_rounds == 0


def test_exhausted_token_budget_blocks_advance() -> None:
    run = make_run(route=Route.STANDARD)
    run.budget = BudgetSnapshot(token_budget=100, tokens_used=100)
    decision = apply_result(
        run,
        _result(
            Stage.SPECIFICATION,
            ExecuteStageAction(next_stage=Stage.PLANNING),
            gates=_passing_gates(Stage.SPECIFICATION, Route.STANDARD),
        ),
    )
    assert decision.action.type == "stop"
    assert "token budget exhausted" in decision.action.reason
    assert decision.run_status == RunStatus.BLOCKED


def test_exhausted_cost_budget_blocks_advance() -> None:
    run = make_run(route=Route.STANDARD)
    run.budget = BudgetSnapshot(cost_budget=Decimal("10"), cost_used=Decimal("10"))
    decision = apply_result(
        run,
        _result(
            Stage.SPECIFICATION,
            ExecuteStageAction(next_stage=Stage.PLANNING),
            gates=_passing_gates(Stage.SPECIFICATION, Route.STANDARD),
        ),
    )
    assert decision.action.type == "stop"
    assert "cost budget exhausted" in decision.action.reason


def test_passed_deadline_blocks_advance() -> None:
    run = make_run(route=Route.STANDARD)
    run.budget = BudgetSnapshot(deadline=NOW - timedelta(hours=1))
    result = _result(
        Stage.SPECIFICATION,
        ExecuteStageAction(next_stage=Stage.PLANNING),
        gates=_passing_gates(Stage.SPECIFICATION, Route.STANDARD),
    )
    decision = apply_result(run, result, now=NOW)
    assert decision.action.type == "stop"
    assert decision.run_status == RunStatus.BLOCKED

    fresh = make_run(route=Route.STANDARD)
    fresh.budget = BudgetSnapshot(deadline=NOW + timedelta(hours=1))
    decision = apply_result(fresh, result, now=NOW)
    assert decision.action.type == "execute_stage"


def test_stage_usage_may_trigger_budget_stop_by_itself() -> None:
    run = make_run(route=Route.STANDARD)
    run.budget = BudgetSnapshot(token_budget=100, tokens_used=90)
    decision = apply_result(
        run,
        _result(
            Stage.SPECIFICATION,
            ExecuteStageAction(next_stage=Stage.PLANNING),
            gates=_passing_gates(Stage.SPECIFICATION, Route.STANDARD),
            usage=Usage(total_tokens=20),
        ),
    )
    assert decision.action.type == "stop"
    assert run.budget.tokens_used == 110


def test_failed_gate_blocks_advance() -> None:
    run = make_run(route=Route.STANDARD)
    decision = apply_result(
        run,
        _result(
            Stage.CONSTRUCTION,
            ExecuteStageAction(next_stage=Stage.REVIEW_VERIFICATION),
            gates=_passing_gates(
                Stage.CONSTRUCTION, Route.STANDARD, **{Gate.UI.value: GateStatus.FAILED}
            ),
        ),
    )
    assert decision.action.type == "stop"
    assert "ui" in decision.action.reason
    assert decision.run_status == RunStatus.BLOCKED


def test_missing_gate_result_blocks_advance() -> None:
    run = make_run(route=Route.STANDARD)
    decision = apply_result(
        run,
        _result(
            Stage.CONSTRUCTION,
            ExecuteStageAction(next_stage=Stage.REVIEW_VERIFICATION),
            gates=[
                GateResult(gate=Gate.CODE, status=GateStatus.PASSED, sha="731ac91")
            ],  # ui result absent
        ),
    )
    assert decision.action.type == "stop"


def test_skipped_gate_satisfies_requirement() -> None:
    run = make_run(route=Route.STANDARD)
    decision = apply_result(
        run,
        _result(
            Stage.CONSTRUCTION,
            ExecuteStageAction(next_stage=Stage.REVIEW_VERIFICATION),
            gates=_passing_gates(
                Stage.CONSTRUCTION, Route.STANDARD, **{Gate.UI.value: GateStatus.SKIPPED}
            ),
        ),
    )
    assert decision.action.type == "execute_stage"


def test_quick_route_does_not_require_ui_gate() -> None:
    run = make_run(route=Route.QUICK)
    decision = apply_result(
        run,
        _result(
            Stage.CONSTRUCTION,
            ExecuteStageAction(next_stage=Stage.REVIEW_VERIFICATION),
            gates=[GateResult(gate=Gate.CODE, status=GateStatus.PASSED, sha="731ac91")],
        ),
    )
    assert decision.action.type == "execute_stage"


def test_merge_advances_to_release_stage() -> None:
    run = make_run(route=Route.STANDARD)
    decision = apply_result(
        run,
        _result(
            Stage.REVIEW_VERIFICATION,
            MergeAction(change_request=_change_request()),
            gates=_passing_gates(Stage.REVIEW_VERIFICATION, Route.STANDARD),
        ),
    )
    assert decision.next_stage == Stage.RELEASE
    assert decision.stage_status == StageStatus.SUCCEEDED
    assert run.status == RunStatus.RUNNING
    assert run.stages[-1].stage == Stage.RELEASE
    assert run.stages[-1].status == StageStatus.PENDING


def test_release_success_requires_available_required_evidence() -> None:
    run = make_run(route=Route.STANDARD)
    blocked = apply_result(
        run,
        _result(
            Stage.RELEASE,
            ReleaseAction(),
            gates=_passing_gates(Stage.RELEASE, Route.STANDARD),
            evidence=[
                Evidence(
                    id="ev-smoke",
                    type=EvidenceType.SMOKE,
                    uri="https://ci.example/smoke/1",
                    required=True,
                    available=False,
                )
            ],
        ),
    )
    assert blocked.action.type == "stop"
    assert run.status == RunStatus.BLOCKED

    fresh = make_run(route=Route.STANDARD)
    ok = apply_result(
        fresh,
        _result(
            Stage.RELEASE,
            ReleaseAction(),
            gates=_passing_gates(Stage.RELEASE, Route.STANDARD),
            evidence=[
                Evidence(
                    id="ev-smoke",
                    type=EvidenceType.SMOKE,
                    uri="https://ci.example/smoke/1",
                    required=True,
                    available=True,
                )
            ],
        ),
    )
    assert ok.action.type == "release"
    assert fresh.status == RunStatus.SUCCEEDED


@pytest.mark.parametrize(
    ("outcome", "run_status", "stage_status", "finished"),
    [
        (StopOutcome.BLOCKED, RunStatus.BLOCKED, StageStatus.BLOCKED, False),
        (StopOutcome.FAILED, RunStatus.FAILED, StageStatus.FAILED, True),
    ],
    ids=["blocked", "failed"],
)
def test_stop_action_terminalizes(
    outcome: StopOutcome, run_status: RunStatus, stage_status: StageStatus, finished: bool
) -> None:
    run = make_run(route=Route.STANDARD)
    decision = apply_result(
        run, _result(Stage.SPECIFICATION, StopAction(outcome=outcome, reason="escalation"))
    )
    assert decision.action.type == "stop"
    assert run.status == run_status
    assert decision.stage_status == stage_status
    # finished_at is set for terminal run statuses only: a blocked run may be
    # resumed, so it has not finished yet (T-003 status tables).
    assert (run.finished_at is not None) is finished


def test_canceled_stop_cannot_be_carried_by_a_stage_result() -> None:
    """Result statuses (T-003) have no canceled value: cancellation is applied by
    the runner directly via ChangeRun.apply_status, not through the flow table."""
    with pytest.raises(ValidationError):
        StageResult(
            stage=Stage.SPECIFICATION,
            run_id="run-001",
            change_id="chg-001",
            status=StageStatus.CANCELED,
            next_action=StopAction(outcome=StopOutcome.CANCELED, reason="escalation"),
        )


def test_terminal_run_rejects_results() -> None:
    run = make_run(route=Route.STANDARD)
    apply_result(
        run,
        _result(
            Stage.SPECIFICATION,
            ExecuteStageAction(next_stage=Stage.PLANNING),
            gates=_passing_gates(Stage.SPECIFICATION, Route.STANDARD),
        ),
    )
    run.apply_status(RunStatus.CANCELED)
    with pytest.raises(FlowStateError):
        apply_result(run, _result(Stage.PLANNING, WaitForInputAction(reason="any")))


def test_wrong_execute_stage_target_is_rejected() -> None:
    run = make_run(route=Route.STANDARD)
    with pytest.raises(InvalidFlowTransition):
        apply_result(
            run,
            _result(
                Stage.SPECIFICATION,
                ExecuteStageAction(next_stage=Stage.RELEASE),
            ),
        )


def test_stale_attempt_is_rejected() -> None:
    run = make_run(route=Route.STANDARD)
    apply_result(run, _result(Stage.SPECIFICATION, WaitForInputAction(reason="waiting")))
    with pytest.raises(FlowStateError):
        apply_result(
            run,
            _result(
                Stage.SPECIFICATION,
                ExecuteStageAction(next_stage=Stage.PLANNING),
                attempt_number=2,
            ),
        )


def test_result_status_inconsistent_with_action_is_rejected() -> None:
    run = make_run(route=Route.STANDARD)
    with pytest.raises(FlowStateError):
        apply_result(
            run,
            _result(
                Stage.SPECIFICATION,
                ExecuteStageAction(next_stage=Stage.PLANNING),
                status=StageStatus.WAITING,
            ),
        )


def test_open_blocker_finding_blocks_release_success() -> None:
    run = make_run(route=Route.STANDARD)
    decision = apply_result(
        run,
        _result(
            Stage.RELEASE,
            ReleaseAction(),
            gates=_passing_gates(Stage.RELEASE, Route.STANDARD),
            findings=[
                Finding(
                    id="f-1",
                    origin=FindingOrigin.AGENT,
                    role=Role.QUALITY,
                    severity=FindingSeverity.BLOCKER,
                    status=FindingStatus.OPEN,
                )
            ],
        ),
    )
    assert decision.action.type == "stop"
    assert "blocker" in decision.action.reason
