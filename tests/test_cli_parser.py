"""Parsing and stub behavior of the Factory Runner CLI (T007, contract cli.md)."""

import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from dark_factory.changes.enums import Route, Stage
from dark_factory.cli.main import (
    ApiServeArgs,
    DoctorArgs,
    OutboxDispatchArgs,
    OutboxReplayArgs,
    OutboxSkipArgs,
    ReconcileArgs,
    ReleaseVerifyArgs,
    ResumeNextAction,
    RunPublishArgs,
    RunStatusArgs,
    StageResumeArgs,
    StageRunArgs,
    main,
    parse_command,
)


def test_stage_run_parses_all_options() -> None:
    args = parse_command(
        [
            "stage",
            "run",
            "--change",
            "changes/chg_01H.yaml",
            "--stage",
            "construction",
            "--route",
            "quick",
            "--input-revision",
            "a1b2c3d",
            "--run-id",
            "run_01H",
            "--json",
            "--evidence-dir",
            "evidence/run_01H",
            "--non-interactive",
        ]
    )
    assert args == StageRunArgs(
        change="changes/chg_01H.yaml",
        stage=Stage.CONSTRUCTION,
        route=Route.QUICK,
        input_revision="a1b2c3d",
        run_id="run_01H",
        json_output=True,
        evidence_dir="evidence/run_01H",
        non_interactive=True,
    )


def test_stage_run_defaults() -> None:
    args = parse_command(["stage", "run", "--change", "c.yaml", "--stage", "planning"])
    assert args == StageRunArgs(
        change="c.yaml",
        stage=Stage.PLANNING,
        route=None,
        input_revision=None,
        run_id=None,
        json_output=False,
        evidence_dir=None,
        non_interactive=False,
    )


@pytest.mark.parametrize("stage", [member.value for member in Stage])
def test_stage_run_accepts_every_stage_value(stage: str) -> None:
    args = parse_command(["stage", "run", "--change", "c.yaml", "--stage", stage])
    assert isinstance(args, StageRunArgs)
    assert args.stage == Stage(stage)


@pytest.mark.parametrize("route", [member.value for member in Route])
def test_stage_run_accepts_every_route_value(route: str) -> None:
    args = parse_command(
        ["stage", "run", "--change", "c.yaml", "--stage", "construction", "--route", route]
    )
    assert isinstance(args, StageRunArgs)
    assert args.route == Route(route)


def test_stage_resume_parses_options() -> None:
    args = parse_command(
        ["stage", "resume", "--run-id", "run_01H", "--next-action", "wa", "--json"]
    )
    assert args == StageResumeArgs(
        run_id="run_01H",
        next_action=ResumeNextAction.WA,
        json_output=True,
    )


@pytest.mark.parametrize("next_action", [member.value for member in ResumeNextAction])
def test_stage_resume_accepts_every_next_action_value(next_action: str) -> None:
    args = parse_command(["stage", "resume", "--run-id", "run_01H", "--next-action", next_action])
    assert args == StageResumeArgs(
        run_id="run_01H",
        next_action=ResumeNextAction(next_action),
        json_output=False,
    )


def test_run_status_parses_options() -> None:
    args = parse_command(["run", "status", "--run-id", "run_01H", "--json"])
    assert args == RunStatusArgs(run_id="run_01H", json_output=True)


def test_run_publish_parses_options() -> None:
    assert parse_command(
        [
            "run",
            "publish",
            "--record",
            "evidence/run_01H/run_record.json",
        ]
    ) == RunPublishArgs(
        record="evidence/run_01H/run_record.json", runs_root=None, json_output=False
    )
    assert parse_command(
        [
            "run",
            "publish",
            "--record",
            "evidence/run_01H/run_record.json",
            "--runs-root",
            "dark-factory-runs",
            "--json",
        ]
    ) == RunPublishArgs(
        record="evidence/run_01H/run_record.json",
        runs_root="dark-factory-runs",
        json_output=True,
    )


