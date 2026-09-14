"""Independent Quality acceptance: context, findings, review gate (T-013, FR-006, FR-007)."""

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from dark_factory.changes.enums import (
    ChangeRequestStatus,
    FindingOrigin,
    FindingSeverity,
    FindingStatus,
    Gate,
    GateStatus,
    Provider,
    RiskClass,
    Route,
    RunStatus,
    Stage,
    StopOutcome,
)
from dark_factory.changes.findings import Finding, GateResult
from dark_factory.changes.next_action import ExecuteStageAction, MergeAction, NextAction, StopAction
from dark_factory.changes.refs import ChangeRequestRef, RepositoryRef
from dark_factory.changes.run import ChangeRun, StageResult
from dark_factory.context.bundle import SourceKind
from dark_factory.flows.routes import STAGE_SEQUENCE
from dark_factory.orchestration.flow import apply_result, expected_result_status
from dark_factory.orchestration.policy.merge import MergeRequestContext
from dark_factory.quality.acceptance import (
    DiffMaterial,
    EvidenceMaterial,
    MrComment,
    SpecMaterial,
    build_acceptance_context,
    classify_findings,
    evaluate_review_gate,
    human_comment_findings,
)
from dark_factory.rules.gates import required_gates
from tests.changes_factories import make_finding, make_merge_approval, make_run

NOW = datetime(2026, 9, 13, 12, 0, 0, tzinfo=UTC)
SHA = "731ac91"


def _context(*, spec: SpecMaterial, diff: DiffMaterial, **kwargs: object) -> dict[str, object]:
    return {"change_id": "chg-001", "run_id": "run-001", "spec": spec, "diff": diff, **kwargs}


def _spec() -> SpecMaterial:
    return SpecMaterial(
        location=".factory/changes/2026/chg-001/spec/requirements",
        revision="9f31ab2",
        content="REQ-001: the export button renders",
    )


def _diff(content: str = "diff --git a/src/app.py b/src/app.py") -> DiffMaterial:
    return DiffMaterial(repository="small/pilot", sha=SHA, content=content)


def test_acceptance_context_is_reproducible() -> None:
    first = build_acceptance_context(
        change_id="chg-001", run_id="run-001", spec=_spec(), diff=_diff(), now=NOW
    )
    later = build_acceptance_context(
        change_id="chg-001",
        run_id="run-001",
        spec=_spec(),
        diff=_diff(),
        now=NOW + timedelta(hours=1),
    )
    assert first.bundle_hash == later.bundle_hash


def test_acceptance_context_records_provenance() -> None:
    evidence = EvidenceMaterial(
        evidence_id="ev-tests", location="artifacts/pytest.xml", content_hash="a" * 64
    )
    bundle = build_acceptance_context(
        change_id="chg-001",
        run_id="run-001",
        spec=_spec(),
        diff=_diff(),
        evidence=(evidence,),
        now=NOW,
    )
    kinds = sorted(source.kind for source in bundle.sources)
    assert kinds == [SourceKind.EVIDENCE, SourceKind.REPO, SourceKind.SPEC]
    spec_source = next(source for source in bundle.sources if source.kind is SourceKind.SPEC)
    assert spec_source.location == _spec().location
    assert spec_source.revision == "9f31ab2"
    assert spec_source.content_hash == hashlib.sha256(_spec().content.encode()).hexdigest()
    diff_source = next(source for source in bundle.sources if source.kind is SourceKind.REPO)
    assert diff_source.location == "small/pilot"
    assert diff_source.revision == SHA
    evidence_source = next(
        source for source in bundle.sources if source.kind is SourceKind.EVIDENCE
    )
    assert evidence_source.content_hash == "a" * 64


def test_acceptance_context_depends_on_review_materials() -> None:
    base = build_acceptance_context(
        change_id="chg-001", run_id="run-001", spec=_spec(), diff=_diff(), now=NOW
    )
    other = build_acceptance_context(
        change_id="chg-001",
        run_id="run-001",
        spec=_spec(),
        diff=_diff(content="diff --git a/other.py"),
        now=NOW,
    )
    assert base.bundle_hash != other.bundle_hash


def test_acceptance_context_rejects_duplicate_evidence() -> None:
    items = (
        EvidenceMaterial(evidence_id="ev-1", location="a.xml", content_hash="a" * 64),
        EvidenceMaterial(evidence_id="ev-1", location="b.xml", content_hash="b" * 64),
    )
    with pytest.raises(ValueError, match="duplicate evidence id"):
        build_acceptance_context(
            change_id="chg-001",
            run_id="run-001",
            spec=_spec(),
            diff=_diff(),
            evidence=items,
            now=NOW,
        )


