"""Run-record manifest collection, record build and evidence persistence (T011, T034)."""

from pathlib import Path

import pytest

from dark_factory.changes.enums import (
    Provider,
    ReleaseStatus,
    Route,
    RunStatus,
    Stage,
    StageStatus,
    StopOutcome,
)
from dark_factory.changes.next_action import (
    ExecuteStageAction,
    NextAction,
    StopAction,
    WaitForInputAction,
)
from dark_factory.changes.run import StageResult
from dark_factory.changes.run_records import ReleaseEvidence, RunRecord
from dark_factory.cli import run_records
from dark_factory.cli.run_records import RunRecordError
from tests.changes_factories import NOW, make_change, make_manifest, make_stage_result


def _result(status: StageStatus) -> StageResult:
    """A construction attempt result with the next action matching its status."""
    next_action: NextAction
    match status:
        case StageStatus.WAITING:
            next_action = WaitForInputAction(reason="required gates not evaluated: code, ui")
        case StageStatus.BLOCKED:
            next_action = StopAction(outcome=StopOutcome.BLOCKED, reason="rework limit exhausted")
        case _:
            next_action = ExecuteStageAction(next_stage=Stage.REVIEW_VERIFICATION)
    return make_stage_result(next_action, status=status)


def _record(result: StageResult) -> RunRecord:
    """Build the record the CLI would persist for the attempt."""
    return run_records.build_run_record(
        change=make_change(),
        run_id="run-001",
        stage=Stage.CONSTRUCTION,
        route=Route.STANDARD,
        input_revision="731ac91",
        result=result,
        manifest=make_manifest(),
        started_at=NOW,
    )


def test_explicit_env_commits_win_over_the_ci_fallback() -> None:
    manifest = run_records.collect_run_manifest(
        {
            "DARK_FACTORY_COMMIT": "4f86c2a",
            "DARK_FACTORY_PRODUCT_COMMIT": "731ac91",
            "GITHUB_SHA": "ca31c10",
        }
    )
    assert manifest.factory_commit == "4f86c2a"
    assert manifest.product_commit == "731ac91"


def test_github_sha_fills_both_commits_when_the_explicit_vars_are_absent() -> None:
    manifest = run_records.collect_run_manifest({"GITHUB_SHA": "ca31c10"})
    assert manifest.factory_commit == "ca31c10"
    assert manifest.product_commit == "ca31c10"


def test_absent_and_whitespace_only_env_values_fall_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_records, "_git_head", lambda: "abc1234")
    manifest = run_records.collect_run_manifest(
        {"DARK_FACTORY_COMMIT": None, "DARK_FACTORY_PRODUCT_COMMIT": "   "}
    )
    assert manifest.factory_commit == "abc1234"
    assert manifest.product_commit == "abc1234"


def test_git_head_is_the_last_resort_for_both_commits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_records, "_git_head", lambda: "abc1234")
    manifest = run_records.collect_run_manifest({})
    assert manifest.factory_commit == "abc1234"
    assert manifest.product_commit == "abc1234"


def test_optional_manifest_inputs_stay_absent() -> None:
    manifest = run_records.collect_run_manifest({"GITHUB_SHA": "ca31c10"})
    assert manifest.pack_name is None
    assert manifest.pack_version is None
    assert manifest.blueprint_version is None
    assert manifest.gitops_commit is None
    assert manifest.okf_revision is None


@pytest.mark.parametrize("env", [{}, {"DARK_FACTORY_COMMIT": "  ", "GITHUB_SHA": None}])
def test_unresolvable_factory_commit_names_its_env_var(
    env: dict[str, str | None], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(run_records, "_git_head", lambda: None)
    with pytest.raises(RunRecordError) as excinfo:
        run_records.collect_run_manifest(env)
    assert "DARK_FACTORY_COMMIT" in str(excinfo.value)


def test_unresolvable_product_commit_names_its_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_records, "_git_head", lambda: None)
    with pytest.raises(RunRecordError) as excinfo:
        run_records.collect_run_manifest({"DARK_FACTORY_COMMIT": "4f86c2a"})
    assert "DARK_FACTORY_PRODUCT_COMMIT" in str(excinfo.value)


def test_waiting_result_keeps_the_run_waiting() -> None:
    result = _result(StageStatus.WAITING)
    record = _record(result)
    assert record.schema_version == 1
    assert record.manifest == make_manifest()
    assert record.change == make_change()
    run = record.run
    assert run.id == "run-001"
    assert run.change_id == "chg-001"
    assert run.route is Route.STANDARD
    assert run.provider is Provider.GITHUB  # fixed at run start from change.product
    assert run.status is RunStatus.WAITING
    stage_run = run.stages[0]
    assert stage_run.id == "run-001:construction:1"
    assert stage_run.status is StageStatus.WAITING
    assert stage_run.finished_at is None  # waiting is not terminal
    assert stage_run.started_at == NOW
    assert stage_run.input_revision == "731ac91"
    assert record.stage_results == [result]
    assert record.decisions == []


