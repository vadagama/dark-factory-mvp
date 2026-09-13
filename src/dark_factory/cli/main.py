"""Factory Runner CLI: entry point and command parsing (T007, contract cli.md).

``factory`` is the entry point of a factory stage (ADR-006 p.1): the same core
release runs locally and in CI (FR-022), so no always-on service is needed.
This module owns the command tree (``stage run``/``stage resume``,
``run status``, ``reconcile``, ``outbox dispatch``, ``doctor``), option
validation and exit codes. Exit codes (contract cli.md): 0 success,
10 waiting, 20 blocked, 1 execution error, 2 invalid input/configuration;
argparse rejects invalid input with exit code 2, matching the contract.

Handlers are dispatched from here. ``doctor`` (T008) is implemented in
``dark_factory.cli.doctor`` and ``stage run`` (T009, with run-record
persistence T011, ADR-015 p.4/p.5) in ``dark_factory.cli.stage``; the
remaining handlers arrive in later tasks (``stage resume`` and ``run status``
with the durable state-store wiring; reconcile T027; outbox dispatch T028)
and report ``not_implemented`` with exit code 2 until then.
"""

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import assert_never

from dark_factory.changes.enums import Route, Stage
from dark_factory.cli import doctor

# Exit codes of the CLI (contract cli.md).
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_INVALID_INPUT = 2
EXIT_WAITING = 10
EXIT_BLOCKED = 20


class ResumeNextAction(StrEnum):
    """Continuation chosen by ``factory stage resume`` (contract cli.md)."""

    WA = "wa"
    CI = "ci"
    INPUT = "input"


@dataclass(frozen=True, slots=True)
class StageRunArgs:
    """Arguments of ``factory stage run`` (contract cli.md)."""

    change: str
    stage: Stage
    route: Route | None
    input_revision: str | None
    run_id: str | None
    json_output: bool
    evidence_dir: str | None
    non_interactive: bool


@dataclass(frozen=True, slots=True)
class StageResumeArgs:
    """Arguments of ``factory stage resume`` (contract cli.md)."""

    run_id: str
    next_action: ResumeNextAction
    json_output: bool


@dataclass(frozen=True, slots=True)
class RunStatusArgs:
    """Arguments of ``factory run status`` (contract cli.md)."""

    run_id: str
    json_output: bool


@dataclass(frozen=True, slots=True)
class ReconcileArgs:
    """Arguments of ``factory reconcile`` (contract cli.md)."""

    json_output: bool


@dataclass(frozen=True, slots=True)
class OutboxDispatchArgs:
    """Arguments of ``factory outbox dispatch`` (contract cli.md)."""

    once: bool


@dataclass(frozen=True, slots=True)
class DoctorArgs:
    """Arguments of ``factory doctor`` (contract cli.md)."""

    json_output: bool


CommandArgs = (
    StageRunArgs | StageResumeArgs | RunStatusArgs | ReconcileArgs | OutboxDispatchArgs | DoctorArgs
)


def build_parser() -> argparse.ArgumentParser:
    """Build the ``factory`` argument parser (command tree per contract cli.md)."""
    parser = argparse.ArgumentParser(
        prog="factory",
        description="Factory Runner: execute one stage of the dark factory (ADR-006).",
    )
    commands = parser.add_subparsers(required=True, metavar="command")

    stage = commands.add_parser("stage", help="Run or resume a factory stage.")
    stage_commands = stage.add_subparsers(required=True, metavar="command")

    stage_run = stage_commands.add_parser(
        "run", help="Execute one stage of a change and persist its StageResult."
    )
    stage_run.add_argument("--change", required=True, help="Path or ref of the change snapshot.")
    stage_run.add_argument(
        "--stage",
        required=True,
        choices=[member.value for member in Stage],
        help="Stage to execute.",
    )
    stage_run.add_argument(
        "--route",
        choices=[member.value for member in Route],
        help="Factory Flow route; the default is decided by the stage runner (ADR-005).",
    )
    stage_run.add_argument(
        "--input-revision",
        help="Input revision of the stage; computed from the input snapshot when omitted.",
    )
    stage_run.add_argument("--run-id", help="Existing run id; a new run is created when omitted.")
    stage_run.add_argument(
        "--json", action="store_true", help="Emit the StageResult as JSON on stdout."
    )
    stage_run.add_argument(
        "--evidence-dir", help="Directory for the run record and evidence artifacts."
    )
    stage_run.add_argument(
        "--non-interactive", action="store_true", help="Forbid interactive prompts (CI)."
    )
    stage_run.set_defaults(command="stage_run")

    stage_resume = stage_commands.add_parser(
        "resume", help="Resume a waiting run with the chosen next action."
    )
    stage_resume.add_argument("--run-id", required=True, help="Id of the run to resume.")
    stage_resume.add_argument(
        "--next-action",
        required=True,
        choices=[member.value for member in ResumeNextAction],
        help="Continuation chosen for the waiting run.",
    )
    stage_resume.add_argument(
        "--json", action="store_true", help="Emit the StageResult as JSON on stdout."
    )
    stage_resume.set_defaults(command="stage_resume")

    run = commands.add_parser("run", help="Inspect a run.")
    run_commands = run.add_subparsers(required=True, metavar="command")
    run_status = run_commands.add_parser("status", help="Show the status of a run.")
    run_status.add_argument("--run-id", required=True, help="Id of the run to inspect.")
    run_status.add_argument("--json", action="store_true", help="Emit the run record as JSON.")
    run_status.set_defaults(command="run_status")

    reconcile = commands.add_parser(
        "reconcile", help="Perform one idempotent Reconciler pass (ADR-019 p.5)."
    )
    reconcile.add_argument(
        "--json", action="store_true", help="Emit the reconciliation report as JSON."
    )
    reconcile.set_defaults(command="reconcile")

    outbox = commands.add_parser("outbox", help="Event delivery (ADR-016).")
    outbox_commands = outbox.add_subparsers(required=True, metavar="command")
    outbox_dispatch = outbox_commands.add_parser("dispatch", help="Deliver pending outbox events.")
    outbox_dispatch.add_argument(
        "--once", action="store_true", help="Perform a single delivery pass instead of looping."
    )
    outbox_dispatch.set_defaults(command="outbox_dispatch")

    doctor = commands.add_parser("doctor", help="Check the environment and configuration.")
    doctor.add_argument("--json", action="store_true", help="Emit the doctor report as JSON.")
    doctor.set_defaults(command="doctor")

    return parser


