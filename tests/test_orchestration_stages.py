"""Deterministic stage steps: context, checks, aggregation, decision (T010)."""

from datetime import timedelta
from decimal import Decimal

import pytest

from dark_factory.changes.enums import (
    Gate,
    GateStatus,
    Route,
    RunStatus,
    Stage,
    StageStatus,
    StopOutcome,
)
from dark_factory.changes.findings import GateResult
from dark_factory.changes.next_action import StopAction, WaitForInputAction
from dark_factory.changes.usage import BudgetSnapshot
from dark_factory.flows.routes import route_profile
from dark_factory.orchestration.flow import apply_result
from dark_factory.orchestration.stages import StageContext, build_context, run_deterministic_stage
from dark_factory.rules.gates import required_gates
from tests.changes_factories import NOW, make_change, make_change_request, make_run

RUN_ID = "run_01H"
REVISION = "a1b2c3d"


def _context(
    stage: Stage, route: Route = Route.STANDARD, *, budget: BudgetSnapshot | None = None
) -> StageContext:
    return build_context(
        change=make_change(),
        stage=stage,
        route=route,
        run_id=RUN_ID,
        input_revision=REVISION,
        budget=budget if budget is not None else BudgetSnapshot(),
    )


def test_gate_results_mirror_the_route_policy() -> None:
    """Gate applicability per route (ADR-005): pending records mirror required_gates."""
    for route in Route:
        for stage in route_profile(route).stages:
            result = run_deterministic_stage(_context(stage, route))
            assert result.gate_results == [
                GateResult(gate=gate, status=GateStatus.PENDING)
                for gate in sorted(required_gates(route, stage), key=lambda g: g.value)
            ]
    standard = run_deterministic_stage(_context(Stage.CONSTRUCTION, Route.STANDARD))
    assert [record.gate for record in standard.gate_results] == [Gate.CODE, Gate.UI]
    quick = run_deterministic_stage(_context(Stage.CONSTRUCTION, Route.QUICK))
    assert [record.gate for record in quick.gate_results] == [Gate.CODE]


def test_stage_waits_with_an_honest_reason() -> None:
    """No gate is evaluated on a product SHA, so the attempt cannot succeed (FR-009)."""
    result = run_deterministic_stage(_context(Stage.CONSTRUCTION))
    assert result.schema_version == 1
    assert result.status is StageStatus.WAITING
    assert isinstance(result.next_action, WaitForInputAction)
    assert "required gates not evaluated: code, ui" in result.next_action.reason
    assert result.attempt_number == 1
    assert result.run_id == RUN_ID
    assert result.change_id == "chg-001"
    assert result.input_revision == REVISION
    assert result.usage is None
    assert result.evidence == []
    assert result.findings == []


def test_missing_change_request_is_reported_on_review_verification() -> None:
    without = run_deterministic_stage(_context(Stage.REVIEW_VERIFICATION))
    assert isinstance(without.next_action, WaitForInputAction)
    assert "change request is not attached" in without.next_action.reason

    attached = make_change().model_copy(update={"change_request": make_change_request()})
    context = build_context(
        change=attached,
        stage=Stage.REVIEW_VERIFICATION,
        route=Route.STANDARD,
        run_id=RUN_ID,
        input_revision=REVISION,
        budget=BudgetSnapshot(),
    )
    with_request = run_deterministic_stage(context)
    assert isinstance(with_request.next_action, WaitForInputAction)
    assert "change request" not in with_request.next_action.reason


@pytest.mark.parametrize(
    ("budget", "fragment"),
    [
        (
            BudgetSnapshot(max_rework_rounds=3, used_rework_rounds=3),
            "rework limit exhausted: 3/3 rounds used",
        ),
        (BudgetSnapshot(token_budget=100, tokens_used=100), "token budget exhausted: 100/100"),
        (BudgetSnapshot(cost_budget=Decimal("5.00"), cost_used=Decimal("5.00")), "cost budget"),
        (BudgetSnapshot(deadline=NOW - timedelta(seconds=1)), "deadline"),
    ],
)
def test_exhausted_limits_block_the_attempt(budget: BudgetSnapshot, fragment: str) -> None:
    result = run_deterministic_stage(_context(Stage.PLANNING, budget=budget), now=NOW)
    assert result.status is StageStatus.BLOCKED
    assert isinstance(result.next_action, StopAction)
    assert result.next_action.outcome is StopOutcome.BLOCKED
    assert fragment in result.next_action.reason


def test_no_deterministic_path_claims_success() -> None:
    """Honest scope: without evaluated gates no stage reports succeeded/failed (FR-009)."""
    for route in Route:
        for stage in Stage:
            result = run_deterministic_stage(_context(stage, route))
            assert result.status in {StageStatus.WAITING, StageStatus.BLOCKED}


def test_results_are_accepted_by_the_flow_engine() -> None:
    """Executor output satisfies the (stage, NextAction, status) pairing of apply_result."""
    run = make_run(route=Route.STANDARD)
    decision = apply_result(run, run_deterministic_stage(_context(Stage.SPECIFICATION)))
    assert decision.stage_status is StageStatus.WAITING
    assert run.status is RunStatus.WAITING

    blocked_run = make_run(route=Route.STANDARD)
    blocked = run_deterministic_stage(
        _context(Stage.SPECIFICATION, budget=BudgetSnapshot(used_rework_rounds=3)), now=NOW
    )
    blocked_decision = apply_result(blocked_run, blocked)
    assert blocked_decision.stage_status is StageStatus.BLOCKED
    assert blocked_run.status is RunStatus.BLOCKED


def test_output_is_deterministic_for_fixed_inputs() -> None:
    first = run_deterministic_stage(_context(Stage.CONSTRUCTION), now=NOW)
    second = run_deterministic_stage(_context(Stage.CONSTRUCTION), now=NOW)
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