def test_reconcile_and_doctor_parse_options() -> None:
    assert parse_command(["reconcile", "--json"]) == ReconcileArgs(json_output=True)
    assert parse_command(["reconcile"]) == ReconcileArgs(json_output=False)
    assert parse_command(["doctor", "--json"]) == DoctorArgs(json_output=True)
    assert parse_command(["doctor"]) == DoctorArgs(json_output=False)


def test_api_serve_parses_options() -> None:
    assert parse_command(["api", "serve"]) == ApiServeArgs(host="127.0.0.1", port=8000)
    assert parse_command(["api", "serve", "--host", "0.0.0.0", "--port", "9000"]) == ApiServeArgs(
        host="0.0.0.0", port=9000
    )


def test_outbox_dispatch_parses_options() -> None:
    assert parse_command(["outbox", "dispatch"]) == OutboxDispatchArgs(
        once=False, json_output=False, limit=None, cleanup=False
    )
    assert parse_command(["outbox", "dispatch", "--once"]) == OutboxDispatchArgs(
        once=True, json_output=False, limit=None, cleanup=False
    )
    assert parse_command(
        ["outbox", "dispatch", "--once", "--json", "--limit", "25", "--cleanup"]
    ) == OutboxDispatchArgs(once=True, json_output=True, limit=25, cleanup=True)


def test_outbox_replay_parses_options() -> None:
    assert parse_command(["outbox", "replay", "--event-id", "evt-1"]) == OutboxReplayArgs(
        event_id="evt-1", consumer=None, json_output=False
    )
    assert parse_command(
        ["outbox", "replay", "--event-id", "evt-1", "--consumer", "tracker", "--json"]
    ) == OutboxReplayArgs(event_id="evt-1", consumer="tracker", json_output=True)


def test_outbox_skip_parses_options() -> None:
    assert parse_command(
        ["outbox", "skip", "--event-id", "evt-1", "--consumer", "tracker"]
    ) == OutboxSkipArgs(event_id="evt-1", consumer="tracker", json_output=False)
    assert parse_command(
        ["outbox", "skip", "--event-id", "evt-1", "--consumer", "tracker", "--json"]
    ) == OutboxSkipArgs(event_id="evt-1", consumer="tracker", json_output=True)


def test_release_verify_parses_all_options() -> None:
    args = parse_command(
        [
            "release",
            "verify",
            "--expected-digest",
            "sha256:abc",
            "--application",
            "apps-dev/pilot-dev",
            "--observed-digest",
            "sha256:abc",
            "--argo-sync",
            "Synced",
            "--argo-health",
            "Healthy",
            "--smoke-url",
            "http://t/healthz",
            "--smoke-digest-url",
            "http://t/version",
            "--smoke-digest-header",
            "X-Image-Digest",
            "--evidence-dir",
            "evidence/run_01H",
            "--change",
            "changes/chg_01H.yaml",
            "--run-id",
            "run_01H",
            "--json",
        ]
    )
    assert args == ReleaseVerifyArgs(
        expected_digest="sha256:abc",
        digest_json=None,
        application="apps-dev/pilot-dev",
        observed_digest="sha256:abc",
        argo_sync="Synced",
        argo_health="Healthy",
        smoke_url="http://t/healthz",
        smoke_digest_url="http://t/version",
        smoke_digest_header="X-Image-Digest",
        evidence_dir="evidence/run_01H",
        change="changes/chg_01H.yaml",
        run_id="run_01H",
        json_output=True,
    )


def test_release_verify_parses_the_digest_json_alternative() -> None:
    args = parse_command(["release", "verify", "--digest-json", "artifacts/image-digest.json"])
    assert args == ReleaseVerifyArgs(
        expected_digest=None,
        digest_json="artifacts/image-digest.json",
        application=None,
        observed_digest=None,
        argo_sync=None,
        argo_health=None,
        smoke_url=None,
        smoke_digest_url=None,
        smoke_digest_header=None,
        evidence_dir=None,
        change=None,
        run_id=None,
        json_output=False,
    )


