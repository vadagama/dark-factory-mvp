"""CI job script ``dark_factory.cli.ci_job``: exit codes, validation, outputs (T025)."""

import json
from pathlib import Path

import pytest

from dark_factory.cli.ci_job import (
    StageJobError,
    StageJobOutcome,
    classify_exit_code,
    job_exit_code,
    load_stage_result,
    main,
    outcome_for_status,
    parse_stage_result,
    render_outputs,
)
from dark_factory.cli.main import (
    EXIT_BLOCKED,
    EXIT_ERROR,
    EXIT_INVALID_INPUT,
    EXIT_OK,
    EXIT_WAITING,
)

VALID_RESULT = {
    "schema_version": 1,
    "stage": "construction",
    "run_id": "run_01H",
    "change_id": "chg_smoke",
    "attempt_number": 1,
    "input_revision": "a1b2c3d",
    "status": "succeeded",
    "next_action": {"type": "merge", "reason": "all machine gates passed"},
    "artifacts": [],
    "evidence": [],
    "gate_results": [],
    "findings": [],
    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "cost": "0.00"},
    "produced_at": "2026-09-13T10:00:00Z",
}

EXPECTED_OUTPUTS = (
    "status=succeeded\n"
    "next_action=merge\n"
    "run_id=run_01H\n"
    "change_id=chg_smoke\n"
    "usage_total_tokens=15\n"
    "usage_cost=0.00\n"
)


def mutated(**changes: object) -> dict[str, object]:
    result = dict(VALID_RESULT)
    result.update(changes)
    return result


def write_result(
    directory: Path, *, name: str = "stage_result.json", data: object = VALID_RESULT
) -> Path:
    path = directory / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("code", "outcome"),
    [
        (EXIT_OK, StageJobOutcome.OK),
        (EXIT_WAITING, StageJobOutcome.WAITING),
        (EXIT_BLOCKED, StageJobOutcome.BLOCKED),
        (EXIT_ERROR, StageJobOutcome.FAILED),
        (EXIT_INVALID_INPUT, StageJobOutcome.INVALID_INPUT),
    ],
)
def test_exit_code_classification_matches_the_contract_table(
    code: int, outcome: StageJobOutcome
) -> None:
    assert classify_exit_code(code) is outcome


def test_unknown_stage_run_exit_code_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown factory stage run exit code"):
        classify_exit_code(3)


@pytest.mark.parametrize(
    ("outcome", "code"),
    [
        (StageJobOutcome.OK, EXIT_OK),
        # waiting is not an error (contract cli.md): the job stays green.
        (StageJobOutcome.WAITING, EXIT_OK),
        (StageJobOutcome.BLOCKED, EXIT_BLOCKED),
        (StageJobOutcome.FAILED, EXIT_ERROR),
        (StageJobOutcome.INVALID_INPUT, EXIT_INVALID_INPUT),
    ],
)
def test_job_exit_code_fails_the_job_only_for_blockers(outcome: StageJobOutcome, code: int) -> None:
    assert job_exit_code(outcome) == code


@pytest.mark.parametrize(
    ("status", "outcome"),
    [
        ("succeeded", StageJobOutcome.OK),
        ("waiting", StageJobOutcome.WAITING),
        ("blocked", StageJobOutcome.BLOCKED),
        ("failed", StageJobOutcome.FAILED),
    ],
)
def test_outcome_for_status_matches_the_contract(status: str, outcome: StageJobOutcome) -> None:
    assert outcome_for_status(status) is outcome


def test_valid_stage_result_is_parsed() -> None:
    view = parse_stage_result(VALID_RESULT)
    assert view.stage == "construction"
    assert view.status == "succeeded"
    assert view.run_id == "run_01H"
    assert view.change_id == "chg_smoke"
    assert view.next_action_type == "merge"
    assert view.next_action_reason == "all machine gates passed"
    assert view.usage == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
        "cost": "0.00",
    }


@pytest.mark.parametrize(
    "data",
    [
        mutated(schema_version=2),  # bad schema_version
        mutated(schema_version="1"),
        mutated(status="in_progress"),  # not a result status
        mutated(status="pending"),
        mutated(next_action=None),
        mutated(next_action="wait_for_ci"),  # not a JSON object
        mutated(next_action={}),  # missing next_action.type
        mutated(next_action={"reason": "no type"}),
        mutated(next_action={"type": ""}),  # empty next_action.type
        mutated(next_action={"type": None}),
        mutated(run_id=""),
        mutated(change_id=None),
        mutated(stage=None),
        [],  # not a JSON object
        "not an object",
        None,
    ],
)
def test_invalid_stage_result_is_rejected(data: object) -> None:
    with pytest.raises(StageJobError):
        parse_stage_result(data)


