"""Schema of the Implementation Contract and its carriers (T-016, ADR-018 p.3-4)."""

import pytest
from pydantic import ValidationError

from dark_factory.changes.enums import BoundaryArea, EscalationRule, RiskClass, Role
from dark_factory.changes.escalations import EscalationViolation
from dark_factory.changes.implementation_contract import (
    IMPLEMENTATION_CONTRACT_SCHEMA_VERSION,
    AcceptanceCriterion,
    ChangeScope,
    ContractApproval,
    ContractBudget,
    ImplementationContract,
)
from dark_factory.changes.next_action import ExecuteStageAction
from dark_factory.changes.run import ChangeRun, StageResult
from tests.changes_factories import NOW, make_contract, make_run


def _contract(
    *,
    allowed_boundaries: tuple[BoundaryArea, ...] = (),
) -> ImplementationContract:
    return ImplementationContract(
        id="ict-001",
        scope=ChangeScope(in_scope=("src/app.py",)),
        acceptance_criteria=(
            AcceptanceCriterion(id="ac-1", description="the export button renders"),
        ),
        risk_class=RiskClass.R1,
        budget=ContractBudget(max_autonomous_iterations=5),
        allowed_boundaries=allowed_boundaries,
        approval=ContractApproval(approved_by=Role.PRODUCT, decided_at=NOW),
    )


def test_contract_schema_version_is_pinned() -> None:
    contract = make_contract()
    assert contract.schema_version == 1
    assert contract.schema_version == IMPLEMENTATION_CONTRACT_SCHEMA_VERSION


def test_contract_carries_all_adr_components() -> None:
    contract = make_contract()
    # ADR-018 p.3: scope, acceptance criteria, architectural constraints, UI
    # evidence, risk class, budget, escalation rules.
    assert contract.scope.in_scope == ("src/app.py",)
    assert contract.acceptance_criteria
    assert contract.architectural_constraints == ()
    assert contract.ui_evidence == ()
    assert contract.risk_class is RiskClass.R1
    assert contract.budget.max_autonomous_iterations == 5
    assert contract.escalation_rules == tuple(EscalationRule)
    assert contract.approval is not None


def test_contract_is_frozen() -> None:
    contract = make_contract()
    with pytest.raises(ValidationError):
        contract.risk_class = RiskClass.R2  # type: ignore[misc]


def test_contract_variants_are_produced_by_copy_not_mutation() -> None:
    contract = make_contract()
    unapproved = contract.model_copy(update={"approval": None})
    assert unapproved.approval is None
    assert contract.approval is not None


def test_contract_requires_acceptance_criteria_and_scope() -> None:
    with pytest.raises(ValidationError):
        ImplementationContract(
            id="ict-002",
            scope=ChangeScope(in_scope=("src/app.py",)),
            acceptance_criteria=(),
            risk_class=RiskClass.R1,
            budget=ContractBudget(max_autonomous_iterations=5),
        )
    with pytest.raises(ValidationError):
        ImplementationContract(
            id="ict-003",
            scope=ChangeScope(in_scope=()),
            acceptance_criteria=(AcceptanceCriterion(id="ac-1", description="works"),),
            risk_class=RiskClass.R1,
            budget=ContractBudget(max_autonomous_iterations=5),
        )


def test_contract_budget_validates_bounds() -> None:
    with pytest.raises(ValidationError):
        ContractBudget(max_autonomous_iterations=0)
    budget = ContractBudget(max_autonomous_iterations=3, max_cost="12.50")
    assert budget.max_cost is not None


def test_allowed_boundaries_cover_protected_areas() -> None:
    contract = _contract(allowed_boundaries=(BoundaryArea.PUBLIC_API,))
    assert contract.allowed_boundaries == (BoundaryArea.PUBLIC_API,)


def test_change_run_contract_default_is_none() -> None:
    run = ChangeRun(
        id="run-009",
        change_id="chg-001",
        route="standard",
        provider="github",
    )
    assert run.implementation_contract is None
    assert make_run().implementation_contract is not None


def test_stage_result_escalations_default_is_empty() -> None:
    result = StageResult(
        stage="construction",
        run_id="run-001",
        change_id="chg-001",
        status="succeeded",
        next_action=ExecuteStageAction(next_stage="review_verification"),
    )
    assert result.escalations == []


def test_stage_result_escalations_roundtrip_through_json() -> None:
    violation = EscalationViolation(
        rule=EscalationRule.SCOPE_EXIT,
        reason="outside the approved scope",
    )
    result = StageResult(
        stage="construction",
        run_id="run-001",
        change_id="chg-001",
        status="succeeded",
        next_action=ExecuteStageAction(next_stage="review_verification"),
        escalations=[violation],
    )
    restored = StageResult.model_validate_json(result.model_dump_json())
    assert restored.escalations == [violation]
