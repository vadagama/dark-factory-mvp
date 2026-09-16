"""Escalation and risk policy wired into the flow: T-016/T-080 DoD (ADR-018 p.5, ADR-023).

Every DoD invariant lives here:

- a change without an approved Implementation Contract never enters
  construction;
- the effective risk class (declared, derived facts, route floor) is checked
  against the route band on *every* advancing action — stage advance, merge and
  release: R2+ never takes the short route (T-080, ADR-023 p.3);
- the obligations of a R2+ class are produced by the engine itself: a stage whose
  control points lack a version-bound human approval stops in ``Blocked`` with
  ``risk_raised_to_r2`` naming what is missing (T-080, ADR-023 p.5);
- the facts observed on the contract overrule the declared class, so a change
  editing the trusted layer cannot travel as R1 (T-080, ADR-023 p.2/p.7);
- the route floor raises a low declared class instead of rejecting it
  (T-080, ADR-023 p.6);
- each machine-checkable escalation condition stops the flow in ``Blocked``
  with diagnostics naming the rule;
- declared escalations veto autonomous continuations only: waiting actions
  (already Awaiting Decision) pass through;
- an escalated rework round does not burn rework budget.
"""

from collections.abc import Sequence

import pytest

from dark_factory.changes.enums import (
    BoundaryArea,
    ChangeRequestStatus,
    ControlPoint,
    DecisionOutcome,
    DecisionSource,
    EscalationRule,
    Gate,
    GateStatus,
    Provider,
    RiskClass,
    Role,
    Route,
    RunStatus,
    Stage,
    StageStatus,
    StopOutcome,
)
from dark_factory.changes.escalations import EscalationViolation
from dark_factory.changes.findings import Decision, GateResult
from dark_factory.changes.implementation_contract import ChangeScope, ContractBudget
from dark_factory.changes.next_action import (
    ExecuteStageAction,
    MergeAction,
    NextAction,
    ReleaseAction,
    ReworkAction,
    StopAction,
    WaitForInputAction,
)
from dark_factory.changes.refs import ChangeRequestRef, RepositoryRef
from dark_factory.changes.run import ChangeRun, StageResult
from dark_factory.flows.routes import STAGE_SEQUENCE
from dark_factory.orchestration.flow import apply_result, expected_result_status
from dark_factory.orchestration.policy.escalation import (
    BoundaryChange,
    adr_proposal_violation,
    autonomy_budget_violation,
    boundary_change_violation,
    contract_entry_violation,
    gate_failure_violation,
    irreversible_operation_violation,
    requirements_violation,
    risk_escalation_violation,
    scope_exit_violation,
    ui_verification_violation,
)
from dark_factory.orchestration.policy.merge import MergeRequestContext
from dark_factory.rules.gates import required_gates
from tests.changes_factories import NOW, make_contract, make_merge_approval, make_run

SHA = "731ac91"
"""Revision the stage results of this suite are evaluated at."""


def _approval(gate: Gate, *, sha: str | None = None) -> Decision:
    """Human approval of ``gate``, optionally bound to a revision (ADR-009 p.7)."""
    return Decision(
        id=f"dec-{gate.value}",
        gate=gate,
        outcome=DecisionOutcome.APPROVED,
        decided_by=DecisionSource.HUMAN,
        role=Role.PRODUCT,
        decided_at=NOW,
        commit_sha=sha,
    )


def _merge_context(route: Route = Route.STANDARD) -> MergeRequestContext:
    """Merge facts of a run whose gates passed at ``SHA`` and a human approved (T-026)."""
    return MergeRequestContext(
        executor="human",
        risk_class=RiskClass.R1,
        route=route,
        stage=Stage.REVIEW_VERIFICATION,
        expected_sha=SHA,
        head_sha=SHA,
        human_approvals=(make_merge_approval(SHA),),
    )


def _passing_gates(stage: Stage, route: Route) -> list[GateResult]:
    return [
        GateResult(gate=gate, status=GateStatus.PASSED, sha=SHA)
        for gate in sorted(required_gates(route, stage), key=lambda g: g.value)
    ]


