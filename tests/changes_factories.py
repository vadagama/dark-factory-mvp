"""Shared builders for the change domain tests (T-003)."""

from datetime import UTC, datetime
from decimal import Decimal

from dark_factory.changes.enums import (
    ChangeRequestStatus,
    ChangeSource,
    DecisionOutcome,
    DecisionSource,
    EvidenceType,
    FindingOrigin,
    FindingSeverity,
    FindingStatus,
    Gate,
    GateStatus,
    Provider,
    RiskClass,
    Role,
    Route,
    RunStatus,
    Stage,
    StageStatus,
)
from dark_factory.changes.findings import Decision, Finding, GateResult
from dark_factory.changes.implementation_contract import (
    AcceptanceCriterion,
    ChangeScope,
    ContractApproval,
    ContractBudget,
    ImplementationContract,
)
from dark_factory.changes.next_action import ExecuteStageAction, NextAction
from dark_factory.changes.refs import ChangeRequestRef, Evidence, RepositoryRef
from dark_factory.changes.run import Change, ChangeRun, StageResult
from dark_factory.changes.run_records import RunManifest, RunRecord
from dark_factory.changes.usage import BudgetSnapshot

NOW = datetime(2026, 9, 13, 12, 0, 0, tzinfo=UTC)


def make_repository() -> RepositoryRef:
    return RepositoryRef(provider=Provider.GITHUB, slug="small/pilot")


def make_change_request() -> ChangeRequestRef:
    return ChangeRequestRef(
        repository=make_repository(),
        number=12,
        url="https://github.com/small/pilot/pull/12",
        status=ChangeRequestStatus.OPEN,
    )


def make_change() -> Change:
    return Change(
        id="chg-001",
        title="Add export button",
        source=ChangeSource.TRACKER,
        external_ref="PLANE-42",
        product=make_repository(),
        risk_class=RiskClass.R1,
        created_at=NOW,
    )


def make_contract() -> ImplementationContract:
    """Approved contract a factory run needs to enter construction (T-016)."""
    return ImplementationContract(
        id="ict-001",
        scope=ChangeScope(in_scope=("src/app.py",)),
        acceptance_criteria=(
            AcceptanceCriterion(id="ac-1", description="the export button renders"),
        ),
        risk_class=RiskClass.R1,
        budget=ContractBudget(max_autonomous_iterations=5),
        approval=ContractApproval(approved_by=Role.PRODUCT, decided_at=NOW),
    )


def make_run(route: Route = Route.STANDARD) -> ChangeRun:
    return ChangeRun(
        id="run-001",
        change_id="chg-001",
        route=route,
        provider=Provider.GITHUB,
        created_at=NOW,
        updated_at=NOW,
        implementation_contract=make_contract(),
    )


def make_manifest() -> RunManifest:
    return RunManifest(
        factory_version="0.1.0",
        factory_commit="4f86c2a",
        pack_name="backend-fastapi",
        pack_version="1.4.0",
        blueprint_version="2.1.0",
        product_commit="731ac91",
        gitops_commit="ca31c10",
    )


def make_evidence(
    evidence_id: str = "ev-1",
    *,
    required: bool = False,
    available: bool = True,
) -> Evidence:
    return Evidence(
        id=evidence_id,
        type=EvidenceType.REPORT,
        uri="https://ci.example/artifacts/1",
        checksum="sha256:abc123",
        produced_at=NOW,
        required=required,
        available=available,
    )


def make_finding(
    finding_id: str = "f-1",
    *,
    severity: FindingSeverity = FindingSeverity.MINOR,
    status: FindingStatus = FindingStatus.OPEN,
) -> Finding:
    return Finding(
        id=finding_id,
        origin=FindingOrigin.AGENT,
        role=Role.QUALITY,
        severity=severity,
        category="tests",
        file="src/app.py",
        line=10,
        reviewed_sha="731ac91",
        required_action="fix the failing test",
        status=status,
    )


def make_gate_result() -> GateResult:
    return GateResult(
        gate=Gate.CODE,
        status=GateStatus.PASSED,
        sha="731ac91",
        evidence_ids=["ev-1"],
    )


def make_decision() -> Decision:
    return Decision(
        id="dec-1",
        gate=Gate.PLANNING,
        outcome=DecisionOutcome.APPROVED,
        decided_by=DecisionSource.HUMAN,
        role=Role.PRODUCT,
        decided_at=NOW,
        comment="approved",
    )


def make_merge_approval(sha: str = "731ac91") -> Decision:
    """Human approval of the merge gate, bound to a SHA (version-bound, ADR-009 p.7)."""
    return Decision(
        id="dec-merge-1",
        gate=Gate.REVIEW,
        outcome=DecisionOutcome.APPROVED,
        decided_by=DecisionSource.HUMAN,
        role=Role.QUALITY,
        decided_at=NOW,
        commit_sha=sha,
        comment="approved for merge",
    )


def make_stage_result(
    next_action: NextAction | None = None,
    *,
    status: StageStatus = StageStatus.SUCCEEDED,
    evidence: list[Evidence] | None = None,
    findings: list[Finding] | None = None,
    gate_results: list[GateResult] | None = None,
) -> StageResult:
    if next_action is None:
        next_action = ExecuteStageAction(next_stage=Stage.REVIEW_VERIFICATION)
    return StageResult(
        stage=Stage.CONSTRUCTION,
        run_id="run-001",
        change_id="chg-001",
        input_revision="731ac91",
        status=status,
        next_action=next_action,
        evidence=evidence if evidence is not None else [],
        findings=findings if findings is not None else [],
        gate_results=gate_results if gate_results is not None else [],
        produced_at=NOW,
    )


def make_record(
    run_status: RunStatus = RunStatus.SUCCEEDED,
    stage_results: list[StageResult] | None = None,
) -> RunRecord:
    """Build a run record; ``run_status`` is set directly, without transition validation."""
    run = make_run()
    run.status = run_status
    run.budget = BudgetSnapshot(
        max_rework_rounds=3,
        used_rework_rounds=1,
        token_budget=100_000,
        tokens_used=500,
        cost_budget=Decimal("12.5"),
        cost_used=Decimal("0.75"),
        deadline=NOW,
    )
    return RunRecord(
        manifest=make_manifest(),
        change=make_change(),
        run=run,
        stage_results=stage_results if stage_results is not None else [],
    )