def test_blocked_result_blocks_the_run() -> None:
    record = _record(_result(StageStatus.BLOCKED))
    assert record.run.status is RunStatus.BLOCKED
    stage_run = record.run.stages[0]
    assert stage_run.status is StageStatus.BLOCKED
    assert stage_run.finished_at is None  # blocked is not terminal for the stage
    assert record.run.finished_at is None  # blocked is not terminal for the run


def test_succeeded_result_keeps_the_run_running() -> None:
    record = _record(_result(StageStatus.SUCCEEDED))
    assert record.run.status is RunStatus.RUNNING  # one stage does not complete the run
    stage_run = record.run.stages[0]
    assert stage_run.status is StageStatus.SUCCEEDED
    assert stage_run.finished_at is not None


def test_run_record_round_trips_through_the_evidence_dir(tmp_path: Path) -> None:
    record = _record(_result(StageStatus.WAITING))
    path = run_records.persist_run_record(tmp_path, record)
    assert path == tmp_path / "run_record.json"
    assert run_records.load_run_record(path) == record


def _release_evidence(decision: ReleaseStatus) -> ReleaseEvidence:
    return ReleaseEvidence(
        verified_at=NOW,
        decision=decision,
        reason=None if decision is ReleaseStatus.RELEASED else "smoke probes failed: http-health",
        expected_digest="sha256:" + "1" * 64,
        observed_digest="sha256:" + "1" * 64,
        argo_sync_status="Synced",
        argo_health_status="Healthy",
        application="apps-dev/pilot-dev",
        smoke=(),
        rollback_signal=None if decision is ReleaseStatus.RELEASED else "revert the GitOps commit",
    )


class TestBuildReleaseRunRecord:
    def test_released_decision_succeeds_the_run_and_the_release_stage(self) -> None:
        record = run_records.build_release_run_record(
            change=make_change(),
            run_id="run-042",
            evidence=_release_evidence(ReleaseStatus.RELEASED),
            manifest=make_manifest(),
        )

        assert record.schema_version == 1
        assert record.manifest == make_manifest()
        assert record.change == make_change()
        assert record.run.id == "run-042"
        assert record.run.change_id == "chg-001"
        assert record.run.route is Route.STANDARD
        assert record.run.provider is Provider.GITHUB
        assert record.run.status is RunStatus.SUCCEEDED
        assert record.run.finished_at is not None
        stage_run = record.run.stages[0]
        assert stage_run.id == "run-042:release:1"
        assert stage_run.stage is Stage.RELEASE
        assert stage_run.status is StageStatus.SUCCEEDED
        assert stage_run.started_at == NOW
        assert stage_run.finished_at is not None
        assert record.stage_results == []
        assert record.decisions == []
        assert record.release is not None
        assert record.release.decision is ReleaseStatus.RELEASED

    def test_failed_smoke_decision_fails_the_run(self) -> None:
        record = run_records.build_release_run_record(
            change=make_change(),
            run_id="run-042",
            evidence=_release_evidence(ReleaseStatus.RELEASE_FAILED),
            manifest=make_manifest(),
        )

        assert record.run.status is RunStatus.FAILED
        assert record.run.finished_at is not None
        stage_run = record.run.stages[0]
        assert stage_run.status is StageStatus.FAILED
        assert record.release is not None
        assert record.release.decision is ReleaseStatus.RELEASE_FAILED
        assert record.release.rollback_signal is not None

    def test_release_record_has_no_completion_violations_when_released(self) -> None:
        record = run_records.build_release_run_record(
            change=make_change(),
            run_id="run-042",
            evidence=_release_evidence(ReleaseStatus.RELEASED),
            manifest=make_manifest(),
        )

        assert record.completion_violations() == []

    def test_release_section_round_trips_through_the_evidence_dir(self, tmp_path: Path) -> None:
        record = run_records.build_release_run_record(
            change=make_change(),
            run_id="run-042",
            evidence=_release_evidence(ReleaseStatus.RELEASE_FAILED),
            manifest=make_manifest(),
        )
        path = run_records.persist_run_record(tmp_path, record)

        assert run_records.load_run_record(path) == record

    def test_records_written_before_t034_load_without_the_release_section(
        self, tmp_path: Path
    ) -> None:
        """Backward compatibility: a pre-T034 record has no release key at all."""
        record = _record(_result(StageStatus.SUCCEEDED))
        path = run_records.persist_run_record(tmp_path, record)

        loaded = run_records.load_run_record(path)

        assert loaded.release is None
        assert loaded == record
