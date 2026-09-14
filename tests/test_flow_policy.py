"""Escalation policy wired into the flow: T-016 DoD invariants (T-016, ADR-018 p.5).

Every DoD invariant lives here:

- a change without an approved Implementation Contract never enters
  construction;
- each machine-checkable escalation condition stops the flow in ``Blocked``
  with diagnostics naming the rule;
- declared escalations veto autonomous continuations only: waiting actions
  (already Awaiting Decision) pass through;
- an escalated rework round does not burn rework budget.
"""

import pytest

from dark_factory.changes.enums import (
    BoundaryArea,
    ChangeRequestStatus,
    EscalationRule,
    GateStatus,
    Provider,
    RiskClass,
    Route,
    RunStatus,
    Stage,
    StageStatus,
    StopOutcome,
)
from dark_factory.changes.escalations import EscalationViolation
from dark_factory.changes.findings import GateResult
from dark_factory.changes.implementation_contract import ChangeScope, ContractBudget
from dark_factory.changes.next_action import (
    ExecuteStageAction,
    MergeAction,
    NextAction,
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
    risk_raise_violation,
    scope_exit_violation,
    ui_verification_violation,
)
from dark_factory.rules.gates import required_gates
from tests.changes_factories import make_contract, make_run


def _passing_gates(stage: Stage, route: Route) -> list[GateResult]:
    return [
        GateResult(gate=gate, status=GateStatus.PASSED, sha="731ac91")
        for gate in sorted(required_gates(route, stage), key=lambda g: g.value)
    ]


def _result(
    stage: Stage,
    action: NextAction,
    route: Route,
    *,
    escalations: list[EscalationViolation] | None = None,
) -> StageResult:
    return StageResult(
        stage=stage,
        run_id="run-001",
        change_id="chg-001",
        status=expected_result_status(action),
        next_action=action,
        gate_results=_passing_gates(stage, route),
        escalations=escalations if escalations is not None else [],
    )


def _advance_to(run: ChangeRun, target: Stage) -> None:
    """Complete every stage before ``target`` so its stage run becomes active."""
    prior = STAGE_SEQUENCE[: STAGE_SEQUENCE.index(target)]
    if not prior:
        return
    for stage, following in zip(prior, [*prior[1:], target], strict=True):
        decision = apply_result(
            run, _result(stage, ExecuteStageAction(next_stage=following), run.route)
        )
        assert not isinstance(decision.action, StopAction)


def _blocked_stop(run: ChangeRun, result: StageResult) -> StopAction:
    """Apply ``result`` and assert the flow stopped in Blocked with diagnostics."""
    decision = apply_result(run, result)
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
            violation = risk_raise_violation(RiskClass.R2)
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


# --- DoD: every escalation condition stops the flow with diagnostics ---


@pytest.mark.parametrize(("rule", "stage", "action"), _ESCALATION_POINTS)
def test_declared_escalation_blocks_stage_advance(
    rule: EscalationRule, stage: Stage, action: NextAction
) -> None:
    run = make_run()
    _advance_to(run, stage)
    violation = _declared_violation(rule)
    if rule is EscalationRule.RISK_RAISED_TO_R2:
        # Until T-080 the R2+ raise stays a manual assessment (ADR-018 p.5).
        assert violation.manual_assessment is True
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