def _result(
    stage: Stage,
    action: NextAction,
    route: Route,
    *,
    escalations: list[EscalationViolation] | None = None,
    input_revision: str | None = None,
) -> StageResult:
    return StageResult(
        stage=stage,
        run_id="run-001",
        change_id="chg-001",
        input_revision=input_revision,
        status=expected_result_status(action),
        next_action=action,
        gate_results=_passing_gates(stage, route),
        escalations=escalations if escalations is not None else [],
    )


def _advance_to(run: ChangeRun, target: Stage, *, decisions: Sequence[Decision] = ()) -> None:
    """Complete every stage before ``target`` so its stage run becomes active."""
    prior = STAGE_SEQUENCE[: STAGE_SEQUENCE.index(target)]
    if not prior:
        return
    for stage, following in zip(prior, [*prior[1:], target], strict=True):
        decision = apply_result(
            run,
            _result(stage, ExecuteStageAction(next_stage=following), run.route),
            human_decisions=decisions,
        )
        assert not isinstance(decision.action, StopAction)


def _blocked_stop(
    run: ChangeRun,
    result: StageResult,
    *,
    merge_context: MergeRequestContext | None = None,
    human_decisions: Sequence[Decision] = (),
    history: Sequence[StageResult] = (),
) -> StopAction:
    """Apply ``result`` and assert the flow stopped in Blocked with diagnostics."""
    decision = apply_result(
        run,
        result,
        history=history,
        merge_context=merge_context,
        human_decisions=human_decisions,
    )
    stop = decision.action
    assert isinstance(stop, StopAction)
    assert stop.outcome is StopOutcome.BLOCKED
    assert stop.reason
    assert run.status is RunStatus.BLOCKED
    assert decision.run_status is RunStatus.BLOCKED
    assert decision.stage_status is StageStatus.BLOCKED
    assert decision.next_stage is None
    return stop


def _change_request() -> ChangeRequestRef:
    return ChangeRequestRef(
        repository=RepositoryRef(provider=Provider.GITHUB, slug="small/pilot"),
        number=12,
        status=ChangeRequestStatus.OPEN,
    )


def _declared_violation(rule: EscalationRule) -> EscalationViolation:
    """Build one violation per rule with the production checks (not hand-made).

    Covers the rules declared on stage advances; the other three
    (``implementation_contract_unapproved``, ``unrecoverable_gate_failure``,
    ``autonomy_budget_exhausted``) have dedicated flow tests below.
    """
    violation: EscalationViolation | None
    match rule:
        case EscalationRule.REQUIREMENTS_DEFICIENT:
            violation = requirements_violation(["AC-1 and AC-2 contradict"])
        case EscalationRule.SCOPE_EXIT:
            violation = scope_exit_violation(["infra/main.tf"], ChangeScope(in_scope=("src/",)))
        case EscalationRule.BOUNDARY_CHANGE:
            violation = boundary_change_violation(
                BoundaryChange(area=BoundaryArea.PUBLIC_API), make_contract()
            )
        case EscalationRule.NEW_ADR_PROPOSAL:
            violation = adr_proposal_violation("introduce an event bus between stages")
        case EscalationRule.RISK_RAISED_TO_R2:
            violation = risk_escalation_violation(
                risk_class=RiskClass.R2, route=Route.STANDARD, stage=Stage.PLANNING
            )
        case EscalationRule.UI_UNVERIFIABLE:
            violation = ui_verification_violation(
                auto_verifiable=False, detail="no golden screenshot"
            )
        case EscalationRule.IRREVERSIBLE_OPERATION:
            violation = irreversible_operation_violation("drop the legacy table")
        case _:
            raise AssertionError(f"rule {rule.value} has its own flow test")
    assert violation is not None
    return violation


# Points where an escalation is declared on a stage result; the rework case is
# covered separately because it flows through ReworkAction.
_ESCALATION_POINTS: list[tuple[EscalationRule, Stage, NextAction]] = [
    (rule, stage, ExecuteStageAction(next_stage=next_stage))
    for rule, stage, next_stage in [
        (EscalationRule.REQUIREMENTS_DEFICIENT, Stage.SPECIFICATION, Stage.PLANNING),
        (EscalationRule.SCOPE_EXIT, Stage.SPECIFICATION, Stage.PLANNING),
        (EscalationRule.BOUNDARY_CHANGE, Stage.PLANNING, Stage.CONSTRUCTION),
        (EscalationRule.NEW_ADR_PROPOSAL, Stage.PLANNING, Stage.CONSTRUCTION),
        (EscalationRule.RISK_RAISED_TO_R2, Stage.PLANNING, Stage.CONSTRUCTION),
        (EscalationRule.UI_UNVERIFIABLE, Stage.CONSTRUCTION, Stage.REVIEW_VERIFICATION),
        (EscalationRule.IRREVERSIBLE_OPERATION, Stage.CONSTRUCTION, Stage.REVIEW_VERIFICATION),
    ]
]