@pytest.mark.parametrize(
    ("data", "field"),
    [
        (mutated(schema_version=2), "schema_version"),
        (mutated(status="pending"), "status"),
        (mutated(next_action={"type": ""}), "next_action.type"),
        (mutated(next_action={"reason": "no type"}), "next_action.type"),
        (mutated(run_id=""), "run_id"),
    ],
)
def test_validation_error_names_the_field(data: object, field: str) -> None:
    with pytest.raises(StageJobError) as excinfo:
        parse_stage_result(data)
    assert field in str(excinfo.value)


def test_load_stage_result_reads_the_document(tmp_path: Path) -> None:
    path = write_result(tmp_path)
    assert load_stage_result(str(path)).change_id == "chg_smoke"


def test_load_stage_result_rejects_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "stage_result.json"
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(StageJobError, match="not valid JSON"):
        load_stage_result(str(path))


def test_load_stage_result_rejects_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(StageJobError, match="cannot read stage result"):
        load_stage_result(str(tmp_path / "absent.json"))


def test_outputs_are_rendered_as_key_value_lines() -> None:
    assert render_outputs(parse_stage_result(VALID_RESULT)) == EXPECTED_OUTPUTS


def test_outputs_without_usage_are_empty_values() -> None:
    view = parse_stage_result(mutated(usage=None))
    outputs = render_outputs(view)
    assert "usage_total_tokens=\n" in outputs
    assert "usage_cost=\n" in outputs


def test_usage_tokens_fall_back_to_prompt_plus_completion() -> None:
    usage = {"prompt_tokens": 7, "completion_tokens": 3, "cost": "0.01"}
    outputs = render_outputs(parse_stage_result(mutated(usage=usage)))
    assert "usage_total_tokens=10\n" in outputs
    assert "usage_cost=0.01\n" in outputs


def test_waiting_stage_exits_zero_without_annotation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result = mutated(
        status="waiting", next_action={"type": "wait_for_ci", "reason": "gates pending"}
    )
    path = write_result(tmp_path, data=result)
    assert main([str(path)]) == EXIT_OK
    captured = capsys.readouterr()
    assert "status=waiting\n" in captured.out
    assert "next_action=wait_for_ci\n" in captured.out
    assert "::error::" not in captured.out


def test_succeeded_stage_exits_zero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = write_result(tmp_path)
    assert main([str(path)]) == EXIT_OK
    assert capsys.readouterr().out == EXPECTED_OUTPUTS


def test_blocked_stage_exits_20_with_stop_reason_annotation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result = mutated(
        status="blocked",
        next_action={"type": "stop", "outcome": "blocked", "reason": "rework limit reached"},
    )
    path = write_result(tmp_path, data=result)
    assert main([str(path)]) == EXIT_BLOCKED
    captured = capsys.readouterr()
    assert "status=blocked\n" in captured.out
    assert "::error::stage construction blocked: rework limit reached" in captured.out


def test_failed_stage_exits_1_with_stop_reason_annotation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result = mutated(
        status="failed",
        next_action={"type": "stop", "outcome": "failed", "reason": "stage execution crashed"},
    )
    path = write_result(tmp_path, data=result)
    assert main([str(path)]) == EXIT_ERROR
    assert "::error::stage construction failed: stage execution crashed" in capsys.readouterr().out


def test_blocked_annotation_falls_back_to_the_action_type(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result = mutated(status="blocked", next_action={"type": "request_approval", "gate": "code"})
    path = write_result(tmp_path, data=result)
    assert main([str(path)]) == EXIT_BLOCKED
    assert "::error::stage construction blocked: request_approval" in capsys.readouterr().out


def test_blocked_annotation_stays_single_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result = mutated(
        status="blocked",
        next_action={"type": "stop", "outcome": "blocked", "reason": "line one\nline two"},
    )
    path = write_result(tmp_path, data=result)
    assert main([str(path)]) == EXIT_BLOCKED
    assert "::error::stage construction blocked: line one line two" in capsys.readouterr().out


def test_outputs_file_receives_the_rendered_outputs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_result(tmp_path)
    outputs_file = tmp_path / "github_output.txt"
    assert main([str(path), "--outputs-file", str(outputs_file)]) == EXIT_OK
    assert outputs_file.read_text(encoding="utf-8") == EXPECTED_OUTPUTS
    assert "status=succeeded" in capsys.readouterr().out


def test_unusable_stage_result_exits_2_with_annotation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "stage_result.json"
    path.write_text("not json", encoding="utf-8")
    assert main([str(path)]) == EXIT_INVALID_INPUT
    captured = capsys.readouterr()
    assert "::error::invalid stage result:" in captured.out
    assert "not valid JSON" in captured.out


def test_missing_stage_result_exits_2_with_annotation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([str(tmp_path / "absent.json")]) == EXIT_INVALID_INPUT
    assert "::error::invalid stage result:" in capsys.readouterr().out


def test_schema_version_mismatch_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_result(tmp_path, data=mutated(schema_version=2))
    assert main([str(path)]) == EXIT_INVALID_INPUT
    assert "::error::invalid stage result:" in capsys.readouterr().out
