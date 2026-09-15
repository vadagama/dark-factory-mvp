"""``factory run publish``: exit codes, output and secret hygiene (T-061).

The command publishes the ``run_record.json`` produced by ``factory stage run``
(T011) or ``factory release verify`` (T034) into a ``dark-factory-runs``
checkout; the runs root comes from ``--runs-root`` or ``DARK_FACTORY_RUNS_ROOT``.
"""

import json
from pathlib import Path

import pytest

from dark_factory.changes.run_records import RunRecord, to_json
from dark_factory.cli.main import EXIT_ERROR, EXIT_INVALID_INPUT, EXIT_OK, main
from dark_factory.cli.runs import RUNS_ROOT_ENV_VAR
from dark_factory.execution.runs import SNAPSHOT_NAME
from tests.changes_factories import make_record

GITHUB_TOKEN = "ghp_" + "a" * 36


def write_record(path: Path, record: RunRecord) -> Path:
    """Persist a record the way ``stage run --evidence-dir`` does."""
    path.write_text(to_json(record), encoding="utf-8")
    return path


def test_publish_reports_created_then_unchanged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    record_path = write_record(tmp_path / "run_record.json", make_record())
    runs_root = tmp_path / "runs-repo"

    created = main(["run", "publish", "--record", str(record_path), "--runs-root", str(runs_root)])
    captured = capsys.readouterr()
    assert created == EXIT_OK
    assert captured.err == ""
    assert captured.out == "factory run publish: created chg-001 at runs/2026/09/chg-001/run-001\n"

    unchanged = main(
        ["run", "publish", "--record", str(record_path), "--runs-root", str(runs_root)]
    )
    assert unchanged == EXIT_OK
    assert capsys.readouterr().out.startswith("factory run publish: unchanged chg-001 at ")


def test_publish_json_reports_the_outcome_and_the_address(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    record_path = write_record(tmp_path / "run_record.json", make_record())

    code = main(
        [
            "run",
            "publish",
            "--record",
            str(record_path),
            "--runs-root",
            str(tmp_path / "runs-repo"),
            "--json",
        ]
    )

    assert code == EXIT_OK
    assert json.loads(capsys.readouterr().out) == {
        "outcome": "created",
        "change_id": "chg-001",
        "run_id": "run-001",
        "path": "runs/2026/09/chg-001/run-001",
    }


def test_runs_root_falls_back_to_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record_path = write_record(tmp_path / "run_record.json", make_record())
    monkeypatch.setenv(RUNS_ROOT_ENV_VAR, str(tmp_path / "from-env"))

    code = main(["run", "publish", "--record", str(record_path)])

    assert code == EXIT_OK
    assert (tmp_path / "from-env" / "runs" / "2026" / "09" / "chg-001" / "run-001").is_dir()


def test_publish_without_a_runs_root_is_invalid_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(RUNS_ROOT_ENV_VAR, raising=False)
    record_path = write_record(tmp_path / "run_record.json", make_record())

    code = main(["run", "publish", "--record", str(record_path)])

    captured = capsys.readouterr()
    assert code == EXIT_INVALID_INPUT
    assert captured.out == ""
    assert RUNS_ROOT_ENV_VAR in captured.err


def test_publish_of_an_unreadable_record_is_invalid_input(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        [
            "run",
            "publish",
            "--record",
            str(tmp_path / "missing.json"),
            "--runs-root",
            str(tmp_path / "runs-repo"),
        ]
    )

    assert code == EXIT_INVALID_INPUT
    assert capsys.readouterr().out == ""


def test_publish_of_a_malformed_record_is_invalid_input(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    record_path = tmp_path / "run_record.json"
    record_path.write_text("{}", encoding="utf-8")

    code = main(
        [
            "run",
            "publish",
            "--record",
            str(record_path),
            "--runs-root",
            str(tmp_path / "runs-repo"),
        ]
    )

    captured = capsys.readouterr()
    assert code == EXIT_INVALID_INPUT
    assert "does not match the RunRecord schema" in captured.err
    assert not (tmp_path / "runs-repo").exists()


def test_publish_rejects_an_unsafe_record_without_echoing_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    record = make_record()
    unsafe = record.model_copy(
        update={"change": record.change.model_copy(update={"description": GITHUB_TOKEN})}
    )
    record_path = write_record(tmp_path / "run_record.json", unsafe)

    code = main(
        [
            "run",
            "publish",
            "--record",
            str(record_path),
            "--runs-root",
            str(tmp_path / "runs-repo"),
        ]
    )

    captured = capsys.readouterr()
    assert code == EXIT_ERROR
    assert captured.out == ""
    assert "change.description" in captured.err
    assert GITHUB_TOKEN not in captured.err
    assert not (tmp_path / "runs-repo").exists()


def test_publish_json_reports_a_rejected_record(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    record_path = write_record(tmp_path / "run_record.json", make_record())
    runs_root = tmp_path / "runs-repo"
    assert (
        main(["run", "publish", "--record", str(record_path), "--runs-root", str(runs_root)])
        == EXIT_OK
    )
    capsys.readouterr()
    changed = make_record().model_copy(
        update={"change": make_record().change.model_copy(update={"title": "Changed"})}
    )
    write_record(record_path, changed)

    code = main(
        ["run", "publish", "--record", str(record_path), "--runs-root", str(runs_root), "--json"]
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == EXIT_ERROR
    assert payload["error"] == "run_record_rejected"
    assert "already published" in payload["detail"]


def test_publish_writes_the_record_files(tmp_path: Path) -> None:
    record_path = write_record(tmp_path / "run_record.json", make_record())
    runs_root = tmp_path / "runs-repo"

    assert (
        main(["run", "publish", "--record", str(record_path), "--runs-root", str(runs_root)])
        == EXIT_OK
    )

    directory = runs_root / "runs" / "2026" / "09" / "chg-001" / "run-001"
    assert (directory / SNAPSHOT_NAME).is_file()
    assert (directory / "manifest.yaml").is_file()
    assert (directory / "evidence-index.json").is_file()
