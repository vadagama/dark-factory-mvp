"""Machine-checkable escalation conditions (T-016, ADR-018 p.5)."""

import pytest

from dark_factory.changes.enums import (
    BoundaryArea,
    ControlPoint,
    EscalationRule,
    Gate,
    RiskClass,
    Route,
    Stage,
)
from dark_factory.changes.escalations import EscalationViolation
from dark_factory.changes.findings import Decision
from dark_factory.changes.implementation_contract import ChangeScope
from dark_factory.orchestration.policy.escalation import (
    BoundaryChange,
    adr_proposal_violation,
    autonomy_budget_violation,
    boundary_change_violation,
    contract_entry_violation,
    escalation_stop_reason,
    gate_failure_violation,
    irreversible_operation_violation,
    requirements_violation,
    risk_escalation_violation,
    scope_exit_violation,
    ui_verification_violation,
)
from tests.changes_factories import make_contract, make_decision

_IN_SCOPE = ("src/app.py", "tests/app_test.py")


def _scope() -> ChangeScope:
    return ChangeScope(in_scope=_IN_SCOPE, out_of_scope=("infra/",))


# --- implementation_contract_unapproved (construction entry gate, T-016 DoD) ---


def test_missing_contract_escalates() -> None:
    violation = contract_entry_violation(None)
    assert violation is not None
    assert violation.rule is EscalationRule.IMPLEMENTATION_CONTRACT_UNAPPROVED
    assert "no implementation contract" in violation.reason
    assert violation.manual_assessment is False


def test_unapproved_contract_escalates() -> None:
    contract = make_contract().model_copy(update={"approval": None})
    violation = contract_entry_violation(contract)
    assert violation is not None
    assert violation.rule is EscalationRule.IMPLEMENTATION_CONTRACT_UNAPPROVED
    assert "not approved" in violation.reason
    assert contract.id in violation.reason


def test_approved_contract_is_silent() -> None:
    assert contract_entry_violation(make_contract()) is None


# --- requirements_deficient ---


def test_conflicting_requirements_escalate() -> None:
    violation = requirements_violation(["AC-1 and AC-2 contradict", "target users undefined"])
    assert violation is not None
    assert violation.rule is EscalationRule.REQUIREMENTS_DEFICIENT
    assert "AC-1 and AC-2 contradict" in violation.reason


def test_consistent_requirements_are_silent() -> None:
    assert requirements_violation([]) is None


# --- scope_exit ---


def test_items_outside_scope_escalate() -> None:
    violation = scope_exit_violation(["src/app.py", "infra/main.tf"], _scope())
    assert violation is not None
    assert violation.rule is EscalationRule.SCOPE_EXIT
    assert "infra/main.tf" in violation.reason
    assert "src/app.py" not in violation.reason


def test_items_inside_scope_are_silent() -> None:
    assert scope_exit_violation(_IN_SCOPE, _scope()) is None


# --- boundary_change (public API / data schema / IAM / architecture boundary) ---


def test_uncovered_boundary_change_escalates() -> None:
    change = BoundaryChange(area=BoundaryArea.PUBLIC_API)
    violation = boundary_change_violation(change, make_contract())
    assert violation is not None
    assert violation.rule is EscalationRule.BOUNDARY_CHANGE
    assert "not provided for by the approved implementation contract" in violation.reason


def test_missing_contract_escalates_any_boundary_change() -> None:
    violation = boundary_change_violation(BoundaryChange(area=BoundaryArea.DATA_SCHEMA), None)
    assert violation is not None
    assert violation.rule is EscalationRule.BOUNDARY_CHANGE


def test_incompatible_covered_change_without_migration_plan_escalates() -> None:
    contract = make_contract().model_copy(
        update={"allowed_boundaries": (BoundaryArea.DATA_SCHEMA,)}
    )
    violation = boundary_change_violation(
        BoundaryChange(area=BoundaryArea.DATA_SCHEMA, compatible=False), contract
    )
    assert violation is not None
    assert "incompatible" in violation.reason
    assert "migration plan" in violation.reason


def test_covered_compatible_change_is_silent() -> None:
    contract = make_contract().model_copy(update={"allowed_boundaries": (BoundaryArea.PUBLIC_API,)})
    assert boundary_change_violation(BoundaryChange(area=BoundaryArea.PUBLIC_API), contract) is None


def test_covered_incompatible_change_with_migration_plan_is_silent() -> None:
    contract = make_contract().model_copy(update={"allowed_boundaries": (BoundaryArea.IAM,)})
    change = BoundaryChange(area=BoundaryArea.IAM, compatible=False, migration_plan_approved=True)
    assert boundary_change_violation(change, contract) is None


# --- new_adr_proposal ---


def test_adr_proposal_escalates() -> None:
    violation = adr_proposal_violation("introduce an event bus between stages")
    assert violation is not None
    assert violation.rule is EscalationRule.NEW_ADR_PROPOSAL
    assert "event bus" in violation.reason


def test_no_adr_proposal_is_silent() -> None:
    assert adr_proposal_violation(None) is None


# --- risk_raised_to_r2 (machine-checked class obligations, T-080, ADR-023 p.5) ---


def _planning_approval(*, commit_sha: str | None = None) -> Decision:
    """Approved human decision on the planning gate (the ``solution`` control point)."""
    return make_decision().model_copy(update={"commit_sha": commit_sha})


@pytest.mark.parametrize("risk_class", [RiskClass.R0, RiskClass.R1])
def test_risk_below_r2_is_silent(risk_class: RiskClass) -> None:
    violation = risk_escalation_violation(
        risk_class=risk_class, route=Route.QUICK, stage=Stage.PLANNING
    )
    assert violation is None