# --- DoD: no entry into construction without an approved contract ---


def test_change_without_contract_cannot_enter_construction() -> None:
    run = make_run()
    run.implementation_contract = None
    _advance_to(run, Stage.PLANNING)
    result = _result(Stage.PLANNING, ExecuteStageAction(next_stage=Stage.CONSTRUCTION), run.route)
    stop = _blocked_stop(run, result)
    violation = contract_entry_violation(None)
    assert violation is not None
    assert violation.reason == stop.reason


def test_unapproved_contract_cannot_enter_construction() -> None:
    run = make_run()
    run.implementation_contract = make_contract().model_copy(update={"approval": None})
    _advance_to(run, Stage.PLANNING)
    result = _result(Stage.PLANNING, ExecuteStageAction(next_stage=Stage.CONSTRUCTION), run.route)
    stop = _blocked_stop(run, result)
    assert "not approved" in stop.reason


def test_approved_contract_enters_construction_and_finishes_route() -> None:
    run = make_run()
    assert run.implementation_contract is not None
    _advance_to(run, Stage.CONSTRUCTION)
    active = run.stages[-1]
    assert active.stage is Stage.CONSTRUCTION
    assert run.status is RunStatus.RUNNING


# --- DoD: a risky change does not take the short route (T-080, ADR-023 p.3) ---


def _run_with_risk(route: Route, risk_class: RiskClass, **contract_updates: object) -> ChangeRun:
    """A run whose approved contract declares ``risk_class`` plus any observed facts."""
    run = make_run(route=route)
    run.implementation_contract = make_contract().model_copy(
        update={"risk_class": risk_class, **contract_updates}
    )
    return run


def test_r2_contract_blocks_the_first_advance_on_the_quick_route() -> None:
    run = _run_with_risk(Route.QUICK, RiskClass.R2)
    result = _result(Stage.SPECIFICATION, ExecuteStageAction(next_stage=Stage.PLANNING), run.route)
    stop = _blocked_stop(run, result)
    assert EscalationRule.RISK_RAISED_TO_R2.value in stop.reason
    assert Route.QUICK.value in stop.reason


def test_a_risk_raise_mid_run_blocks_the_next_advance_on_the_quick_route() -> None:
    """A route chosen before the raise cannot carry the raised class either (ADR-023 p.3)."""
    run = make_run(route=Route.QUICK)
    _advance_to(run, Stage.PLANNING)
    run.implementation_contract = make_contract().model_copy(update={"risk_class": RiskClass.R2})
    result = _result(Stage.PLANNING, ExecuteStageAction(next_stage=Stage.CONSTRUCTION), run.route)
    stop = _blocked_stop(run, result)
    assert RiskClass.R2.value in stop.reason


def test_the_same_risk_class_advances_on_the_standard_route() -> None:
    run = _run_with_risk(Route.STANDARD, RiskClass.R3)
    _advance_to(
        run,
        Stage.CONSTRUCTION,
        decisions=(_approval(Gate.SPECIFICATION), _approval(Gate.PLANNING)),
    )
    assert run.stages[-1].stage is Stage.CONSTRUCTION
    assert run.status is RunStatus.RUNNING


def test_low_risk_still_advances_on_the_quick_route() -> None:
    run = _run_with_risk(Route.QUICK, RiskClass.R1)
    _advance_to(run, Stage.PLANNING)
    assert run.stages[-1].stage is Stage.PLANNING


def test_an_unapproved_contract_is_reported_before_the_risk_band() -> None:
    """Without an approval the run carries no risk class: the entry gate fires first."""
    run = make_run(route=Route.QUICK)
    run.implementation_contract = make_contract().model_copy(
        update={"risk_class": RiskClass.R2, "approval": None}
    )
    _advance_to(run, Stage.PLANNING)
    result = _result(Stage.PLANNING, ExecuteStageAction(next_stage=Stage.CONSTRUCTION), run.route)
    stop = _blocked_stop(run, result)
    assert "not approved" in stop.reason
    assert RiskClass.R2.value not in stop.reason


