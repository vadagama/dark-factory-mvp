"""Specification gate: deterministic machine checks (T-021)."""

from pathlib import Path

import pytest
import yaml

from dark_factory.changes.enums import DecisionSource, Gate, GateStatus, RiskClass
from dark_factory.changes.implementation_contract import (
    AcceptanceCriterion,
    ChangeScope,
    ContractBudget,
    ImplementationContract,
)
from dark_factory.context.sdd.lifecycle import ChangeSetStatus
from dark_factory.context.sdd.models import (
    CheckSpec,
    EvidenceEntry,
    EvidenceIndex,
    EvidenceResult,
    RequirementVerification,
    TaskDef,
    TaskGraph,
    VerificationPlan,
)
from dark_factory.context.sdd.normalized import Axis, ChangeSet
from dark_factory.context.sdd.strictness import ArtifactSlot, WorkflowProfile
from dark_factory.quality.gates import (
    GATE_DECISION_SCHEMA,
    GateDecision,
    GateOverrideError,
    SpecGatePolicy,
    apply_override,
    evaluate_specification_gate,
    write_gate_decision,
)
from tests.sdd_factories import (
    SDD_CHANGE_ID,
    SDD_REVISION,
    SDD_TARGET,
    make_change_set,
    make_manifest,
    make_reconciliation_plan,
)


def make_gate_ready_change_set(**overrides: object) -> ChangeSet:
    """``make_change_set`` whose verification plan carries an acceptance-scenario check.

    The shared factory plans a unit test only; the gate additionally requires an
    acceptance-scenario check per touched requirement (T-021), so the clean
    cases build on this variant.
    """
    verification = VerificationPlan(
        requirements=[
            RequirementVerification(
                requirement=SDD_TARGET,
                checks=[CheckSpec(type="unit-test"), CheckSpec(type="acceptance-scenario")],
            )
        ]
    )
    overrides.setdefault("verification", verification)
    return make_change_set(**overrides)


def make_change_set_without_slot(slot: ArtifactSlot, **overrides: object) -> ChangeSet:
    """A gate-ready ChangeSet whose manifest does not declare ``slot``."""
    manifest = make_manifest()
    artifacts = dict(manifest.artifacts)
    del artifacts[slot]
    overrides.setdefault("manifest", make_manifest(artifacts=artifacts))
    return make_gate_ready_change_set(**overrides)


def make_contract(**overrides: object) -> ImplementationContract:
    values: dict[str, object] = {
        "id": "contract-001",
        "scope": ChangeScope(in_scope=("src/pilot",), out_of_scope=()),
        "acceptance_criteria": (AcceptanceCriterion(id="AC-1", description="timeout applies"),),
        "risk_class": RiskClass.R1,
        "budget": ContractBudget(max_autonomous_iterations=3),
    }
    values.update(overrides)
    return ImplementationContract.model_validate(values)


def test_clean_change_set_passes_by_policy() -> None:
    decision = evaluate_specification_gate(make_gate_ready_change_set())
    assert decision.result is GateStatus.PASSED
    assert decision.decided_by is DecisionSource.POLICY
    assert decision.gate is Gate.SPECIFICATION
    assert decision.override is None
    assert decision.findings == ()
    assert decision.risk_class is RiskClass.R1
    assert decision.changeset_id == SDD_CHANGE_ID
    assert decision.changeset_revision == SDD_REVISION
    assert "EVD-001" in decision.evidence
    assert "intent.md" in decision.evidence
    assert decision.explanation == "specification gate passed: no findings"


def test_matching_contract_keeps_effective_risk() -> None:
    changeset = make_gate_ready_change_set()
    decision = evaluate_specification_gate(changeset, make_contract(risk_class=RiskClass.R1))
    assert decision.result is GateStatus.PASSED
    assert decision.risk_class is RiskClass.R1
    assert decision.findings == ()


def test_missing_required_slot_fails_the_gate() -> None:
    decision = evaluate_specification_gate(make_change_set_without_slot(ArtifactSlot.TASKS))
    assert decision.result is GateStatus.FAILED
    missing = [
        finding for finding in decision.findings if finding.code == "missing_required_artifact"
    ]
    assert len(missing) == 1
    assert missing[0].blocking
    assert missing[0].axis is Axis.COMPLETENESS