def _option_str(data: Mapping[str, object], option: str) -> str | None:
    """Read a string option from the parsed namespace (argparse guarantees the type)."""
    value = data.get(option)
    if value is None or isinstance(value, str):
        return value
    raise AssertionError(f"option --{option.replace('_', '-')} must be a string")


def _required_str(data: Mapping[str, object], option: str) -> str:
    value = _option_str(data, option)
    if value is None:
        raise AssertionError(f"option --{option.replace('_', '-')} is required")
    return value


def _flag(data: Mapping[str, object], option: str) -> bool:
    value = data.get(option)
    if isinstance(value, bool):
        return value
    raise AssertionError(f"option --{option.replace('_', '-')} must be a flag")


def build_command_args(ns: argparse.Namespace) -> CommandArgs:
    """Convert the flat argparse namespace into a typed per-command record."""
    data: Mapping[str, object] = vars(ns)
    match data.get("command"):
        case "stage_run":
            route = _option_str(data, "route")
            return StageRunArgs(
                change=_required_str(data, "change"),
                stage=Stage(_required_str(data, "stage")),
                route=Route(route) if route is not None else None,
                input_revision=_option_str(data, "input_revision"),
                run_id=_option_str(data, "run_id"),
                json_output=_flag(data, "json"),
                evidence_dir=_option_str(data, "evidence_dir"),
                non_interactive=_flag(data, "non_interactive"),
            )
        case "stage_resume":
            return StageResumeArgs(
                run_id=_required_str(data, "run_id"),
                next_action=ResumeNextAction(_required_str(data, "next_action")),
                json_output=_flag(data, "json"),
            )
        case "run_status":
            return RunStatusArgs(
                run_id=_required_str(data, "run_id"),
                json_output=_flag(data, "json"),
            )
        case "reconcile":
            return ReconcileArgs(json_output=_flag(data, "json"))
        case "outbox_dispatch":
            return OutboxDispatchArgs(once=_flag(data, "once"))
        case "doctor":
            return DoctorArgs(json_output=_flag(data, "json"))
        case _:
            raise AssertionError(f"unknown command: {data.get('command')!r}")


def parse_command(argv: Sequence[str] | None = None) -> CommandArgs:
    """Parse ``argv`` (``sys.argv[1:]`` by default) into a typed command record."""
    return build_command_args(build_parser().parse_args(argv))


def _not_implemented(command: str, planned_task: str, *, json_output: bool) -> int:
    """Stub for commands implemented in later tasks: report and fail with code 2."""
    if json_output:
        payload = {"error": "not_implemented", "command": command}
        print(json.dumps(payload))
    else:
        print(
            f"factory {command}: not implemented yet (planned in {planned_task})",
            file=sys.stderr,
        )
    return EXIT_INVALID_INPUT


def _run_stage_run(args: StageRunArgs) -> int:
    # Imported here: cli.stage imports StageRunArgs and the exit codes from this
    # module, so a module-level import would be circular.
    from dark_factory.cli import stage

    return stage.run_stage_command(args)


def _resume_stage(args: StageResumeArgs) -> int:
    return _not_implemented(
        "stage resume", "the durable state-store wiring", json_output=args.json_output
    )


def _show_run_status(args: RunStatusArgs) -> int:
    return _not_implemented(
        "run status", "the durable state-store wiring", json_output=args.json_output
    )


def _reconcile(args: ReconcileArgs) -> int:
    return _not_implemented("reconcile", "T027", json_output=args.json_output)


def _dispatch_outbox(args: OutboxDispatchArgs) -> int:
    return _not_implemented("outbox dispatch", "T028", json_output=False)


def _doctor(args: DoctorArgs) -> int:
    report = doctor.collect_report()
    print(doctor.render_json(report) if args.json_output else doctor.render_text(report))
    return EXIT_INVALID_INPUT if report.has_errors else EXIT_OK


def dispatch(command: CommandArgs) -> int:
    """Execute one parsed command via its handler (exhaustive over the tree)."""
    match command:
        case StageRunArgs():
            return _run_stage_run(command)
        case StageResumeArgs():
            return _resume_stage(command)
        case RunStatusArgs():
            return _show_run_status(command)
        case ReconcileArgs():
            return _reconcile(command)
        case OutboxDispatchArgs():
            return _dispatch_outbox(command)
        case DoctorArgs():
            return _doctor(command)
        case _:
            assert_never(command)


def main(argv: Sequence[str] | None = None) -> int:
    """Console-script entry point (``factory``, pyproject ``[project.scripts]``).

    Returns the process exit code; the console-script wrapper (and
    ``python -m dark_factory.cli``) raises ``SystemExit`` with it.
    """
    return dispatch(parse_command(argv))