def test_human_comments_enter_findings_as_data() -> None:
    comments = [
        MrComment(
            comment_id="c-1",
            body="please rename this variable",
            file="src/app.py",
            line=10,
            evidence_id="ev-comment-1",
        ),
        MrComment(comment_id="c-2", body="older round remark", reviewed_sha="aaa111"),
    ]
    findings = human_comment_findings(comments, reviewed_sha=SHA)
    assert [finding.id for finding in findings] == ["c-1", "c-2"]
    first, second = findings
    assert first.origin is FindingOrigin.HUMAN
    assert first.role is None
    assert first.severity is FindingSeverity.INFO
    assert first.status is FindingStatus.OPEN
    assert first.file == "src/app.py"
    assert first.line == 10
    assert first.reviewed_sha == SHA
    assert first.required_action is None
    assert first.evidence_ids == ["ev-comment-1"]
    assert second.reviewed_sha == "aaa111"


def test_classify_findings_weights_severity_and_status() -> None:
    findings = [
        make_finding("f-blocker", severity=FindingSeverity.BLOCKER),
        make_finding("f-resolved", severity=FindingSeverity.BLOCKER, status=FindingStatus.RESOLVED),
        make_finding("f-major", severity=FindingSeverity.MAJOR),
        make_finding("f-waived", severity=FindingSeverity.BLOCKER, status=FindingStatus.WAIVED),
    ]
    classification = classify_findings(findings, sha=SHA)
    assert [finding.id for finding in classification.blocking] == ["f-blocker"]
    assert [finding.id for finding in classification.non_blocking] == [
        "f-resolved",
        "f-major",
        "f-waived",
    ]
    assert classification.stale == ()


def test_classify_findings_excludes_other_revisions() -> None:
    stale = make_finding("f-stale", severity=FindingSeverity.BLOCKER).model_copy(
        update={"reviewed_sha": "aaa111"}
    )
    unbound = make_finding("f-unbound", severity=FindingSeverity.BLOCKER).model_copy(
        update={"reviewed_sha": None}
    )
    classification = classify_findings([stale, unbound], sha=SHA)
    assert [finding.id for finding in classification.blocking] == ["f-unbound"]
    assert [finding.id for finding in classification.stale] == ["f-stale"]


def test_review_gate_fails_on_blocking_finding() -> None:
    findings = [
        make_finding("f-blocker", severity=FindingSeverity.BLOCKER),
        make_finding("f-minor", severity=FindingSeverity.MINOR),
    ]
    result = evaluate_review_gate(findings, sha=SHA)
    assert result.gate is Gate.REVIEW
    assert result.status is GateStatus.FAILED
    assert result.sha == SHA
    assert result.summary is not None and "f-blocker" in result.summary


def test_review_gate_passes_on_non_blocking_findings() -> None:
    minor = make_finding("f-minor", severity=FindingSeverity.MINOR)
    result = evaluate_review_gate([minor], sha=SHA)
    assert result.status is GateStatus.PASSED
    assert result.summary is not None
    assert result.summary.startswith("review gate passed: 1 non-blocking finding(s)")


def test_review_gate_passes_clean() -> None:
    result = evaluate_review_gate([], sha=SHA)
    assert result.status is GateStatus.PASSED
    assert result.summary == f"review gate passed: no findings at {SHA}"


def test_review_gate_ignores_stale_blocker() -> None:
    stale = make_finding("f-stale", severity=FindingSeverity.BLOCKER).model_copy(
        update={"reviewed_sha": "aaa111"}
    )
    result = evaluate_review_gate([stale], sha=SHA)
    assert result.status is GateStatus.PASSED
    assert result.summary is not None and "1 stale finding(s) excluded" in result.summary


def test_review_gate_collects_evidence_ids_sorted() -> None:
    blocker = make_finding("f-b", severity=FindingSeverity.BLOCKER).model_copy(
        update={"evidence_ids": ["ev-2", "ev-1"]}
    )
    minor = make_finding("f-m", severity=FindingSeverity.MINOR).model_copy(
        update={"evidence_ids": ["ev-3"]}
    )
    result = evaluate_review_gate([blocker, minor], sha=SHA)
    assert result.evidence_ids == ["ev-1", "ev-2", "ev-3"]


