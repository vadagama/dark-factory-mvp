"""Parsing and stub behavior of the Factory Runner CLI (T007, contract cli.md)."""

import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from dark_factory.changes.enums import Phase, Provider, RiskClass, Route, Scenario, Stage
from dark_factory.cli.main import (
    EXIT_INVALID_INPUT,
    ApiServeArgs,
    ArtifactAction,
    ChangeAnswerArgs,
    ChangeApproveArgs,
    ChangeArtifactsArgs,
    ChangeCommentArgs,
    ChangeCreateArgs,
    ChangeReworkArgs,
    ChangeStatusArgs,
    DoctorArgs,
    OutboxDispatchArgs,
    OutboxReplayArgs,
    OutboxSkipArgs,
    ProductAddArgs,
    ProductBootstrapArgs,
    ProductListArgs,
    ProductShowArgs,
    ProductValidateArgs,
    ReconcileArgs,
    ReleaseVerifyArgs,
    ResumeNextAction,
    RunAdvanceArgs,
    RunPublishArgs,
    RunStatusArgs,
    RunWithdrawArgs,
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


def test_run_advance_parses_exactly_one_target() -> None:
    assert parse_command(["run", "advance", "--change-id", "chg_01H"]) == RunAdvanceArgs(
        change_id="chg_01H", run_id=None, json_output=False
    )
    assert parse_command(["run", "advance", "--run-id", "run_01H", "--json"]) == RunAdvanceArgs(
        change_id=None, run_id="run_01H", json_output=True
    )


def test_run_advance_parses_the_release_options() -> None:
    args = parse_command(
        [
            "run",
            "advance",
            "--run-id",
            "run_01H",
            "--expected-digest",
            "sha256:abc",
            "--observed-digest",
            "sha256:abc",
            "--argo-sync",
            "Synced",
            "--argo-health",
            "Healthy",
            "--smoke-url",
            "https://app.example/health",
            "--smoke-digest-url",
            "https://app.example/version",
            "--smoke-digest-header",
            "X-Version",
            "--application",
            "factory/app",
            "--runs-root",
            "/tmp/runs",
            "--json",
        ]
    )
    assert args == RunAdvanceArgs(
        change_id=None,
        run_id="run_01H",
        json_output=True,
        expected_digest="sha256:abc",
        observed_digest="sha256:abc",
        argo_sync="Synced",
        argo_health="Healthy",
        smoke_url="https://app.example/health",
        smoke_digest_url="https://app.example/version",
        smoke_digest_header="X-Version",
        application="factory/app",
        runs_root="/tmp/runs",
    )


def test_run_advance_parses_the_contract_options() -> None:
    args = parse_command(
        [
            "run",
            "advance",
            "--run-id",
            "run_01H",
            "--contract-json",
            "-",
            "--approve-contract",
        ]
    )
    assert args == RunAdvanceArgs(
        change_id=None,
        run_id="run_01H",
        json_output=False,
        contract_json="-",
        approve_contract=True,
    )


@pytest.mark.parametrize(
    "argv",
    [
        ["run", "advance"],
        ["run", "advance", "--change-id", "chg_01H", "--run-id", "run_01H"],
    ],
)
def test_run_advance_rejects_a_missing_or_ambiguous_target(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        parse_command(argv)

    # argparse leaves with the contract's invalid-input code (cli.md).
    assert exit_info.value.code == 2


def test_run_withdraw_parses_options() -> None:
    assert parse_command(["run", "withdraw", "--run-id", "run_01H"]) == RunWithdrawArgs(
        run_id="run_01H", reason=None, json_output=False
    )
    assert parse_command(
        ["run", "withdraw", "--run-id", "run_01H", "--reason", "invalid run", "--json"]
    ) == RunWithdrawArgs(run_id="run_01H", reason="invalid run", json_output=True)


def test_product_add_parses_all_options() -> None:
    args = parse_command(
        [
            "product",
            "add",
            "--id",
            "prd-calc",
            "--name",
            "Calculator",
            "--provider",
            "github",
            "--repository",
            "small/calculator",
            "--description",
            "the pilot calculator",
            "--repository-url",
            "https://github.com/small/calculator",
            "--baseline-ref",
            ".factory/product",
            "--dev-env-ref",
            "apps-dev/calculator",
            "--json",
        ]
    )
    assert args == ProductAddArgs(
        product_id="prd-calc",
        name="Calculator",
        provider=Provider.GITHUB,
        slug="small/calculator",
        description="the pilot calculator",
        repository_url="https://github.com/small/calculator",
        baseline_ref=".factory/product",
        dev_env_ref="apps-dev/calculator",
        json_output=True,
    )


def test_product_add_optional_fields_default_to_none() -> None:
    args = parse_command(
        [
            "product",
            "add",
            "--id",
            "prd-calc",
            "--name",
            "Calculator",
            "--provider",
            "gitlab",
            "--repository",
            "team/calc",
        ]
    )
    assert args == ProductAddArgs(
        product_id="prd-calc",
        name="Calculator",
        provider=Provider.GITLAB,
        slug="team/calc",
        description=None,
        repository_url=None,
        baseline_ref=None,
        dev_env_ref=None,
        json_output=False,
    )


def test_product_validate_show_and_list_parse_options() -> None:
    assert parse_command(["product", "validate", "--id", "prd-calc"]) == ProductValidateArgs(
        product_id="prd-calc", json_output=False
    )
    assert parse_command(["product", "validate", "--id", "prd-calc", "--json"]) == (
        ProductValidateArgs(product_id="prd-calc", json_output=True)
    )
    assert parse_command(["product", "show", "--id", "prd-calc", "--json"]) == ProductShowArgs(
        product_id="prd-calc", json_output=True
    )
    assert parse_command(["product", "list"]) == ProductListArgs(
        limit=50, offset=0, json_output=False
    )
    assert parse_command(["product", "list", "--limit", "5", "--offset", "10", "--json"]) == (
        ProductListArgs(limit=5, offset=10, json_output=True)
    )


def test_change_create_parses_all_options() -> None:
    args = parse_command(
        [
            "change",
            "create",
            "--product",
            "prd-calc",
            "--title",
            "Percent button",
            "--problem",
            "no percent",
            "--goal",
            "percent works",
            "--constraint",
            "keep keyboard",
            "--constraint",
            "no new deps",
            "--out-of-scope",
            "scientific mode",
            "--scenario",
            "specs_only",
            "--limit-usd",
            "12.50",
            "--token-limit",
            "50000",
            "--risk-class",
            "R2",
            "--description",
            "desc",
            "--id",
            "chg_abc",
            "--json",
        ]
    )
    assert args == ChangeCreateArgs(
        product_id="prd-calc",
        title="Percent button",
        problem="no percent",
        goal="percent works",
        constraints=("keep keyboard", "no new deps"),
        out_of_scope=("scientific mode",),
        brief_json=None,
        scenario=Scenario.SPECS_ONLY,
        limit_usd="12.50",
        token_limit=50000,
        risk_class=RiskClass.R2,
        description="desc",
        change_id="chg_abc",
        json_output=True,
    )


def test_change_create_defaults_and_brief_json() -> None:
    args = parse_command(
        [
            "change",
            "create",
            "--product",
            "prd-calc",
            "--title",
            "T",
            "--limit-usd",
            "5",
            "--brief-json",
            "-",
        ]
    )
    assert args == ChangeCreateArgs(
        product_id="prd-calc",
        title="T",
        problem=None,
        goal=None,
        constraints=(),
        out_of_scope=(),
        brief_json="-",
        scenario=Scenario.FULL,
        limit_usd="5",
        token_limit=None,
        risk_class=RiskClass.R1,
        description=None,
        change_id=None,
        json_output=False,
    )


def test_change_status_parses_options() -> None:
    assert parse_command(["change", "status", "--id", "chg_abc", "--json"]) == ChangeStatusArgs(
        change_id="chg_abc", json_output=True
    )


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
        ["run", "withdraw"],
        ["product"],
        ["product", "add"],
        ["product", "add", "--id", "prd-1", "--name", "Calc", "--provider", "github"],
        ["product", "add", "--id", "prd-1", "--name", "Calc", "--repository", "a/b"],
        ["product", "validate"],
        ["product", "show"],
        ["change"],
        ["change", "create"],
        ["change", "create", "--product", "prd-1", "--title", "T"],
        ["change", "create", "--product", "prd-1", "--limit-usd", "5"],
        ["change", "status"],
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
        [
            "product",
            "add",
            "--id",
            "prd-1",
            "--name",
            "Calc",
            "--provider",
            "bitbucket",
            "--repository",
            "a/b",
        ],
        ["product", "list", "--limit", "many"],
        [
            "change",
            "create",
            "--product",
            "p",
            "--title",
            "T",
            "--limit-usd",
            "5",
            "--scenario",
            "x",
        ],
        [
            "change",
            "create",
            "--product",
            "p",
            "--title",
            "T",
            "--limit-usd",
            "5",
            "--risk-class",
            "R9",
        ],
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
        ["run", "withdraw", "--help"],
        ["product", "--help"],
        ["product", "add", "--help"],
        ["product", "validate", "--help"],
        ["product", "list", "--help"],
        ["product", "show", "--help"],
        ["change", "--help"],
        ["change", "create", "--help"],
        ["change", "status", "--help"],
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


@pytest.mark.parametrize(
    "argv",
    [
        ["stage", "resume", "--run-id", "run_01H", "--next-action", "wa"],
    ],
)
def test_stub_reports_not_implemented_on_stderr(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(argv) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert (
        captured.err
        == "factory stage resume: not implemented yet (planned in the durable state-store wiring)\n"
    )


@pytest.mark.parametrize(
    "argv",
    [
        ["stage", "resume", "--run-id", "r", "--next-action", "ci", "--json"],
    ],
)
def test_stub_json_emits_json_error_on_stdout(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(argv) == 2
    captured = capsys.readouterr()
    assert captured.err == ""
    payload: object = json.loads(captured.out)
    assert payload == {"error": "not_implemented", "command": "stage resume"}


def test_factory_script_is_declared_in_pyproject() -> None:
    # The console script points at the composition root, not at the core CLI: the
    # CLI may not import ``dark_factory.runtime`` (ADR-024 p.5), so the binding
    # into ``run advance`` has to start in the runtime (ADR-025).
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    assert data["project"]["scripts"] == {"factory": "dark_factory.runtime.entrypoint:main"}


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


# --- M2 discussion and artifact commands (T086) ------------------------------------------


def test_change_answer_comment_rework_parse_options() -> None:
    assert parse_command(
        ["change", "answer", "--id", "chg_1", "--question", "q_1", "--value", "half up", "--json"]
    ) == ChangeAnswerArgs(
        change_id="chg_1", question_id="q_1", value="half up", comment=None, json_output=True
    )
    assert parse_command(
        [
            "change",
            "comment",
            "--id",
            "chg_1",
            "--artifact",
            "spec/requirements/REQ-001.md",
            "--anchor",
            "AC-2",
            "--body",
            "too vague",
            "--phase",
            "requirements",
        ]
    ) == ChangeCommentArgs(
        change_id="chg_1",
        artifact="spec/requirements/REQ-001.md",
        anchor_id="AC-2",
        body="too vague",
        phase=Phase.REQUIREMENTS,
        json_output=False,
    )
    assert parse_command(
        [
            "change",
            "rework",
            "--id",
            "chg_1",
            "--comment",
            "cmt_1",
            "--comment",
            "cmt_2",
            "--question",
            "q_1",
            "--instruction",
            "tighten",
        ]
    ) == ChangeReworkArgs(
        change_id="chg_1",
        phase=None,
        comment_ids=("cmt_1", "cmt_2"),
        question_ids=("q_1",),
        instruction="tighten",
        json_output=False,
    )


def test_change_approve_and_artifacts_parse_options() -> None:
    assert parse_command(
        [
            "change",
            "approve",
            "--id",
            "chg_1",
            "--phase",
            "requirements",
            "--waive",
            "--comment",
            "no UI",
        ]
    ) == ChangeApproveArgs(
        change_id="chg_1",
        phase=Phase.REQUIREMENTS,
        waive=True,
        comment="no UI",
        revision=None,
        json_output=False,
    )
    assert parse_command(
        [
            "change",
            "artifacts",
            "diff",
            "--id",
            "chg_1",
            "--path",
            "spec/x.md",
            "--from",
            "r1",
            "--to",
            "r2",
            "--json",
        ]
    ) == ChangeArtifactsArgs(
        change_id="chg_1",
        action=ArtifactAction.DIFF,
        path="spec/x.md",
        revision=None,
        from_revision="r1",
        to_revision="r2",
        file=None,
        base_revision=None,
        json_output=True,
    )
    assert parse_command(
        [
            "change",
            "artifacts",
            "edit",
            "--id",
            "chg_1",
            "--path",
            "spec/x.md",
            "--file",
            "-",
            "--base-revision",
            "r1",
        ]
    ) == ChangeArtifactsArgs(
        change_id="chg_1",
        action=ArtifactAction.EDIT,
        path="spec/x.md",
        revision=None,
        from_revision=None,
        to_revision=None,
        file="-",
        base_revision="r1",
        json_output=False,
    )
    assert parse_command(
        ["product", "bootstrap", "--id", "prd-1", "--pack", "product-baseline"]
    ) == (ProductBootstrapArgs(product_id="prd-1", packs=("product-baseline",), json_output=False))


@pytest.mark.parametrize(
    "argv",
    [
        ["change", "answer", "--id", "chg_1", "--question", "q_1"],
        ["change", "comment", "--id", "chg_1", "--artifact", "spec/x.md"],
        ["change", "approve", "--id", "chg_1", "--phase", "not-a-phase"],
        ["change", "artifacts", "rename", "--id", "chg_1"],
        ["product", "bootstrap"],
    ],
)
def test_m2_commands_reject_invalid_input(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        parse_command(argv)
    assert excinfo.value.code == EXIT_INVALID_INPUT
