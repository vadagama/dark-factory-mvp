"""``factory stage run`` behavior: snapshot, revision, keys, exit codes (T009)."""

import hashlib
import json
from pathlib import Path

import pytest

from dark_factory.changes.enums import RunStatus, StageStatus
from dark_factory.changes.run_records import RunRecord, from_json
from dark_factory.cli import run_records
from dark_factory.cli.main import (
    EXIT_BLOCKED,
    EXIT_ERROR,
    EXIT_INVALID_INPUT,
    EXIT_OK,
    EXIT_WAITING,
    main,
)
from dark_factory.cli.stage import exit_code_for

VALID_SNAPSHOT = """\
id: chg-001
title: Add export button
source: tracker
product:
  provider: github
  slug: small/pilot
risk_class: R1
created_at: 2026-09-13T12:00:00Z
"""

CONSTRUCTION = ["--stage", "construction"]

CONTRACT_STAGE_RESULT_FIELDS = {
    "schema_version",
    "stage",
    "run_id",
    "change_id",
    "attempt_number",
    "input_revision",
    "status",
    "next_action",
    "artifacts",
    "evidence",
    "gate_results",
    "findings",
    "escalations",
    "release",
    "usage",
    "produced_at",
    "questions",
    "rework_summary",
    "conversation_errors",
    "phase",
}


def write_snapshot(
    directory: Path, *, name: str = "chg_001.yaml", text: str = VALID_SNAPSHOT
) -> Path:
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


def stage_run_argv(snapshot: Path, *extra: str) -> list[str]:
    return ["stage", "run", "--change", str(snapshot), *CONSTRUCTION, *extra]