def test_requirement_without_task_fails_the_gate() -> None:
    tasks = TaskGraph(tasks=[TaskDef(id="TASK-001", title="Implement the timeout", satisfies=[])])
    decision = evaluate_specification_gate(make_gate_ready_change_set(tasks=tasks))
    assert decision.result is GateStatus.FAILED
    finding = next(f for f in decision.findings if f.code == "requirement_without_task")
    assert finding.blocking
    assert finding.axis is Axis.COVERAGE


def test_requirement_without_acceptance_scenario_fails_the_gate() -> None:
    unit_only = make_change_set()  # the shared factory plans a unit test only
    empty_checks = make_change_set(
        verification=VerificationPlan(
            requirements=[RequirementVerification(requirement=SDD_TARGET, checks=[])]
        )
    )
    for changeset in (unit_only, empty_checks):
        decision = evaluate_specification_gate(changeset)
        assert decision.result is GateStatus.FAILED
        finding = next(
            f for f in decision.findings if f.code == "requirement_without_acceptance_scenario"
        )
        assert finding.blocking
        assert finding.axis is Axis.COMPLETENESS


def test_contract_lowering_risk_blocks_without_policy() -> None:
    manifest = make_manifest(risk_class=RiskClass.R2)
    changeset = make_gate_ready_change_set(
        manifest=manifest, reconciliation=make_reconciliation_plan()
    )
    decision = evaluate_specification_gate(changeset, make_contract(risk_class=RiskClass.R1))
    assert decision.result is GateStatus.FAILED
    assert decision.risk_class is RiskClass.R1
    # The completeness axis was recomputed by R1: the R2-only reconciliation
    # slot is no longer reported missing; the lowering itself blocks.
    assert [finding.code for finding in decision.findings] == ["risk_class_lowered_without_policy"]
    assert decision.findings[0].blocking
    assert decision.findings[0].axis is Axis.POLICY


def test_contract_raising_risk_requires_reconciliation_artifacts() -> None:
    changeset = make_gate_ready_change_set()  # manifest R1, no reconciliation
    decision = evaluate_specification_gate(changeset, make_contract(risk_class=RiskClass.R2))
    assert decision.result is GateStatus.FAILED
    assert decision.risk_class is RiskClass.R2
    blocking = {finding.code for finding in decision.findings if finding.blocking}
    assert blocking == {"missing_required_artifact", "reconciliation_required_for_high_risk"}
    raised = [finding for finding in decision.findings if finding.code == "risk_class_raised"]
    assert len(raised) == 1
    assert not raised[0].blocking


def test_contract_raising_risk_with_reconciliation_passes() -> None:
    artifacts = dict(make_manifest().artifacts)
    artifacts[ArtifactSlot.RECONCILIATION] = "reconciliation/plan.yaml"
    manifest = make_manifest(artifacts=artifacts)
    changeset = make_gate_ready_change_set(
        manifest=manifest, reconciliation=make_reconciliation_plan()
    )
    decision = evaluate_specification_gate(changeset, make_contract(risk_class=RiskClass.R2))
    assert decision.result is GateStatus.PASSED
    assert decision.risk_class is RiskClass.R2
    assert [(finding.code, finding.blocking) for finding in decision.findings] == [
        ("risk_class_raised", False)
    ]


def test_scope_contradiction_fails_the_gate() -> None:
    contract = make_contract(
        scope=ChangeScope(in_scope=("Pilot API ", "src/pilot"), out_of_scope=("pilot api",))
    )
    decision = evaluate_specification_gate(make_gate_ready_change_set(), contract)
    assert decision.result is GateStatus.FAILED
    contradictions = [
        finding for finding in decision.findings if finding.code == "scope_contradiction"
    ]
    assert len(contradictions) == 1
    assert contradictions[0].blocking
    assert contradictions[0].axis is Axis.CONSISTENCY
    assert "pilot api" in contradictions[0].message


def test_required_evidence_unavailable_fails_the_gate() -> None:
    evidence = EvidenceIndex(
        evidence=[
            EvidenceEntry(
                id="EVD-001",
                type="test-report",
                verifies=[SDD_TARGET],
                uri="s3://factory-evidence/report.xml",
                result=EvidenceResult.PASSED,
                required=True,
                available=False,
            )
        ]
    )
    decision = evaluate_specification_gate(make_gate_ready_change_set(evidence=evidence))
    assert decision.result is GateStatus.FAILED
    finding = next(f for f in decision.findings if f.code == "required_evidence_unavailable")
    assert finding.blocking
    assert finding.axis is Axis.EVIDENCE