@pytest.mark.parametrize("risk_class", [RiskClass.R2, RiskClass.R3, RiskClass.R4])
def test_risk_raise_to_r2_or_higher_is_a_machine_check(risk_class: RiskClass) -> None:
    """The raise is a gate, not a manual assessment: it carries machine diagnostics."""
    violation = risk_escalation_violation(
        risk_class=risk_class, route=Route.QUICK, stage=Stage.PLANNING
    )
    assert violation is not None
    assert violation.rule is EscalationRule.RISK_RAISED_TO_R2
    assert violation.manual_assessment is False
    assert risk_class.value in violation.reason


def test_violation_names_every_unmet_obligation() -> None:
    violation = risk_escalation_violation(
        risk_class=RiskClass.R2, route=Route.QUICK, stage=Stage.PLANNING
    )
    assert violation is not None
    assert Route.QUICK.value in violation.reason
    assert ControlPoint.SOLUTION.value in violation.reason


def test_a_missing_control_point_alone_escalates() -> None:
    violation = risk_escalation_violation(
        risk_class=RiskClass.R2, route=Route.STANDARD, stage=Stage.PLANNING
    )
    assert violation is not None
    assert ControlPoint.SOLUTION.value in violation.reason
    assert "does not allow" not in violation.reason


def test_fulfilled_obligations_are_silent() -> None:
    violation = risk_escalation_violation(
        risk_class=RiskClass.R2,
        route=Route.STANDARD,
        stage=Stage.PLANNING,
        decisions=[_planning_approval()],
    )
    assert violation is None


def test_an_approval_on_another_gate_does_not_fulfil_the_obligation() -> None:
    other_gate = make_decision().model_copy(update={"gate": Gate.UI})
    violation = risk_escalation_violation(
        risk_class=RiskClass.R2,
        route=Route.STANDARD,
        stage=Stage.PLANNING,
        decisions=[other_gate],
    )
    assert violation is not None
    assert ControlPoint.SOLUTION.value in violation.reason


def test_an_approval_at_an_older_sha_does_not_fulfil_the_obligation() -> None:
    violation = risk_escalation_violation(
        risk_class=RiskClass.R2,
        route=Route.STANDARD,
        stage=Stage.PLANNING,
        decisions=[_planning_approval(commit_sha="aaa111")],
        sha="731ac91",
    )
    assert violation is not None
    assert ControlPoint.SOLUTION.value in violation.reason


# --- unrecoverable_gate_failure ---


def test_non_retryable_policy_violation_escalates() -> None:
    violation = gate_failure_violation(
        policy_violations=["license policy forbids dependency X"],
        rework_budget_exhausted=False,
    )
    assert violation is not None
    assert violation.rule is EscalationRule.UNRECOVERABLE_GATE_FAILURE
    assert "non-retryable" in violation.reason


def test_exhausted_rework_budget_with_failing_gates_escalates() -> None:
    violation = gate_failure_violation(
        policy_violations=[],
        rework_budget_exhausted=True,
    )
    assert violation is not None
    assert violation.rule is EscalationRule.UNRECOVERABLE_GATE_FAILURE
    assert "rework budget exhausted" in violation.reason


def test_fixable_gate_failure_stays_in_rework_cycle() -> None:
    assert gate_failure_violation(policy_violations=[], rework_budget_exhausted=False) is None


# --- autonomy_budget_exhausted ---


def test_autonomy_budget_exhaustion_escalates() -> None:
    violation = autonomy_budget_violation(make_contract(), iterations_used=5)
    assert violation is not None
    assert violation.rule is EscalationRule.AUTONOMY_BUDGET_EXHAUSTED
    assert "5/5" in violation.reason


def test_autonomy_budget_headroom_is_silent() -> None:
    assert autonomy_budget_violation(make_contract(), iterations_used=4) is None


def test_missing_contract_has_no_autonomy_budget() -> None:
    assert autonomy_budget_violation(None, iterations_used=100) is None


# --- ui_unverifiable ---


def test_unverifiable_ui_escalates() -> None:
    violation = ui_verification_violation(
        auto_verifiable=False, detail="no golden screenshot for the dialog"
    )
    assert violation is not None
    assert violation.rule is EscalationRule.UI_UNVERIFIABLE
    assert "golden screenshot" in violation.reason


def test_unverifiable_ui_without_detail_uses_default_diagnostics() -> None:
    violation = ui_verification_violation(auto_verifiable=False)
    assert violation is not None
    assert "cannot be confirmed automatically" in violation.reason


def test_verifiable_ui_is_silent() -> None:
    assert ui_verification_violation(auto_verifiable=True) is None


# --- irreversible_operation ---


def test_irreversible_operation_escalates() -> None:
    violation = irreversible_operation_violation("drop the legacy table")
    assert violation is not None
    assert violation.rule is EscalationRule.IRREVERSIBLE_OPERATION
    assert "drop the legacy table" in violation.reason


def test_no_irreversible_operation_is_silent() -> None:
    assert irreversible_operation_violation(None) is None


# --- joint stop reason ---


def test_stop_reason_joins_declared_escalations() -> None:
    violations = [
        EscalationViolation(rule=EscalationRule.SCOPE_EXIT, reason="left the scope"),
        EscalationViolation(rule=EscalationRule.UI_UNVERIFIABLE, reason="no comparison"),
    ]
    reason = escalation_stop_reason(violations)
    assert reason is not None
    assert "scope_exit: left the scope" in reason
    assert "ui_unverifiable: no comparison" in reason
    assert "; " in reason


def test_stop_reason_without_escalations_is_none() -> None:
    assert escalation_stop_reason([]) is None