def test_finding_confidence_is_bounded_and_optional() -> None:
    assert make_finding().confidence is None
    confident = make_finding().model_copy(update={"confidence": 0.8})
    assert confident.confidence == 0.8
    payload = confident.model_dump()
    for value in (-0.1, 1.1):
        with pytest.raises(ValidationError):
            Finding(**{**payload, "confidence": value})


def test_finding_confidence_round_trip() -> None:
    finding = make_finding("f-1").model_copy(update={"confidence": 0.42})
    restored = Finding.model_validate(finding.model_dump(mode="json"))
    assert restored == finding


def _change_request() -> ChangeRequestRef:
    return ChangeRequestRef(
        repository=RepositoryRef(provider=Provider.GITHUB, slug="small/pilot"),
        number=12,
        status=ChangeRequestStatus.OPEN,
    )


def _passing_gates(stage: Stage, route: Route) -> list[GateResult]:
    return [
        GateResult(gate=gate, status=GateStatus.PASSED, sha=SHA)
        for gate in sorted(required_gates(route, stage), key=lambda gate: gate.value)
    ]


def _stage_result(
    stage: Stage,
    action: NextAction,
    *,
    gates: list[GateResult],
    findings: list[Finding] | None = None,
) -> StageResult:
    return StageResult(
        stage=stage,
        run_id="run-001",
        change_id="chg-001",
        status=expected_result_status(action),
        next_action=action,
        gate_results=gates,
        findings=findings if findings is not None else [],
    )


def _advance_to(run: ChangeRun, target: Stage) -> None:
    """Complete every stage before ``target`` so its stage run becomes active."""
    prior = STAGE_SEQUENCE[: STAGE_SEQUENCE.index(target)]
    for stage, following in zip(prior, [*prior[1:], target], strict=True):
        apply_result(
            run,
            _stage_result(
                stage,
                ExecuteStageAction(next_stage=following),
                gates=_passing_gates(stage, run.route),
            ),
        )


def _merge_result(review: GateResult) -> StageResult:
    return _stage_result(
        Stage.REVIEW_VERIFICATION,
        MergeAction(change_request=_change_request()),
        gates=[review, GateResult(gate=Gate.VERIFICATION, status=GateStatus.PASSED, sha=SHA)],
    )


def _merge_context() -> MergeRequestContext:
    """Merge facts that pass the policy: human executor with a SHA-bound
    approval over green gates (T-026, FR-010; ADR-009 p.7)."""
    return MergeRequestContext(
        executor="human",
        risk_class=RiskClass.R1,
        route=Route.STANDARD,
        stage=Stage.REVIEW_VERIFICATION,
        expected_sha=SHA,
        head_sha=SHA,
        human_approvals=[make_merge_approval(SHA)],
    )


def test_blocking_finding_stops_merge() -> None:
    run = make_run(route=Route.STANDARD)
    _advance_to(run, Stage.REVIEW_VERIFICATION)
    blocker = make_finding("f-blocker", severity=FindingSeverity.BLOCKER)
    review = evaluate_review_gate([blocker], sha=SHA)
    decision = apply_result(run, _merge_result(review))
    assert run.status is RunStatus.BLOCKED
    assert decision.action == StopAction(
        outcome=StopOutcome.BLOCKED, reason="required gates not satisfied: review"
    )
    assert decision.next_stage is None


def test_merge_proceeds_when_review_gate_passes() -> None:
    run = make_run(route=Route.STANDARD)
    _advance_to(run, Stage.REVIEW_VERIFICATION)
    minor = make_finding("f-minor", severity=FindingSeverity.MINOR)
    review = evaluate_review_gate([minor], sha=SHA)
    decision = apply_result(run, _merge_result(review), merge_context=_merge_context())
    assert run.status is RunStatus.RUNNING
    assert decision.next_stage is Stage.RELEASE


def test_human_comments_do_not_stop_merge() -> None:
    run = make_run(route=Route.STANDARD)
    _advance_to(run, Stage.REVIEW_VERIFICATION)
    comments = [MrComment(comment_id="c-1", body="a remark to consider", file="src/app.py")]
    review = evaluate_review_gate(human_comment_findings(comments, reviewed_sha=SHA), sha=SHA)
    decision = apply_result(run, _merge_result(review), merge_context=_merge_context())
    assert run.status is RunStatus.RUNNING
    assert decision.next_stage is Stage.RELEASE
