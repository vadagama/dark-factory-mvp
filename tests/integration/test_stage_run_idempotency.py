"""``factory stage run`` idempotency: replay of the committed result (T012, FR-017).

A repeat with the same ``operation_key`` (ADR-006 p.3) must not execute again:
a committed ``succeeded``/``waiting`` result replays byte-exact, a ``failed``
result starts a new attempt, a foreign or torn record is treated as absent.
"""

import hashlib
import json
import time
from pathlib import Path

import pytest

from dark_factory.changes.enums import Stage, StageStatus
from dark_factory.changes.keys import operation_key
from dark_factory.changes.next_action import WaitForInputAction
from dark_factory.changes.run import StageResult
from dark_factory.changes.run_records import from_json, to_json
from dark_factory.cli import run_records
from dark_factory.cli.main import EXIT_INVALID_INPUT, EXIT_WAITING, main
from dark_factory.orchestration.idempotency import (
    EvidenceOperationStore,
    stage_operation_key,
)

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


def write_snapshot(
    directory: Path, *, name: str = "chg_001.yaml", text: str = VALID_SNAPSHOT
) -> Path:
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


def stage_run_argv(snapshot: Path, *extra: str) -> list[str]:
    return ["stage", "run", "--change", str(snapshot), "--stage", "construction", *extra]


def evidence_argv(snapshot: Path, evidence: Path, run_id: str) -> list[str]:
    return stage_run_argv(snapshot, "--run-id", run_id, "--json", "--evidence-dir", str(evidence))


def make_result(run_id: str, *, input_revision: str | None) -> StageResult:
    """A valid waiting StageResult with the given operation identity (test seed)."""
    return StageResult(
        stage=Stage.CONSTRUCTION,
        run_id=run_id,
        change_id="chg-001",
        input_revision=input_revision,
        status=StageStatus.WAITING,
        next_action=WaitForInputAction(reason="seeded record"),
    )