def test_missing_evidence_for_accepted_change_is_non_blocking() -> None:
    manifest = make_manifest(
        status=ChangeSetStatus.ACCEPTED,
        kind=WorkflowProfile.BUGFIX_R0.value,
        workflow={"profile": WorkflowProfile.BUGFIX_R0, "version": "1.0"},
        artifacts={},
    )
    changeset = make_change_set(
        manifest=manifest,
        delta=None,
        documents=[],
        tasks=None,
        verification=None,
        evidence=None,
        reconciliation=None,
    )
    decision = evaluate_specification_gate(changeset)
    assert decision.result is GateStatus.PASSED
    finding = next(f for f in decision.findings if f.code == "missing_evidence_for_accepted_change")
    assert not finding.blocking
    assert decision.explanation == "specification gate passed: 1 non-blocking findings"


def test_override_requires_human_and_failed_decision() -> None:
    failed = evaluate_specification_gate(make_change_set_without_slot(ArtifactSlot.TASKS))
    overridden = apply_override(
        failed, decided_by=DecisionSource.HUMAN, reason="waived by release captain"
    )
    assert failed.override is None  # the original decision stays untouched
    assert overridden is not failed
    assert overridden.override is not None
    assert overridden.override.decided_by is DecisionSource.HUMAN
    assert overridden.override.reason == "waived by release captain"
    assert overridden.result is GateStatus.FAILED
    with pytest.raises(GateOverrideError):
        apply_override(failed, decided_by=DecisionSource.AGENT, reason="agent waiver")
    with pytest.raises(GateOverrideError):
        apply_override(failed, decided_by=DecisionSource.POLICY, reason="policy waiver")
    passed = evaluate_specification_gate(make_gate_ready_change_set())
    with pytest.raises(GateOverrideError):
        apply_override(passed, decided_by=DecisionSource.HUMAN, reason="why not")


def test_gate_decision_yaml_round_trip(tmp_path: Path) -> None:
    decision = evaluate_specification_gate(make_change_set_without_slot(ArtifactSlot.TASKS))
    write_gate_decision(tmp_path, decision)
    path = tmp_path / "gates" / "specification.yaml"
    assert path.is_file()
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["schema"] == GATE_DECISION_SCHEMA
    assert GateDecision.model_validate(data) == decision


def test_gate_decision_yaml_round_trip_with_override(tmp_path: Path) -> None:
    failed = evaluate_specification_gate(make_change_set_without_slot(ArtifactSlot.TASKS))
    decision = apply_override(
        failed, decided_by=DecisionSource.HUMAN, reason="waived by release captain"
    )
    write_gate_decision(tmp_path, decision)
    data = yaml.safe_load((tmp_path / "gates" / "specification.yaml").read_text(encoding="utf-8"))
    assert data["override"] == {"decided_by": "human", "reason": "waived by release captain"}
    assert GateDecision.model_validate(data) == decision


def test_policy_data_decides_blocking_classification() -> None:
    changeset = make_change_set_without_slot(ArtifactSlot.TASKS)
    lenient = evaluate_specification_gate(
        changeset, policy=SpecGatePolicy(blocking_codes=frozenset())
    )
    assert lenient.result is GateStatus.PASSED
    assert lenient.findings
    assert all(not finding.blocking for finding in lenient.findings)
    custom = SpecGatePolicy(name="spec-gate-strict", version="2.0", blocking_codes=frozenset())
    decision = evaluate_specification_gate(changeset, policy=custom)
    assert decision.policy == "spec-gate-strict"
    assert decision.policy_version == "2.0"


def test_decision_is_deterministic() -> None:
    changeset = make_gate_ready_change_set()
    contract = make_contract()
    assert evaluate_specification_gate(changeset, contract) == evaluate_specification_gate(
        changeset, contract
    )
    failed_changeset = make_change_set_without_slot(ArtifactSlot.TASKS)
    first = evaluate_specification_gate(failed_changeset)
    second = evaluate_specification_gate(failed_changeset)
    assert first == second
    assert (
        first.explanation
        == "specification gate failed: 1 blocking findings: missing_required_artifact"
    )