def test_release_verify_defaults() -> None:
    """Every option is optional at the parse level; the command validates the rest."""
    assert parse_command(["release", "verify"]) == ReleaseVerifyArgs(
        expected_digest=None,
        digest_json=None,
        application=None,
        observed_digest=None,
        argo_sync=None,
        argo_health=None,
        smoke_url=None,
        smoke_digest_url=None,
        smoke_digest_header=None,
        evidence_dir=None,
        change=None,
        run_id=None,
        json_output=False,
    )


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["stage"],
        ["run"],
        ["outbox"],
        ["api"],
        ["release"],
        ["stage", "run", "--stage", "construction"],
        ["stage", "run", "--change", "c.yaml"],
        ["stage", "resume"],
        ["stage", "resume", "--run-id", "run_01H"],
        ["run", "status"],
        ["run", "publish"],
        ["outbox", "replay"],
        ["outbox", "replay", "--consumer", "tracker"],
        ["outbox", "skip"],
        ["outbox", "skip", "--event-id", "evt-1"],
    ],
)
def test_missing_required_argument_exits_with_code_2(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        parse_command(argv)
    assert excinfo.value.code == 2


@pytest.mark.parametrize(
    "argv",
    [
        ["stage", "run", "--change", "c.yaml", "--stage", "bogus"],
        ["stage", "run", "--change", "c.yaml", "--stage", "construction", "--route", "fast"],
        ["stage", "resume", "--run-id", "run_01H", "--next-action", "manual"],
    ],
)
def test_invalid_choice_exits_with_code_2(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        parse_command(argv)
    assert excinfo.value.code == 2


@pytest.mark.parametrize(
    "argv",
    [
        ["--help"],
        ["stage", "--help"],
        ["stage", "run", "--help"],
        ["stage", "resume", "--help"],
        ["run", "--help"],
        ["run", "status", "--help"],
        ["run", "publish", "--help"],
        ["reconcile", "--help"],
        ["outbox", "--help"],
        ["outbox", "dispatch", "--help"],
        ["outbox", "replay", "--help"],
        ["outbox", "skip", "--help"],
        ["doctor", "--help"],
        ["release", "--help"],
        ["release", "verify", "--help"],
    ],
)
def test_help_exits_with_code_0(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        parse_command(argv)
    assert excinfo.value.code == 0
    assert "usage:" in capsys.readouterr().out


STUB_INVOCATIONS = [
    (
        ["stage", "resume", "--run-id", "run_01H", "--next-action", "wa"],
        "stage resume",
        "the durable state-store wiring",
    ),
    (["run", "status", "--run-id", "run_01H"], "run status", "the durable state-store wiring"),
]


@pytest.mark.parametrize(("argv", "command", "task"), STUB_INVOCATIONS)
def test_stub_reports_not_implemented_on_stderr(
    argv: list[str], command: str, task: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(argv) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"factory {command}: not implemented yet (planned in {task})\n"


@pytest.mark.parametrize(
    ("argv", "command"),
    [
        (["stage", "resume", "--run-id", "r", "--next-action", "ci", "--json"], "stage resume"),
        (["run", "status", "--run-id", "run_01H", "--json"], "run status"),
    ],
)
def test_stub_json_emits_json_error_on_stdout(
    argv: list[str], command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(argv) == 2
    captured = capsys.readouterr()
    assert captured.err == ""
    payload: object = json.loads(captured.out)
    assert payload == {"error": "not_implemented", "command": command}


def test_factory_script_is_declared_in_pyproject() -> None:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    assert data["project"]["scripts"] == {"factory": "dark_factory.cli.main:main"}


def test_python_dash_m_invocation_exits_with_code_2() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "dark_factory.cli",
            "stage",
            "resume",
            "--run-id",
            "r",
            "--next-action",
            "wa",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 2
    assert result.stdout == '{"error": "not_implemented", "command": "stage resume"}\n'
    assert result.stderr == ""