def test_stage_run_json_matches_contract(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot = write_snapshot(tmp_path)
    assert main(stage_run_argv(snapshot, "--json")) == EXIT_WAITING
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == CONTRACT_STAGE_RESULT_FIELDS
    assert payload["schema_version"] == 1
    assert payload["stage"] == "construction"
    assert payload["status"] == "waiting"
    assert payload["next_action"]["type"] == "wait_for_input"
    assert payload["next_action"]["reason"]
    assert payload["change_id"] == "chg-001"
    assert payload["attempt_number"] == 1
    assert payload["run_id"].startswith("run_")
    assert payload["input_revision"] == hashlib.sha256(snapshot.read_bytes()).hexdigest()
    assert payload["artifacts"] == []
    assert payload["evidence"] == []
    # Standard route (the default): construction carries the machine gate ``code``
    # alone since ADR-029 moved ``ui`` to the design stage; it is unevaluated by the
    # deterministic path (FR-009) and reported pending.
    assert payload["gate_results"] == [
        {"gate": "code", "status": "pending", "sha": None, "summary": None, "evidence_ids": []},
    ]
    assert payload["findings"] == []
    assert payload["usage"] is None


def test_stage_run_text_output_is_a_human_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot = write_snapshot(tmp_path)
    assert main(stage_run_argv(snapshot, "--run-id", "run_01H")) == EXIT_WAITING
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "stage construction: waiting" in captured.out
    assert "run_id=run_01H" in captured.out
    assert "operation_key=run_01H:construction:" in captured.out
    assert "next_action=wait_for_input" in captured.out
    assert "reason: required gates not evaluated: code" in captured.out


def test_route_selects_gate_applicability(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot = write_snapshot(tmp_path)
    assert main(stage_run_argv(snapshot, "--route", "quick", "--json")) == EXIT_WAITING
    gates = [gate["gate"] for gate in json.loads(capsys.readouterr().out)["gate_results"]]
    assert gates == ["code"]


def test_stage_run_json_logs_operation_key_on_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot = write_snapshot(tmp_path)
    revision = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    assert main(stage_run_argv(snapshot, "--run-id", "run_01H", "--json")) == EXIT_WAITING
    captured = capsys.readouterr()
    assert captured.err == f"operation_key=run_01H:construction:{revision}\n"
    assert json.loads(captured.out)["status"] == "waiting"


def test_input_revision_is_deterministic_for_identical_input(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot = write_snapshot(tmp_path)
    argv = stage_run_argv(snapshot, "--run-id", "run_01H", "--json")
    assert main(argv) == EXIT_WAITING
    first = json.loads(capsys.readouterr().out)["input_revision"]
    assert main(argv) == EXIT_WAITING
    second = json.loads(capsys.readouterr().out)["input_revision"]
    assert first == second == hashlib.sha256(snapshot.read_bytes()).hexdigest()


def test_input_revision_differs_for_changed_content(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "first").mkdir()
    (tmp_path / "second").mkdir()
    first = write_snapshot(tmp_path / "first", name="chg.yaml")
    second = write_snapshot(
        tmp_path / "second", name="chg.yaml", text=VALID_SNAPSHOT + "description: extra\n"
    )
    assert main(stage_run_argv(first, "--run-id", "run_01H", "--json")) == EXIT_WAITING
    first_revision = json.loads(capsys.readouterr().out)["input_revision"]
    assert main(stage_run_argv(second, "--run-id", "run_01H", "--json")) == EXIT_WAITING
    second_revision = json.loads(capsys.readouterr().out)["input_revision"]
    assert first_revision != second_revision


def test_explicit_input_revision_overrides_the_computed_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot = write_snapshot(tmp_path)
    argv = stage_run_argv(snapshot, "--input-revision", "a1b2c3d", "--run-id", "run_01H", "--json")
    assert main(argv) == EXIT_WAITING
    captured = capsys.readouterr()
    assert json.loads(captured.out)["input_revision"] == "a1b2c3d"
    assert captured.err == "operation_key=run_01H:construction:a1b2c3d\n"


def test_run_id_is_taken_from_the_cli_or_generated(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot = write_snapshot(tmp_path)
    assert main(stage_run_argv(snapshot, "--run-id", "run_custom", "--json")) == EXIT_WAITING
    assert json.loads(capsys.readouterr().out)["run_id"] == "run_custom"
    assert main(stage_run_argv(snapshot, "--json")) == EXIT_WAITING
    generated = json.loads(capsys.readouterr().out)["run_id"]
    assert generated.startswith("run_")
    assert len(generated) > len("run_")


def test_non_interactive_flag_is_accepted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot = write_snapshot(tmp_path)
    assert main(stage_run_argv(snapshot, "--non-interactive", "--json")) == EXIT_WAITING
    assert json.loads(capsys.readouterr().out)["status"] == "waiting"


@pytest.mark.parametrize(
    "text",
    [
        "id: chg-001\nsource: tracker\n",  # schema mismatch: title, product, risk_class missing
        "- just\n- a list\n",  # not a mapping
        "key: [unclosed\n",  # not valid YAML
    ],
)
def test_invalid_snapshot_content_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], text: str
) -> None:
    snapshot = write_snapshot(tmp_path, text=text)
    assert main(stage_run_argv(snapshot, "--json")) == EXIT_INVALID_INPUT
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == "invalid_input"
    assert payload["detail"]


def test_missing_snapshot_file_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    missing = tmp_path / "absent.yaml"
    assert main(stage_run_argv(missing, "--json")) == EXIT_INVALID_INPUT
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == "invalid_input"
    assert "absent.yaml" in payload["detail"]


def test_invalid_input_reports_on_stderr_in_text_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "absent.yaml"
    assert main(stage_run_argv(missing)) == EXIT_INVALID_INPUT
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "cannot read change snapshot" in captured.err


def test_invalid_input_never_starts_the_stage(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "absent.yaml"
    evidence = tmp_path / "evidence"
    assert main(stage_run_argv(missing, "--evidence-dir", str(evidence))) == EXIT_INVALID_INPUT
    assert not evidence.exists()


def test_schema_mismatch_mentions_the_first_missing_field(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot = write_snapshot(tmp_path, text="id: chg-001\nsource: tracker\n")
    assert main(stage_run_argv(snapshot, "--json")) == EXIT_INVALID_INPUT
    detail = json.loads(capsys.readouterr().out)["detail"]
    assert "title: Field required" in detail


@pytest.mark.parametrize("option", ["--input-revision", "--run-id"])
@pytest.mark.parametrize("value", ["", "   "])
def test_blank_option_values_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], option: str, value: str
) -> None:
    snapshot = write_snapshot(tmp_path)
    assert main(stage_run_argv(snapshot, option, value, "--json")) == EXIT_INVALID_INPUT
    assert json.loads(capsys.readouterr().out)["error"] == "invalid_input"


def test_evidence_dir_receives_snapshot_and_stage_result(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot = write_snapshot(tmp_path)
    evidence = tmp_path / "evidence"
    assert main(stage_run_argv(snapshot, "--evidence-dir", str(evidence), "--json")) == (
        EXIT_WAITING
    )
    assert (evidence / "change_snapshot.yaml").read_bytes() == snapshot.read_bytes()
    persisted = json.loads((evidence / "stage_result.json").read_text(encoding="utf-8"))
    assert persisted["schema_version"] == 1
    assert persisted["status"] == "waiting"
    assert persisted["run_id"] == json.loads(capsys.readouterr().out)["run_id"]


def test_evidence_dir_receives_the_run_record(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = write_snapshot(tmp_path)
    evidence = tmp_path / "evidence"
    monkeypatch.setenv("DARK_FACTORY_COMMIT", "4f86c2a")
    monkeypatch.setenv("DARK_FACTORY_PRODUCT_COMMIT", "731ac91")
    assert main(stage_run_argv(snapshot, "--evidence-dir", str(evidence), "--json")) == (
        EXIT_WAITING
    )
    run_id = json.loads(capsys.readouterr().out)["run_id"]
    record = from_json(RunRecord, (evidence / "run_record.json").read_text(encoding="utf-8"))
    assert record.run.id == run_id
    assert record.run.status is RunStatus.WAITING
    assert record.run.stages[0].status is StageStatus.WAITING
    assert record.stage_results[0].status is StageStatus.WAITING
    assert record.manifest.factory_commit == "4f86c2a"
    assert record.manifest.product_commit == "731ac91"
    assert record.change.id == "chg-001"


def test_unusable_evidence_dir_fails_before_execution(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot = write_snapshot(tmp_path)
    blocker = tmp_path / "blocked"  # an existing file cannot become the evidence directory
    blocker.write_text("not a directory", encoding="utf-8")
    assert main(stage_run_argv(snapshot, "--evidence-dir", str(blocker), "--json")) == (
        EXIT_INVALID_INPUT
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == "invalid_input"
    assert "cannot fix the input snapshot" in payload["detail"]


def test_unpersistable_stage_result_fails_with_exit_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot = write_snapshot(tmp_path)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "stage_result.json").mkdir()  # blocks the result write after execution
    assert main(stage_run_argv(snapshot, "--evidence-dir", str(evidence), "--json")) == EXIT_ERROR
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["error"] == "execution_error"
    assert "cannot persist the stage result" in payload["detail"]
    assert captured.err == ""


def test_missing_commit_refs_fail_before_the_stage_starts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = write_snapshot(tmp_path)
    evidence = tmp_path / "evidence"
    monkeypatch.setattr(run_records, "_git_head", lambda: None)
    for variable in ("DARK_FACTORY_COMMIT", "DARK_FACTORY_PRODUCT_COMMIT", "GITHUB_SHA"):
        monkeypatch.delenv(variable, raising=False)
    assert main(stage_run_argv(snapshot, "--evidence-dir", str(evidence), "--json")) == (
        EXIT_INVALID_INPUT
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == "invalid_input"
    assert "DARK_FACTORY_COMMIT" in payload["detail"]
    assert not (evidence / "stage_result.json").exists()  # the stage did not start


def test_unpersistable_run_record_fails_with_exit_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = write_snapshot(tmp_path)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "run_record.json").mkdir()  # blocks the run-record write after execution
    monkeypatch.setenv("DARK_FACTORY_COMMIT", "4f86c2a")
    monkeypatch.setenv("DARK_FACTORY_PRODUCT_COMMIT", "731ac91")
    assert main(stage_run_argv(snapshot, "--evidence-dir", str(evidence), "--json")) == EXIT_ERROR
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["error"] == "execution_error"
    assert "cannot persist the run record" in payload["detail"]
    assert captured.err == ""


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (StageStatus.SUCCEEDED, EXIT_OK),
        (StageStatus.WAITING, EXIT_WAITING),
        (StageStatus.BLOCKED, EXIT_BLOCKED),
        (StageStatus.FAILED, EXIT_ERROR),
    ],
)
def test_exit_code_matches_the_contract_table(status: StageStatus, code: int) -> None:
    assert exit_code_for(status) == code