# --- DoD: the band is checked on the merge and release paths too (ADR-023 p.3) ---


def test_an_r2_class_does_not_merge_on_the_quick_route() -> None:
    run = make_run(route=Route.QUICK)
    _advance_to(run, Stage.REVIEW_VERIFICATION)
    run.implementation_contract = make_contract().model_copy(update={"risk_class": RiskClass.R2})
    result = _result(
        Stage.REVIEW_VERIFICATION, MergeAction(change_request=_change_request()), run.route
    )
    stop = _blocked_stop(run, result, merge_context=_merge_context(Route.QUICK))
    assert RiskClass.R2.value in stop.reason
    assert Route.QUICK.value in stop.reason


def test_an_r2_class_does_not_leave_the_quick_route_through_release() -> None:
    run = make_run(route=Route.QUICK)
    _advance_to(run, Stage.REVIEW_VERIFICATION)
    merged = apply_result(
        run,
        _result(
            Stage.REVIEW_VERIFICATION, MergeAction(change_request=_change_request()), run.route
        ),
        merge_context=_merge_context(Route.QUICK),
    )
    assert not isinstance(merged.action, StopAction)
    assert run.stages[-1].stage is Stage.RELEASE
    run.implementation_contract = make_contract().model_copy(update={"risk_class": RiskClass.R2})
    result = _result(Stage.RELEASE, ReleaseAction(), run.route)
    stop = _blocked_stop(run, result)
    assert RiskClass.R2.value in stop.reason
    assert Route.QUICK.value in stop.reason


def test_an_r2_class_merges_on_the_standard_route_as_before() -> None:
    run = make_run()
    _advance_to(run, Stage.REVIEW_VERIFICATION)
    run.implementation_contract = make_contract().model_copy(update={"risk_class": RiskClass.R2})
    result = _result(
        Stage.REVIEW_VERIFICATION, MergeAction(change_request=_change_request()), run.route
    )
    decision = apply_result(run, result, merge_context=_merge_context())
    assert not isinstance(decision.action, StopAction)
    assert run.stages[-1].stage is Stage.RELEASE
    assert run.status is RunStatus.RUNNING


# --- DoD: the R2+ obligations are produced by the engine (ADR-023 p.5) ---


def test_r2_obligations_stop_the_advance_with_the_rule_and_the_missing_point() -> None:
    run = _run_with_risk(Route.STANDARD, RiskClass.R2)
    result = _result(Stage.SPECIFICATION, ExecuteStageAction(next_stage=Stage.PLANNING), run.route)
    stop = _blocked_stop(run, result)
    assert EscalationRule.RISK_RAISED_TO_R2.value in stop.reason
    assert ControlPoint.PROBLEM.value in stop.reason


def test_r2_obligations_are_met_by_a_human_approval_of_the_point() -> None:
    run = _run_with_risk(Route.STANDARD, RiskClass.R2)
    result = _result(Stage.SPECIFICATION, ExecuteStageAction(next_stage=Stage.PLANNING), run.route)
    decision = apply_result(run, result, human_decisions=(_approval(Gate.SPECIFICATION),))
    assert not isinstance(decision.action, StopAction)
    assert run.stages[-1].stage is Stage.PLANNING


def test_an_approval_bound_to_another_sha_does_not_meet_the_obligations() -> None:
    """Version-bound approval (ADR-009 p.7): the point needs the approval of *its* revision."""
    run = _run_with_risk(Route.STANDARD, RiskClass.R2)
    result = _result(
        Stage.SPECIFICATION,
        ExecuteStageAction(next_stage=Stage.PLANNING),
        run.route,
        input_revision="abc1234",
    )
    stop = _blocked_stop(
        run, result, human_decisions=(_approval(Gate.SPECIFICATION, sha="old9876"),)
    )
    assert ControlPoint.PROBLEM.value in stop.reason


def test_an_approval_of_another_gate_does_not_meet_the_obligations() -> None:
    run = _run_with_risk(Route.STANDARD, RiskClass.R2)
    result = _result(Stage.SPECIFICATION, ExecuteStageAction(next_stage=Stage.PLANNING), run.route)
    stop = _blocked_stop(run, result, human_decisions=(_approval(Gate.REVIEW),))
    assert ControlPoint.PROBLEM.value in stop.reason