def test_repeat_with_same_operation_key_replays_the_committed_result(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot = write_snapshot(tmp_path)
    evidence = tmp_path / "evidence"
    argv = evidence_argv(snapshot, evidence, "run_01H")
    assert main(argv) == EXIT_WAITING
    first = capsys.readouterr()
    payload = json.loads(first.out)
    assert payload["attempt_number"] == 1
    stage_result_before = (evidence / "stage_result.json").read_bytes()
    run_record_before = (evidence / "run_record.json").read_bytes()

    time.sleep(0.01)
    assert main(argv) == EXIT_WAITING
    second = capsys.readouterr()

    # Byte-equal stdout proves no re-execution: a fresh run would stamp a new
    # produced_at into the document.
    assert second.out == first.out
    assert second.err == first.err
    replayed = json.loads(second.out)
    assert replayed == payload
    assert replayed["produced_at"] == payload["produced_at"]
    assert replayed["attempt_number"] == 1
    assert (evidence / "stage_result.json").read_bytes() == stage_result_before
    assert (evidence / "run_record.json").read_bytes() == run_record_before


def test_replay_does_not_require_the_run_manifest_environment(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = write_snapshot(tmp_path)
    evidence = tmp_path / "evidence"
    monkeypatch.setenv("DARK_FACTORY_COMMIT", "4f86c2a")
    monkeypatch.setenv("DARK_FACTORY_PRODUCT_COMMIT", "731ac91")
    argv = evidence_argv(snapshot, evidence, "run_01H")
    assert main(argv) == EXIT_WAITING
    first = capsys.readouterr()

    for variable in ("DARK_FACTORY_COMMIT", "DARK_FACTORY_PRODUCT_COMMIT", "GITHUB_SHA"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(run_records, "_git_head", lambda: None)
    time.sleep(0.01)
    assert main(argv) == EXIT_WAITING
    second = capsys.readouterr()

    assert second.out == first.out
    assert second.err == first.err
    # A fresh run in that environment cannot build a manifest and exits 2
    # before the stage starts — the replay above did not need it.
    assert main(evidence_argv(snapshot, evidence, "run_02H")) == EXIT_INVALID_INPUT


def test_new_run_id_executes_a_new_operation_in_the_same_evidence_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot = write_snapshot(tmp_path)
    evidence = tmp_path / "evidence"
    assert main(evidence_argv(snapshot, evidence, "run_01H")) == EXIT_WAITING
    first = json.loads(capsys.readouterr().out)

    time.sleep(0.01)
    assert main(evidence_argv(snapshot, evidence, "run_02H")) == EXIT_WAITING
    second = json.loads(capsys.readouterr().out)

    assert second["run_id"] == "run_02H"
    assert second["produced_at"] != first["produced_at"]
    persisted = json.loads((evidence / "stage_result.json").read_text(encoding="utf-8"))
    assert persisted["run_id"] == "run_02H"


def test_changed_input_executes_a_new_operation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot = write_snapshot(tmp_path)
    evidence = tmp_path / "evidence"
    argv = evidence_argv(snapshot, evidence, "run_01H")
    assert main(argv) == EXIT_WAITING
    first = json.loads(capsys.readouterr().out)

    snapshot.write_text(VALID_SNAPSHOT + "description: extended scope\n", encoding="utf-8")
    time.sleep(0.01)
    assert main(argv) == EXIT_WAITING
    second = json.loads(capsys.readouterr().out)

    # A new input revision is a new logical operation (ADR-006 p.3): fresh execution.
    assert second["input_revision"] != first["input_revision"]
    assert second["produced_at"] != first["produced_at"]


def test_failed_result_is_retried_not_replayed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot = write_snapshot(tmp_path)
    evidence = tmp_path / "evidence"
    argv = evidence_argv(snapshot, evidence, "run_01H")
    assert main(argv) == EXIT_WAITING
    waiting = json.loads(capsys.readouterr().out)
    result_path = evidence / "stage_result.json"
    committed = from_json(StageResult, result_path.read_text(encoding="utf-8"))
    failed = committed.model_copy(update={"status": StageStatus.FAILED})
    result_path.write_text(to_json(failed), encoding="utf-8")

    time.sleep(0.01)
    assert main(argv) == EXIT_WAITING
    retried = json.loads(capsys.readouterr().out)

    # FAILED/BLOCKED -> IN_PROGRESS: the repeat is a new attempt, not a replay.
    assert retried["status"] == "waiting"
    assert retried["produced_at"] != waiting["produced_at"]
    overwritten = from_json(StageResult, result_path.read_text(encoding="utf-8"))
    assert overwritten.status is StageStatus.WAITING


def test_foreign_committed_result_does_not_replay(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot = write_snapshot(tmp_path)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    foreign = make_result(
        "run_other", input_revision=hashlib.sha256(snapshot.read_bytes()).hexdigest()
    )
    (evidence / "stage_result.json").write_text(to_json(foreign), encoding="utf-8")

    assert main(evidence_argv(snapshot, evidence, "run_01H")) == EXIT_WAITING
    payload = json.loads(capsys.readouterr().out)

    # A committed result of another run does not authorize a replay: fresh execution.
    assert payload["run_id"] == "run_01H"
    persisted = json.loads((evidence / "stage_result.json").read_text(encoding="utf-8"))
    assert persisted["run_id"] == "run_01H"


def test_torn_write_is_treated_as_absent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot = write_snapshot(tmp_path)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "stage_result.json").write_bytes(b"{not json")

    assert main(evidence_argv(snapshot, evidence, "run_01H")) == EXIT_WAITING
    payload = json.loads(capsys.readouterr().out)

    assert payload["run_id"] == "run_01H"
    persisted = from_json(StageResult, (evidence / "stage_result.json").read_text(encoding="utf-8"))
    assert persisted.run_id == "run_01H"
    assert persisted.status is StageStatus.WAITING


def test_store_rejects_identity_mismatch_on_record(tmp_path: Path) -> None:
    store = EvidenceOperationStore(tmp_path)
    result = make_result("run_01H", input_revision="a" * 64)

    with pytest.raises(ValueError):
        store.record(operation_key("run_02H", Stage.CONSTRUCTION, "a" * 64), result)
    assert not (tmp_path / "stage_result.json").exists()
    assert not (tmp_path / "stage_result.json.tmp").exists()


def test_store_find_existing_identity_semantics(tmp_path: Path) -> None:
    snapshot = write_snapshot(tmp_path)
    evidence = tmp_path / "evidence"
    assert main(evidence_argv(snapshot, evidence, "run_01H")) == EXIT_WAITING

    store = EvidenceOperationStore(evidence)
    revision = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    key = operation_key("run_01H", Stage.CONSTRUCTION, revision)

    committed = store.find_existing(key)
    assert committed is not None
    assert stage_operation_key(committed) == key
    assert store.find_existing("other") is None
    assert stage_operation_key(make_result("run_01H", input_revision=None)) is None
