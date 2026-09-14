"""Normalized contract axes as data (T-016; the gate itself is T-021)."""

from dark_factory.changes.enums import RiskClass
from dark_factory.context.sdd.models import (
    Delta,
    SupersedeOperation,
    TaskGraph,
)
from dark_factory.context.sdd.normalized import normalize
from dark_factory.context.sdd.strictness import ArtifactSlot, WorkflowProfile
from tests.sdd_factories import (
    SDD_REVISION,
    SDD_TARGET,
    make_change_set,
    make_manifest,
    make_reconciliation_plan,
)


def test_clean_change_set_has_empty_axes() -> None:
    normalized = normalize(make_change_set())
    assert normalized.completeness == ()
    assert normalized.consistency == ()
    assert normalized.policy == ()
    assert normalized.coverage == ()
    assert normalized.evidence == ()


def test_completeness_reports_missing_required_slots() -> None:
    manifest = make_manifest()
    artifacts = dict(manifest.artifacts)
    del artifacts[ArtifactSlot.TASKS]
    normalized = normalize(make_change_set(manifest=make_manifest(artifacts=artifacts)))
    codes = [finding.code for finding in normalized.completeness]
    assert codes == ["missing_required_artifact"]
    assert "tasks" in normalized.completeness[0].message


def test_bugfix_profile_needs_nothing() -> None:
    manifest = make_manifest(
        workflow={"profile": WorkflowProfile.BUGFIX_R0, "version": "1.0"},
        artifacts={},
    )
    normalized = normalize(
        make_change_set(
            manifest=manifest,
            delta=None,
            tasks=None,
            verification=None,
            evidence=None,
            documents=[],
        )
    )
    assert normalized.completeness == ()


def test_consistency_reports_baseline_revision_mismatch() -> None:
    normalized = normalize(make_change_set(delta=Delta(baseline_revision="other", operations=[])))
    codes = [finding.code for finding in normalized.consistency]
    assert "baseline_revision_mismatch" in codes


def test_consistency_reports_duplicate_targets() -> None:
    from dark_factory.context.sdd.models import AddOperation

    delta = Delta(
        baseline_revision=SDD_REVISION,
        operations=[
            AddOperation(target=SDD_TARGET, artifact="requirements/a.md"),
            AddOperation(target=SDD_TARGET, artifact="requirements/b.md"),
        ],
    )
    normalized = normalize(make_change_set(delta=delta))
    assert [f.code for f in normalized.consistency].count("duplicate_delta_target") == 1


def test_consistency_reports_self_supersede() -> None:
    delta = Delta(
        baseline_revision=SDD_REVISION,
        operations=[SupersedeOperation(target=SDD_TARGET, superseded_by=SDD_TARGET)],
    )
    normalized = normalize(make_change_set(delta=delta))
    assert [f.code for f in normalized.consistency] == ["self_supersede"]


def test_consistency_reports_declared_but_absent_artifacts() -> None:
    normalized = normalize(make_change_set(tasks=None))
    codes = [finding.code for finding in normalized.consistency]
    assert "declared_artifact_missing" in codes


def test_policy_requires_reconciliation_plan_for_high_risk() -> None:
    manifest = make_manifest(risk_class=RiskClass.R2)
    without = normalize(make_change_set(manifest=manifest))
    assert [f.code for f in without.policy] == ["reconciliation_required_for_high_risk"]
    with_plan = normalize(
        make_change_set(manifest=manifest, reconciliation=make_reconciliation_plan())
    )
    assert with_plan.policy == ()


def test_coverage_requires_task_and_verification_per_requirement() -> None:
    from dark_factory.context.sdd.models import AddOperation

    delta = Delta(
        baseline_revision=SDD_REVISION,
        operations=[
            AddOperation(target=SDD_TARGET, artifact="requirements/REQ-001-timeout.md"),
            AddOperation(target="req:pilot:billing:invoice", artifact="requirements/REQ-002.md"),
        ],
    )
    normalized = normalize(make_change_set(delta=delta))
    codes = [finding.code for finding in normalized.coverage]
    assert codes.count("requirement_without_task") == 1
    assert codes.count("requirement_without_verification") == 1
    assert all("req:pilot:billing:invoice" in f.message for f in normalized.coverage)


def test_coverage_ignores_non_requirement_targets() -> None:
    from dark_factory.context.sdd.models import AddOperation

    delta = Delta(
        baseline_revision=SDD_REVISION,
        operations=[
            AddOperation(target="api:pilot:reservation", artifact="contracts/openapi.yaml")
        ],
    )
    normalized = normalize(make_change_set(delta=delta))
    assert normalized.coverage == ()


def test_coverage_without_tasks_at_all() -> None:
    normalized = normalize(make_change_set(tasks=TaskGraph(tasks=[])))
    codes = [finding.code for finding in normalized.coverage]
    assert codes.count("requirement_without_task") == 1


def test_evidence_flags_unavailable_required_entry() -> None:
    from dark_factory.context.sdd.models import EvidenceEntry, EvidenceIndex, EvidenceResult

    evidence = EvidenceIndex(
        evidence=[
            EvidenceEntry(
                id="EVD-001",
                type="test-report",
                uri="s3://x/report.xml",
                result=EvidenceResult.PASSED,
                required=True,
                available=False,
            )
        ]
    )
    normalized = normalize(make_change_set(evidence=evidence))
    assert [f.code for f in normalized.evidence] == ["required_evidence_unavailable"]


def test_evidence_expected_for_accepted_changes() -> None:
    from dark_factory.context.sdd.lifecycle import ChangeSetStatus

    manifest = make_manifest(status=ChangeSetStatus.ACCEPTED)
    normalized = normalize(make_change_set(manifest=manifest, evidence=None))
    assert [f.code for f in normalized.evidence] == ["missing_evidence_for_accepted_change"]