def test_r0_and_r1_advance_without_any_human_decision() -> None:
    """R0/R1 keep the previous behaviour: no control point is demanded (T-080)."""
    for risk_class in (RiskClass.R0, RiskClass.R1):
        run = _run_with_risk(Route.STANDARD, risk_class)
        result = _result(
            Stage.SPECIFICATION, ExecuteStageAction(next_stage=Stage.PLANNING), run.route
        )
        decision = apply_result(run, result)
        assert not isinstance(decision.action, StopAction)


# --- DoD: the class is derived from the facts, not from the declaration (p.2/p.7) ---


def test_a_trusted_path_in_the_scope_is_raised_to_r4_off_the_quick_route() -> None:
    """A change editing the trusted layer may not travel as R1 on ``quick`` (ADR-015 p.3)."""
    run = _run_with_risk(
        Route.QUICK,
        RiskClass.R1,
        scope=ChangeScope(in_scope=("src/dark_factory/rules/gates.py",), out_of_scope=()),
    )
    result = _result(Stage.SPECIFICATION, ExecuteStageAction(next_stage=Stage.PLANNING), run.route)
    stop = _blocked_stop(run, result)
    assert RiskClass.R4.value in stop.reason
    assert Route.QUICK.value in stop.reason


def test_a_permitted_boundary_raises_a_r1_change_off_the_quick_route() -> None:
    run = _run_with_risk(Route.QUICK, RiskClass.R1, allowed_boundaries=(BoundaryArea.PUBLIC_API,))
    result = _result(Stage.SPECIFICATION, ExecuteStageAction(next_stage=Stage.PLANNING), run.route)
    stop = _blocked_stop(run, result)
    assert RiskClass.R2.value in stop.reason
    assert Route.QUICK.value in stop.reason


def test_the_declaration_cannot_hide_the_class_the_facts_derive() -> None:
    """On a wide route too: the derived R4 demands its control points before advancing."""
    run = _run_with_risk(
        Route.STANDARD,
        RiskClass.R1,
        scope=ChangeScope(
            in_scope=("src/dark_factory/orchestration/policy/risk.py",), out_of_scope=()
        ),
    )
    result = _result(Stage.SPECIFICATION, ExecuteStageAction(next_stage=Stage.PLANNING), run.route)
    stop = _blocked_stop(run, result)
    assert RiskClass.R4.value in stop.reason
    assert ControlPoint.PROBLEM.value in stop.reason


# --- DoD: the route floor raises a low declared class (ADR-023 p.6) ---


@pytest.mark.parametrize("route", [Route.ARCHITECTURE, Route.FOUNDATION])
def test_the_route_floor_raises_a_low_declared_class(route: Route) -> None:
    """The floor is part of the effective class: R1 on a stricter route demands its points.

    Without the floor the class would stay R1, the R2+ obligations would stay
    silent and the change would slip through the stricter route unnoticed.
    """
    run = _run_with_risk(route, RiskClass.R1)
    result = _result(Stage.SPECIFICATION, ExecuteStageAction(next_stage=Stage.PLANNING), run.route)
    stop = _blocked_stop(run, result)
    assert EscalationRule.RISK_RAISED_TO_R2.value in stop.reason
    assert ControlPoint.PROBLEM.value in stop.reason


def test_the_route_floor_raises_instead_of_refusing_the_change() -> None:
    """The raised class is satisfiable: with the approval the same advance goes through."""
    run = _run_with_risk(Route.FOUNDATION, RiskClass.R1)
    _advance_to(run, Stage.PLANNING, decisions=(_approval(Gate.SPECIFICATION),))
    assert run.stages[-1].stage is Stage.PLANNING
    assert run.status is RunStatus.RUNNING


def test_a_low_declared_class_on_foundation_reaches_release() -> None:
    """The band on the merge path is checked against the effective class too (ADR-023 p.6)."""
    run = _run_with_risk(Route.FOUNDATION, RiskClass.R1)
    _advance_to(
        run,
        Stage.REVIEW_VERIFICATION,
        decisions=(_approval(Gate.SPECIFICATION), _approval(Gate.PLANNING), _approval(Gate.UI)),
    )
    result = _result(
        Stage.REVIEW_VERIFICATION, MergeAction(change_request=_change_request()), run.route
    )
    decision = apply_result(run, result, merge_context=_merge_context(Route.FOUNDATION))
    assert not isinstance(decision.action, StopAction)
    assert run.stages[-1].stage is Stage.RELEASE


