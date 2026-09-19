"""CLI tests of ``factory release verify`` (T034, US5, ADR-011 p.6).

The probes run against injected fakes — no network. Covered: exit codes
(0 released / 1 release_failed / 2 invalid input), the fail-closed smoke
policy, evidence persistence into the run record, the rollback signal in
the diagnostics and the ADR-009 hygiene (secrets in URLs are never echoed).
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

import dark_factory.cli.release as release_module
from dark_factory.changes.enums import ReleaseStatus, RunStatus, Stage, StageStatus
from dark_factory.changes.run_records import SmokeProbeEvidence
from dark_factory.cli import run_records
from dark_factory.cli.main import EXIT_ERROR, EXIT_INVALID_INPUT, EXIT_OK, ReleaseVerifyArgs, main
from dark_factory.quality.release import (
    ReleaseObservation,
    build_release_evidence,
    evaluate_release,
)

NOW = datetime(2026, 9, 15, 12, 0, 0, tzinfo=UTC)

COMMIT_ENV = {"DARK_FACTORY_COMMIT": "4f86c2a", "DARK_FACTORY_PRODUCT_COMMIT": "731ac91"}

CHANGE_SNAPSHOT = """\
id: chg-001
title: Add export button
source: tracker
product:
  provider: github
  slug: small/pilot