# --- DoD: every escalation condition stops the flow with diagnostics ---


@pytest.mark.parametrize(("rule", "stage", "action"), _ESCALATION_POINTS)
def test_declared_escalation_blocks_stage_advance(
    rule: EscalationRule, stage: Stage, action: NextAction
) -> None:
    run = make_run()
    _advance_to(run, stage)
    violation = _declared_violation(rule)
    if rule is EscalationRule.RISK_RAISED_TO_R2:
        # T-080: the R2+ raise is a machine check, never a manual assessment.
        assert violation.manual_assessment is False
    result = _result(stage, action, run.route, escalations=[violation])
    stop = _blocked_stop(run, result)
    assert rule.value in stop.reason
    assert _declared_violation(rule).reason in stop.reason


def test_declared_gate_failure_vetoes_rework_without_burning_a_round() -> None:
    run = make_run()
    _advance_to(run, Stage.REVIEW_VERIFICATION)
    violation = gate_failure_violation(
        policy_violations=["license policy forbids dependency X"],
        rework_budget_exhausted=False,
    )
    assert violation is not None
    result = _result(
        Stage.REVIEW_VERIFICATION,
        ReworkAction(round=1, max_rounds=3, reason="fix the findings"),
        run.route,
        escalations=[violation],
    )
    stop = _blocked_stop(run, result)
    assert EscalationRule.UNRECOVERABLE_GATE_FAILURE.value in stop.reason
    assert run.budget.used_rework_rounds == 0


def test_exhausted_autonomy_budget_blocks_next_advance() -> None:
    run = make_run()
    run.implementation_contract = make_contract().model_copy(
        update={"budget": ContractBudget(max_autonomous_iterations=2)}
    )
    _advance_to(run, Stage.PLANNING)
    result = _result(Stage.PLANNING, ExecuteStageAction(next_stage=Stage.CONSTRUCTION), run.route)
    stop = _blocked_stop(run, result)
    violation = autonomy_budget_violation(run.implementation_contract, iterations_used=2)
    assert violation is not None
    assert "2/2" in stop.reason
    assert violation.reason in stop.reason


def test_implementation_contract_unapproved_is_a_flow_rule() -> None:
    """The unapproved-contract rule is the T-016 gate, reused as an escalation."""
    approved = contract_entry_violation(make_contract())
    assert approved is None


# --- escalations veto autonomous continuations only ---


def test_merge_with_declared_escalation_is_blocked() -> None:
    run = make_run()
    _advance_to(run, Stage.REVIEW_VERIFICATION)
    violation = _declared_violation(EscalationRule.RISK_RAISED_TO_R2)
    result = _result(
        Stage.REVIEW_VERIFICATION,
        MergeAction(change_request=_change_request()),
        run.route,
        escalations=[violation],
    )
    stop = _blocked_stop(run, result)
    assert EscalationRule.RISK_RAISED_TO_R2.value in stop.reason


def test_declared_escalation_passes_through_waiting_action() -> None:
    """A waiting action is already Awaiting Decision: escalations do not veto it."""
    run = make_run()
    violation = _declared_violation(EscalationRule.REQUIREMENTS_DEFICIENT)
    result = _result(
        Stage.SPECIFICATION,
        WaitForInputAction(reason="requirements need clarification"),
        run.route,
        escalations=[violation],
    )
    decision = apply_result(run, result)
    assert isinstance(decision.action, WaitForInputAction)
    assert run.status is RunStatus.WAITING
    assert decision.stage_status is StageStatus.WAITING


def test_rework_limit_still_blocks_after_escalation_checks_pass() -> None:
    """The deterministic rework limit stays in force behind the policy checks."""
    run = make_run()
    run.budget.used_rework_rounds = run.budget.max_rework_rounds
    _advance_to(run, Stage.REVIEW_VERIFICATION)
    result = _result(
        Stage.REVIEW_VERIFICATION,
        ReworkAction(round=3, max_rounds=3, reason="one more fix"),
        run.route,
    )
    stop = _blocked_stop(run, result)
    assert "rework limit exhausted" in stop.reason