risk_class: R1
"""

DIGEST = "sha256:" + "1" * 64


def _args(**overrides: Any) -> ReleaseVerifyArgs:
    """Arguments of a fully-specified verification; tests override single fields."""
    values: dict[str, Any] = {
        "expected_digest": DIGEST,
        "digest_json": None,
        "application": "apps-dev/pilot-dev",
        "observed_digest": DIGEST,
        "argo_sync": "Synced",
        "argo_health": "Healthy",
        "smoke_url": None,
        "smoke_digest_url": None,
        "smoke_digest_header": None,
        "evidence_dir": None,
        "change": None,
        "run_id": None,
        "json_output": False,
    }
    values.update(overrides)
    return ReleaseVerifyArgs(**values)


class _FakeProbe:
    """Injectable probe stub: every run returns the configured result."""

    def __init__(self, url: str, *, passed: bool, detail: str = "fake", name: str = "fake") -> None:
        self.url = url
        self._result = SmokeProbeEvidence(name=name, passed=passed, detail=detail)

    @property
    def name(self) -> str:
        return self._result.name

    async def run(self) -> SmokeProbeEvidence:
        return self._result


def _fake_passed(url: str) -> _FakeProbe:
    return _FakeProbe(url, passed=True, detail="HTTP 200", name="http-health")


def _fake_failed(url: str) -> _FakeProbe:
    return _FakeProbe(url, passed=False, detail="HTTP 500", name="http-health")


def _write_change(tmp_path: Path) -> Path:
    path = tmp_path / "change.yaml"
    path.write_text(CHANGE_SNAPSHOT, encoding="utf-8")
    return path


def _write_digest_json(tmp_path: Path, payload: str | None = None) -> Path:
    path = tmp_path / "image-digest.json"
    body = payload if payload is not None else json.dumps({"schema_version": 1, "digest": DIGEST})
    path.write_text(body, encoding="utf-8")
    return path


def _evidence_stub():
    """Released evidence for the wiring test (the handler is mocked out)."""
    observation = ReleaseObservation(
        expected_digest=DIGEST,
        observed_digest=DIGEST,
        argo_sync_raw="Synced",
        argo_health_raw="Healthy",
        smoke=[SmokeProbeEvidence(name="http-health", passed=True)],
    )
    decision = evaluate_release(observation)
    return build_release_evidence(
        observation, decision, verified_at=NOW, application="apps-dev/pilot-dev"
    )


class TestRendering:
    def test_text_released_report_lists_target_state_and_evidence(self) -> None:
        observation = ReleaseObservation(
            expected_digest=DIGEST,
            observed_digest=DIGEST,
            argo_sync_raw="Synced",
            argo_health_raw="Healthy",
            smoke=[SmokeProbeEvidence(name="http-health", passed=True)],
        )
        decision = evaluate_release(observation)
        evidence = build_release_evidence(
            observation, decision, verified_at=NOW, application="apps-dev/pilot-dev"
        )

        text = release_module.render_text(evidence, "/evidence/run_record.json")

        lines = text.splitlines()
        assert lines[0] == "factory release verify: released"
        assert "application: apps-dev/pilot-dev" in lines
        assert "argo: sync=Synced health=Healthy" in lines
        assert "smoke: passed (http-health=passed)" in lines
        assert "evidence: /evidence/run_record.json" in lines
        assert "rollback" not in text

    def test_text_failed_report_carries_reason_and_rollback_signal(self) -> None:
        observation = ReleaseObservation(
            expected_digest=DIGEST,
            observed_digest="sha256:" + "2" * 64,
        )
        decision = evaluate_release(observation)
        evidence = build_release_evidence(
            observation, decision, verified_at=NOW, application="apps-dev/pilot-dev"
        )

        text = release_module.render_text(evidence, None)

        assert "factory release verify: release_failed" in text
        assert "reason: expected digest" in text
        assert "rollback: revert the GitOps commit" in text
        assert "evidence:" not in text

    def test_text_smoke_tally_marks_not_run_when_empty(self) -> None:
        observation = ReleaseObservation(expected_digest=DIGEST, observed_digest=DIGEST)
        decision = evaluate_release(observation)
        evidence = build_release_evidence(observation, decision, verified_at=NOW)

        text = release_module.render_text(evidence, None)

        assert "smoke: not_run" in text
        assert "argo: sync=unknown health=unknown" in text

    def test_json_round_trips_the_evidence(self) -> None:
        observation = ReleaseObservation(
            expected_digest=DIGEST,
            observed_digest=DIGEST,
            argo_sync_raw="Synced",
            argo_health_raw="Healthy",
            smoke=[SmokeProbeEvidence(name="http-health", passed=True)],
        )
        decision = evaluate_release(observation)
        evidence = build_release_evidence(observation, decision, verified_at=NOW)

        payload = json.loads(release_module.render_json(evidence))

        assert payload == evidence.model_dump(mode="json")


class TestInvalidInput:
    def test_missing_digest_source_is_invalid_input(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = release_module.run_release_verify_command(_args(expected_digest=None))

        assert code == EXIT_INVALID_INPUT
        assert "expected digest is required" in capsys.readouterr().err

    def test_both_digest_sources_are_mutually_exclusive(self) -> None:
        code = release_module.run_release_verify_command(_args(digest_json="image-digest.json"))

        assert code == EXIT_INVALID_INPUT

    def test_blank_expected_digest_is_invalid_input(self) -> None:
        code = release_module.run_release_verify_command(_args(expected_digest="   "))

        assert code == EXIT_INVALID_INPUT

    def test_missing_digest_json_file_is_invalid_input(self, tmp_path: Path) -> None:
        code = release_module.run_release_verify_command(
            _args(expected_digest=None, digest_json=str(tmp_path / "absent.json"))
        )

        assert code == EXIT_INVALID_INPUT

    def test_invalid_json_artifact_is_invalid_input(self, tmp_path: Path) -> None:
        path = _write_digest_json(tmp_path, payload="not json")

        code = release_module.run_release_verify_command(
            _args(expected_digest=None, digest_json=str(path))
        )

        assert code == EXIT_INVALID_INPUT

    def test_artifact_without_a_digest_field_is_invalid_input(self, tmp_path: Path) -> None:
        path = _write_digest_json(tmp_path, payload=json.dumps({"tag": "sha-abc"}))

        code = release_module.run_release_verify_command(
            _args(expected_digest=None, digest_json=str(path))
        )

        assert code == EXIT_INVALID_INPUT

    def test_digest_probe_url_requires_the_health_probe(self) -> None:
        code = release_module.run_release_verify_command(
            _args(smoke_digest_url="http://target/version")
        )

        assert code == EXIT_INVALID_INPUT

    def test_digest_header_without_a_digest_url_is_invalid_input(self) -> None:
        code = release_module.run_release_verify_command(_args(smoke_digest_header="X-Digest"))

        assert code == EXIT_INVALID_INPUT

    def test_non_http_smoke_url_is_invalid_and_never_echoed(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        secret_url = "ftp://user:token@host/healthz"

        code = release_module.run_release_verify_command(_args(smoke_url=secret_url))

        assert code == EXIT_INVALID_INPUT
        captured = capsys.readouterr()
        assert "token" not in captured.err + captured.out
        assert "user" not in captured.err + captured.out

    def test_evidence_dir_requires_the_change_snapshot(self) -> None:
        code = release_module.run_release_verify_command(_args(evidence_dir="/tmp/evidence"))

        assert code == EXIT_INVALID_INPUT

    def test_change_without_evidence_dir_is_invalid_input(self, tmp_path: Path) -> None:
        code = release_module.run_release_verify_command(_args(change=str(_write_change(tmp_path))))

        assert code == EXIT_INVALID_INPUT

    def test_json_mode_reports_errors_as_json_on_stdout(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = release_module.run_release_verify_command(
            _args(expected_digest=None, json_output=True)
        )

        assert code == EXIT_INVALID_INPUT
        payload = json.loads(capsys.readouterr().out)
        assert payload["error"] == "invalid_input"


class TestVerificationWithoutEvidence:
    def test_failing_smoke_is_release_failed_with_exit_1(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(release_module, "HttpHealthProbe", _fake_failed)

        code = release_module.run_release_verify_command(
            _args(smoke_url="http://target.internal:8080/healthz")
        )

        assert code == EXIT_ERROR
        text = capsys.readouterr().out
        assert "factory release verify: release_failed" in text
        assert "smoke: failed (http-health=failed)" in text
        assert "rollback: revert the GitOps commit" in text

    def test_passing_smoke_releases_with_exit_0(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(release_module, "HttpHealthProbe", _fake_passed)

        code = release_module.run_release_verify_command(
            _args(smoke_url="http://target.internal:8080/healthz", json_output=True)
        )

        assert code == EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        assert payload["decision"] == "released"
        assert payload["expected_digest"] == DIGEST
        assert payload["smoke"] == [{"name": "http-health", "passed": True, "detail": "HTTP 200"}]

    def test_no_smoke_options_fail_closed(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = release_module.run_release_verify_command(_args())

        assert code == EXIT_ERROR
        text = capsys.readouterr().out
        assert "release_failed" in text
        assert "smoke was not run" in text

    def test_digest_mismatch_short_circuits_before_the_probes(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        ran = False

        def _unexpected(url: str) -> _FakeProbe:
            nonlocal ran
            ran = True
            return _fake_passed(url)

        monkeypatch.setattr(release_module, "HttpHealthProbe", _unexpected)

        code = release_module.run_release_verify_command(
            _args(observed_digest="sha256:" + "2" * 64, smoke_url="http://target/healthz")
        )

        assert code == EXIT_ERROR
        assert ran is False  # FR-011: no probe runs against a wrong digest
        text = capsys.readouterr().out
        assert "does not match" in text

    def test_probe_set_with_digest_probe_yields_two_results(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        built: list[str] = []

        class _RecordingProbe(_FakeProbe):
            def __init__(self, url: str, *, passed: bool, detail: str = "fake", name: str = "fake"):
                super().__init__(url, passed=passed, detail=detail, name=name)
                built.append(name)

        def _passed_health(url: str) -> _RecordingProbe:
            return _RecordingProbe(url, passed=True, detail="HTTP 200", name="http-health")

        def _passed_digest(url: str, digest: str, *, header: str | None = None) -> _RecordingProbe:
            return _RecordingProbe(url, passed=True, detail="digest found", name="http-digest")

        monkeypatch.setattr(release_module, "HttpHealthProbe", _passed_health)
        monkeypatch.setattr(release_module, "HttpDigestProbe", _passed_digest)

        code = release_module.run_release_verify_command(
            _args(smoke_url="http://t/healthz", smoke_digest_url="http://t/version")
        )

        assert code == EXIT_OK
        assert built == ["http-health", "http-digest"]


class TestEvidencePersistence:
    @pytest.fixture(autouse=True)
    def _manifest_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key, value in COMMIT_ENV.items():
            monkeypatch.setenv(key, value)

    def test_successful_verification_persists_a_succeeded_run_record(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(release_module, "HttpHealthProbe", _fake_passed)
        evidence_dir = tmp_path / "evidence"
        change_path = _write_change(tmp_path)

        code = release_module.run_release_verify_command(
            _args(
                smoke_url="http://target.internal:8080/healthz",
                evidence_dir=str(evidence_dir),
                change=str(change_path),
                run_id="run-042",
            )
        )

        assert code == EXIT_OK
        record = run_records.load_run_record(evidence_dir / "run_record.json")
        assert record.run.id == "run-042"
        assert record.run.status is RunStatus.SUCCEEDED
        assert record.run.change_id == "chg-001"
        stage_run = record.run.stages[0]
        assert stage_run.stage is Stage.RELEASE
        assert stage_run.status is StageStatus.SUCCEEDED
        assert record.stage_results == []
        release = record.release
        assert release is not None
        assert release.decision is ReleaseStatus.RELEASED
        assert release.expected_digest == DIGEST
        assert release.observed_digest == DIGEST
        assert release.argo_sync_status == "Synced"
        assert release.argo_health_status == "Healthy"
        assert release.application == "apps-dev/pilot-dev"
        assert release.rollback_signal is None
        assert [probe.name for probe in release.smoke] == ["http-health"]
        assert "evidence:" in capsys.readouterr().out

    def test_failed_smoke_persists_a_failed_run_record_with_the_rollback_signal(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(release_module, "HttpHealthProbe", _fake_failed)
        evidence_dir = tmp_path / "evidence"
        change_path = _write_change(tmp_path)

        code = release_module.run_release_verify_command(
            _args(
                smoke_url="http://target.internal:8080/healthz",
                evidence_dir=str(evidence_dir),
                change=str(change_path),
                run_id="run-042",
            )
        )

        assert code == EXIT_ERROR
        record = run_records.load_run_record(evidence_dir / "run_record.json")
        assert record.run.status is RunStatus.FAILED
        assert record.run.stages[0].status is StageStatus.FAILED
        release = record.release
        assert release is not None
        assert release.decision is ReleaseStatus.RELEASE_FAILED
        assert release.reason is not None
        assert "http-health" in release.reason
        assert release.rollback_signal is not None

    def test_unpersistable_evidence_dir_fails_with_exit_1_after_the_verification(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(release_module, "HttpHealthProbe", _fake_passed)
        blocked = tmp_path / "blocked"
        blocked.write_text("", encoding="utf-8")  # a file, not a directory: mkdir fails
        change_path = _write_change(tmp_path)

        code = release_module.run_release_verify_command(
            _args(
                smoke_url="http://target.internal:8080/healthz",
                evidence_dir=str(blocked),
                change=str(change_path),
            )
        )

        assert code == EXIT_ERROR
        captured = capsys.readouterr()
        assert "cannot persist the run record" in captured.err + captured.out

    def test_unresolvable_manifest_commits_are_invalid_input(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("DARK_FACTORY_COMMIT", raising=False)
        monkeypatch.delenv("DARK_FACTORY_PRODUCT_COMMIT", raising=False)
        monkeypatch.delenv("GITHUB_SHA", raising=False)
        monkeypatch.setattr(run_records, "_git_head", lambda: None)
        change_path = _write_change(tmp_path)

        code = release_module.run_release_verify_command(
            _args(evidence_dir=str(tmp_path / "evidence"), change=str(change_path))
        )

        assert code == EXIT_INVALID_INPUT


class TestMainWiring:
    def test_main_dispatches_release_verify(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        seen: dict[str, ReleaseVerifyArgs] = {}

        def _fake_command(args: ReleaseVerifyArgs) -> int:
            seen["args"] = args
            print(release_module.render_json(_evidence_stub()))
            return EXIT_OK

        monkeypatch.setattr(release_module, "run_release_verify_command", _fake_command)

        argv = [
            "release",
            "verify",
            "--expected-digest",
            DIGEST,
            "--observed-digest",
            DIGEST,
            "--application",
            "apps-dev/pilot-dev",
            "--argo-sync",
            "Synced",
            "--argo-health",
            "Healthy",
            "--smoke-url",
            "http://target.internal:8080/healthz",
            "--run-id",
            "run-042",
            "--json",
        ]
        assert main(argv) == EXIT_OK

        args = seen["args"]
        assert args == ReleaseVerifyArgs(
            expected_digest=DIGEST,
            digest_json=None,
            application="apps-dev/pilot-dev",
            observed_digest=DIGEST,
            argo_sync="Synced",
            argo_health="Healthy",
            smoke_url="http://target.internal:8080/healthz",
            smoke_digest_url=None,
            smoke_digest_header=None,
            evidence_dir=None,
            change=None,
            run_id="run-042",
            json_output=True,
        )
        assert json.loads(capsys.readouterr().out)["decision"] == "released"

    def test_invalid_input_in_json_mode_keeps_the_secret_out_of_the_output(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(
            [
                "release",
                "verify",
                "--expected-digest",
                DIGEST,
                "--smoke-url",
                "ftp://user:token@host/healthz",
                "--json",
            ]
        )

        assert code == EXIT_INVALID_INPUT
        captured = capsys.readouterr()
        assert "token" not in captured.out + captured.err
        assert "user" not in captured.out + captured.err
