"""Factory Runner CLI: entry point and command parsing (T007, contract cli.md).

``factory`` is the entry point of a factory stage (ADR-006 p.1): the same core
release runs locally and in CI (FR-022), so no always-on service is needed. The
console script itself is ``dark_factory.runtime.entrypoint:main`` (ADR-025) — the
composition layer that assembles the runtime and calls this module's ``main`` with
the assembled seams; ``python -m dark_factory.cli`` runs this module directly and
is the explicit core path (deterministic executor, no composition).
This module owns the command tree (``stage run``/``stage resume``,
``run advance``/``run status``/``run publish``/``run withdraw``, ``reconcile``, ``outbox dispatch``/
``outbox replay``/``outbox skip``, ``doctor``, ``api serve``,
``release verify``, ``product add``/``product validate``/``product list``/``product show``),
option validation and exit codes.
Exit codes (contract cli.md): 0 success, 10 waiting, 20 blocked, 1 execution
error, 2 invalid input/configuration; argparse rejects invalid input with
exit code 2, matching the contract.

Handlers are dispatched from here. ``doctor`` (T008) is implemented in
``dark_factory.cli.doctor``, ``stage run`` (T009, with run-record
persistence T011, ADR-015 p.4/p.5) in ``dark_factory.cli.stage``,
``run advance``/``run status`` (T-092, the durable run driver) and
``run withdraw`` (T064, the operator withdrawal of a parked run, TD-030) in
``dark_factory.cli.runner``, ``reconcile`` (T-063, one idempotent Reconciler
pass) in ``dark_factory.cli.reconcile``, the outbox commands (T028, delivery of
outbox events per ADR-016) in ``dark_factory.cli.outbox``, ``api serve``
(T035, the REST API of contract api.md) in ``dark_factory.cli.api`` and
``release verify`` (T034, smoke + release evidence, ADR-011 p.6) in
``dark_factory.cli.release`` and ``run publish`` (T-061, the run-record index
published into ``dark-factory-runs``, ADR-015 p.4) in
``dark_factory.cli.runs`` and the product registry commands (T070, the operator
side of ADR-030: register, validate, list and show products) in
``dark_factory.cli.products``; ``stage resume`` still reports ``not_implemented``
with exit code 2 until the durable state-store wiring of the resume protocol
(ADR-006 p.8) lands.

The ``executor``/``revision_of``/``gate_facts`` seams of ``run advance`` and the
``provisioning`` seam of ``api serve``/``product validate`` are values, not
imports: this module is core and may not name ``dark_factory.runtime``
(ADR-024 p.5), so the composition root passes the bindings in
(``main``/``dispatch``/``_advance_run``/``_validate_product``).
"""

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, assert_never

from dark_factory.changes.enums import Provider, Route, Stage
from dark_factory.cli import doctor

if TYPE_CHECKING:
    # Type-only: the CLI is core and must not pull the driver (or anything it
    # imports) into the import of the command tree. The seams arrive as values.
    from dark_factory.orchestration.runner import FactsProvider, RevisionResolver, StageExecutor
    from dark_factory.ports import CiStageTogglePort, RepositoryProvisioningPort

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
class RunAdvanceArgs:
    """Arguments of ``factory run advance`` (T-092, ADR-006).

    Exactly one of ``change_id``/``run_id`` is set (argparse enforces the
    group): ``change_id`` resolves the run from the change snapshot, ``run_id``
    advances the run named directly.

    The release options (T-092 S4) are all optional and default to ``None``:
    the expected digest (``expected_digest`` or the T033 ``digest_json``
    artifact — exactly one source) feeds the GitOps promotion of a fresh
    release attempt, the observed deployment state (``observed_digest``,
    ``argo_sync``, ``argo_health``) and the smoke options feed the value-level
    release facts that resume a waiting release attempt, and ``runs_root``
    (falling back to ``DARK_FACTORY_RUNS_ROOT``) enables the run-record
    publication after a terminal advance (ADR-015 p.4). Without them the
    command behaves exactly as before S4.

    The contract options (T-016) attach the Implementation Contract of the
    advance: ``contract_json`` (a path or ``-`` for stdin) is validated and
    attached to the resolved run — idempotently, never swapping a different
    contract under it — and ``approve_contract`` records the human approval
    (ADR-011) on the loaded contract before it is attached.
    """

    change_id: str | None
    run_id: str | None
    json_output: bool
    expected_digest: str | None = None
    digest_json: str | None = None
    observed_digest: str | None = None
    argo_sync: str | None = None
    argo_health: str | None = None
    smoke_url: str | None = None
    smoke_digest_url: str | None = None
    smoke_digest_header: str | None = None
    application: str | None = None
    runs_root: str | None = None
    contract_json: str | None = None
    approve_contract: bool = False


@dataclass(frozen=True, slots=True)
class RunPublishArgs:
    """Arguments of ``factory run publish`` (T-061, ADR-015 p.4).

    ``record`` is the persisted ``run_record.json`` of a stage or release run;
    ``runs_root`` is the ``dark-factory-runs`` checkout and falls back to
    ``DARK_FACTORY_RUNS_ROOT``, resolved by the command.
    """

    record: str
    runs_root: str | None
    json_output: bool


@dataclass(frozen=True, slots=True)
class RunWithdrawArgs:
    """Arguments of ``factory run withdraw`` (T064, TD-030).

    The one operator command of the CLI: it retracts a parked run. ``reason``
    is optional free text recorded in the append-only audit log next to the
    decision; it never carries a secret.
    """

    run_id: str
    reason: str | None
    json_output: bool


@dataclass(frozen=True, slots=True)
class ProductAddArgs:
    """Arguments of ``factory product add`` (T070, ADR-030 p.1/p.6).

    ``product_id`` is client-chosen like the change id, so registration replays
    by it and a retry cannot create a second product (FR-017). The repository is
    ``provider`` + ``slug`` (``RepositoryRef``); the optional fields are the
    product's own (``description``, canonical ``repository_url``, ``baseline_ref``
    of ADR-020, ``dev_env_ref`` of the dev contour). Readiness is observed by
    ``product validate``, never submitted here.
    """

    product_id: str
    name: str
    provider: Provider
    slug: str
    description: str | None
    repository_url: str | None
    baseline_ref: str | None
    dev_env_ref: str | None
    json_output: bool


@dataclass(frozen=True, slots=True)
class ProductValidateArgs:
    """Arguments of ``factory product validate`` (T070, ADR-030 p.4, ADR-031)."""

    product_id: str
    json_output: bool


@dataclass(frozen=True, slots=True)
class ProductListArgs:
    """Arguments of ``factory product list`` (T070): the registry page ``limit``/``offset``."""

    limit: int
    offset: int
    json_output: bool


@dataclass(frozen=True, slots=True)
class ProductShowArgs:
    """Arguments of ``factory product show`` (T070): one product and its readiness."""

    product_id: str
    json_output: bool


@dataclass(frozen=True, slots=True)
class ReconcileArgs:
    """Arguments of ``factory reconcile`` (contract cli.md)."""

    json_output: bool


@dataclass(frozen=True, slots=True)
class OutboxDispatchArgs:
    """Arguments of ``factory outbox dispatch`` (contract cli.md, T028).

    ``once`` is accepted for compatibility with the contract's ``[--once]``
    option, but the command always performs exactly one dispatch pass — the
    schedule is owned by the CronJob, not by a CLI loop.
    """

    once: bool
    json_output: bool
    limit: int | None
    cleanup: bool


@dataclass(frozen=True, slots=True)
class OutboxReplayArgs:
    """Arguments of ``factory outbox replay`` (T028, ADR-016 p.5 manual replay)."""

    event_id: str
    consumer: str | None
    json_output: bool


@dataclass(frozen=True, slots=True)
class OutboxSkipArgs:
    """Arguments of ``factory outbox skip`` (T028, ADR-016 p.6 operator skip)."""

    event_id: str
    consumer: str
    json_output: bool


@dataclass(frozen=True, slots=True)
class DoctorArgs:
    """Arguments of ``factory doctor`` (contract cli.md)."""

    json_output: bool


@dataclass(frozen=True, slots=True)
class ApiServeArgs:
    """Arguments of ``factory api serve`` (T035, contract api.md)."""

    host: str
    port: int


@dataclass(frozen=True, slots=True)
class ReleaseVerifyArgs:
    """Arguments of ``factory release verify`` (T034, US5, ADR-011 p.6).

    The expected digest comes from ``expected_digest`` or from the T033
    ``image-digest.json`` artifact (``digest_json``) — exactly one source;
    the observed deployment state (``observed_digest``, ``argo_sync``,
    ``argo_health``) is passed as values — the MVP has no live Argo client.
    Evidence persistence (``evidence_dir``) requires the change snapshot
    (``change``) it indexes.
    """

    expected_digest: str | None
    digest_json: str | None
    application: str | None
    observed_digest: str | None
    argo_sync: str | None
    argo_health: str | None
    smoke_url: str | None
    smoke_digest_url: str | None
    smoke_digest_header: str | None
    evidence_dir: str | None
    change: str | None
    run_id: str | None
    json_output: bool


CommandArgs = (
    StageRunArgs
    | StageResumeArgs
    | RunStatusArgs
    | RunAdvanceArgs
    | RunPublishArgs
    | RunWithdrawArgs
    | ProductAddArgs
    | ProductValidateArgs
    | ProductListArgs
    | ProductShowArgs
    | ReconcileArgs
    | OutboxDispatchArgs
    | OutboxReplayArgs
    | OutboxSkipArgs
    | DoctorArgs
    | ApiServeArgs
    | ReleaseVerifyArgs
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

    run = commands.add_parser("run", help="Inspect a run and publish its record.")
    run_commands = run.add_subparsers(required=True, metavar="command")
    run_advance = run_commands.add_parser(
        "advance", help="Advance one run by exactly one stage (T-092)."
    )
    run_advance_target = run_advance.add_mutually_exclusive_group(required=True)
    run_advance_target.add_argument(
        "--change-id", help="Id of the change whose run should be advanced."
    )
    run_advance_target.add_argument("--run-id", help="Id of the run to advance.")
    run_advance_release = run_advance.add_argument_group("release (T-092 S4)")
    run_advance_release.add_argument(
        "--expected-digest",
        help="Immutable image digest the release stage promotes (GitOps pin).",
    )
    run_advance_release.add_argument(
        "--digest-json",
        help="Path of the T033 image-digest.json artifact; alternative to --expected-digest.",
    )
    run_advance_release.add_argument(
        "--observed-digest",
        help="Digest observed on the deployment; release fact of a waiting release stage.",
    )
    run_advance_release.add_argument(
        "--argo-sync",
        help="Raw sync status of the Argo Application; release fact of a waiting release.",
    )
    run_advance_release.add_argument(
        "--argo-health",
        help="Raw health status of the Argo Application; release fact of a waiting release.",
    )
    run_advance_release.add_argument(
        "--smoke-url",
        help="HTTP health-probe URL run after the digest/argo checks pass (FR-013).",
    )
    run_advance_release.add_argument(
        "--smoke-digest-url",
        help="HTTP digest-probe URL checked for the expected digest; requires --smoke-url.",
    )
    run_advance_release.add_argument(
        "--smoke-digest-header",
        help="Response header the digest probe checks; requires --smoke-digest-url.",
    )
    run_advance_release.add_argument(
        "--application",
        help="namespace/name of the Argo Application, recorded in the release evidence.",
    )
    run_advance_release.add_argument(
        "--runs-root",
        help="Checkout of dark-factory-runs; defaults to DARK_FACTORY_RUNS_ROOT.",
    )
    run_advance_contract = run_advance.add_argument_group("implementation contract (T-016)")
    run_advance_contract.add_argument(
        "--contract-json",
        help=(
            "Path of the implementation contract JSON ('-' = stdin); attaches it to the"
            " run, idempotently and without swapping a different one (T-016)."
        ),
    )
    run_advance_contract.add_argument(
        "--approve-contract",
        action="store_true",
        help="Record the human approval on the loaded contract (operator decision, ADR-011).",
    )
    run_advance.add_argument(
        "--json", action="store_true", help="Emit the advance outcome as JSON on stdout."
    )
    run_advance.set_defaults(command="run_advance")

    run_status = run_commands.add_parser("status", help="Show the status of a run.")
    run_status.add_argument("--run-id", required=True, help="Id of the run to inspect.")
    run_status.add_argument("--json", action="store_true", help="Emit the run record as JSON.")
    run_status.set_defaults(command="run_status")

    run_publish = run_commands.add_parser(
        "publish", help="Publish a run record into dark-factory-runs (T-061, ADR-015 p.4)."
    )
    run_publish.add_argument(
        "--record", required=True, help="Path of the persisted run record to publish."
    )
    run_publish.add_argument(
        "--runs-root",
        help="Checkout of dark-factory-runs; defaults to DARK_FACTORY_RUNS_ROOT.",
    )
    run_publish.add_argument(
        "--json", action="store_true", help="Emit the publish outcome as JSON."
    )
    run_publish.set_defaults(command="run_publish")

    run_withdraw = run_commands.add_parser(
        "withdraw", help="Withdraw (cancel) a parked run by operator decision (T064)."
    )
    run_withdraw.add_argument("--run-id", required=True, help="Id of the run to withdraw.")
    run_withdraw.add_argument(
        "--reason",
        help="Optional operator reason, recorded in the append-only audit log (T064).",
    )
    run_withdraw.add_argument(
        "--json", action="store_true", help="Emit the withdrawal outcome as JSON."
    )
    run_withdraw.set_defaults(command="run_withdraw")

    product = commands.add_parser(
        "product", help="Register, validate and inspect products (T070, ADR-030)."
    )
    product_commands = product.add_subparsers(required=True, metavar="command")
    product_add = product_commands.add_parser(
        "add", help="Register a product; a repeat with the same --id replays (FR-017)."
    )
    product_add.add_argument(
        "--id", dest="product_id", required=True, help="Client-chosen product id."
    )
    product_add.add_argument("--name", required=True, help="Human-readable product name.")
    product_add.add_argument(
        "--provider",
        required=True,
        choices=[provider.value for provider in Provider],
        help="Source-control provider of the product repository (ADR-019).",
    )
    product_add.add_argument(
        "--repository",
        dest="slug",
        required=True,
        help="Repository slug at the provider (owner/name).",
    )
    product_add.add_argument("--description", help="Optional product description.")
    product_add.add_argument(
        "--repository-url", help="Optional canonical URL of the repository (ADR-030 p.1)."
    )
    product_add.add_argument(
        "--baseline-ref",
        help="Optional reference of the canonical Product Baseline (ADR-020, .factory/product).",
    )
    product_add.add_argument(
        "--dev-env-ref", help="Optional reference of the dev delivery environment (namespace/app)."
    )
    product_add.add_argument(
        "--json", action="store_true", help="Emit the registration outcome as JSON."
    )
    product_add.set_defaults(command="product_add")

    product_validate = product_commands.add_parser(
        "validate",
        help="Observe the product repository and record its readiness (ADR-030 p.4, ADR-031).",
    )
    product_validate.add_argument(
        "--id", dest="product_id", required=True, help="Id of the product to validate."
    )
    product_validate.add_argument(
        "--json", action="store_true", help="Emit the product and the observation as JSON."
    )
    product_validate.set_defaults(command="product_validate")

    product_list = product_commands.add_parser("list", help="List the registered products.")
    product_list.add_argument(
        "--limit", type=int, default=50, help="Page size, 1..200 (default: 50)."
    )
    product_list.add_argument("--offset", type=int, default=0, help="Page offset (default: 0).")
    product_list.add_argument("--json", action="store_true", help="Emit the products as JSON.")
    product_list.set_defaults(command="product_list")

    product_show = product_commands.add_parser("show", help="Show one product and its readiness.")
    product_show.add_argument(
        "--id", dest="product_id", required=True, help="Id of the product to show."
    )
    product_show.add_argument("--json", action="store_true", help="Emit the product as JSON.")
    product_show.set_defaults(command="product_show")

    reconcile = commands.add_parser(
        "reconcile", help="Perform one idempotent Reconciler pass (ADR-019 p.5)."
    )
    reconcile.add_argument(
        "--json", action="store_true", help="Emit the reconciliation report as JSON."
    )
    reconcile.set_defaults(command="reconcile")

    outbox = commands.add_parser("outbox", help="Event delivery (ADR-016).")
    outbox_commands = outbox.add_subparsers(required=True, metavar="command")
    outbox_dispatch = outbox_commands.add_parser(
        "dispatch", help="Run one Outbox Dispatcher delivery pass (T028)."
    )
    outbox_dispatch.add_argument(
        "--once",
        action="store_true",
        help="Accepted for contract cli.md compatibility; the pass is always single.",
    )
    outbox_dispatch.add_argument(
        "--json", action="store_true", help="Emit the dispatch report as JSON."
    )
    outbox_dispatch.add_argument(
        "--limit", type=int, help="Maximum deliveries reserved in this pass."
    )
    outbox_dispatch.add_argument(
        "--cleanup",
        action="store_true",
        help="Also delete past-retention events with all deliveries terminal (ADR-016 p.9).",
    )
    outbox_dispatch.set_defaults(command="outbox_dispatch")

    outbox_replay = outbox_commands.add_parser(
        "replay", help="Reset dead/failed deliveries of one event to pending (ADR-016 p.5)."
    )
    outbox_replay.add_argument("--event-id", required=True, help="Id of the event to replay.")
    outbox_replay.add_argument(
        "--consumer", help="Limit the replay to one consumer; default: all of them."
    )
    outbox_replay.add_argument(
        "--json", action="store_true", help="Emit the replay result as JSON."
    )
    outbox_replay.set_defaults(command="outbox_replay")

    outbox_skip = outbox_commands.add_parser(
        "skip", help="Waive one delivery by operator decision (ADR-016 p.6)."
    )
    outbox_skip.add_argument("--event-id", required=True, help="Id of the event to skip.")
    outbox_skip.add_argument("--consumer", required=True, help="Consumer whose delivery is waived.")
    outbox_skip.add_argument("--json", action="store_true", help="Emit the skip result as JSON.")
    outbox_skip.set_defaults(command="outbox_skip")

    doctor = commands.add_parser("doctor", help="Check the environment and configuration.")
    doctor.add_argument("--json", action="store_true", help="Emit the doctor report as JSON.")
    doctor.set_defaults(command="doctor")

    api = commands.add_parser("api", help="Operate the factory REST API (contract api.md).")
    api_commands = api.add_subparsers(required=True, metavar="command")
    api_serve = api_commands.add_parser("serve", help="Serve the factory REST API locally (T035).")
    api_serve.add_argument("--host", default="127.0.0.1", help="Bind address of the API server.")
    api_serve.add_argument("--port", type=int, default=8000, help="TCP port of the API server.")
    api_serve.set_defaults(command="api_serve")

    release = commands.add_parser(
        "release", help="Release verification of a deployed change (US5, ADR-011 p.6)."
    )
    release_commands = release.add_subparsers(required=True, metavar="command")
    release_verify = release_commands.add_parser(
        "verify",
        help="Verify a deployed release: digest immutability, Argo status, smoke (T034).",
    )
    release_verify.add_argument(
        "--expected-digest",
        help="Expected immutable image digest of the promoted build (FR-011).",
    )
    release_verify.add_argument(
        "--digest-json",
        help="Path of the T033 image-digest.json artifact; alternative to --expected-digest.",
    )
    release_verify.add_argument(
        "--application", help="Target Argo Application (namespace/name) recorded in the evidence."
    )
    release_verify.add_argument(
        "--observed-digest", help="Digest observed on the deployment (FR-011)."
    )
    release_verify.add_argument(
        "--argo-sync", help="Raw sync status of the Argo Application (ADR-010)."
    )
    release_verify.add_argument(
        "--argo-health", help="Raw health status of the Argo Application (ADR-010)."
    )
    release_verify.add_argument(
        "--smoke-url", help="HTTP health-probe URL; any 2xx response passes (FR-013)."
    )
    release_verify.add_argument(
        "--smoke-digest-url",
        help="HTTP digest-probe URL checked for the expected digest; requires --smoke-url.",
    )
    release_verify.add_argument(
        "--smoke-digest-header",
        help="Response header carrying the digest; default: the response body.",
    )
    release_verify.add_argument(
        "--evidence-dir", help="Directory for the run record; requires --change."
    )
    release_verify.add_argument(
        "--change", help="Path of the change snapshot; requires --evidence-dir."
    )
    release_verify.add_argument(
        "--run-id", help="Existing run id; a new run is created when omitted."
    )
    release_verify.add_argument(
        "--json", action="store_true", help="Emit the release evidence as JSON on stdout."
    )
    release_verify.set_defaults(command="release_verify")

    return parser


def _option_str(data: Mapping[str, object], option: str) -> str | None:
    """Read a string option from the parsed namespace (argparse guarantees the type)."""
    value = data.get(option)
    if value is None or isinstance(value, str):
        return value
    raise AssertionError(f"option --{option.replace('_', '-')} must be a string")


def _option_int(data: Mapping[str, object], option: str) -> int | None:
    """Read an integer option from the parsed namespace (argparse guarantees the type)."""
    value = data.get(option)
    if value is None or isinstance(value, int):
        return value
    raise AssertionError(f"option --{option.replace('_', '-')} must be an integer")


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
        case "run_advance":
            return RunAdvanceArgs(
                change_id=_option_str(data, "change_id"),
                run_id=_option_str(data, "run_id"),
                json_output=_flag(data, "json"),
                expected_digest=_option_str(data, "expected_digest"),
                digest_json=_option_str(data, "digest_json"),
                observed_digest=_option_str(data, "observed_digest"),
                argo_sync=_option_str(data, "argo_sync"),
                argo_health=_option_str(data, "argo_health"),
                smoke_url=_option_str(data, "smoke_url"),
                smoke_digest_url=_option_str(data, "smoke_digest_url"),
                smoke_digest_header=_option_str(data, "smoke_digest_header"),
                application=_option_str(data, "application"),
                runs_root=_option_str(data, "runs_root"),
                contract_json=_option_str(data, "contract_json"),
                approve_contract=_flag(data, "approve_contract"),
            )
        case "run_publish":
            return RunPublishArgs(
                record=_required_str(data, "record"),
                runs_root=_option_str(data, "runs_root"),
                json_output=_flag(data, "json"),
            )
        case "run_withdraw":
            return RunWithdrawArgs(
                run_id=_required_str(data, "run_id"),
                reason=_option_str(data, "reason"),
                json_output=_flag(data, "json"),
            )
        case "product_add":
            return ProductAddArgs(
                product_id=_required_str(data, "product_id"),
                name=_required_str(data, "name"),
                provider=Provider(_required_str(data, "provider")),
                slug=_required_str(data, "slug"),
                description=_option_str(data, "description"),
                repository_url=_option_str(data, "repository_url"),
                baseline_ref=_option_str(data, "baseline_ref"),
                dev_env_ref=_option_str(data, "dev_env_ref"),
                json_output=_flag(data, "json"),
            )
        case "product_validate":
            return ProductValidateArgs(
                product_id=_required_str(data, "product_id"),
                json_output=_flag(data, "json"),
            )
        case "product_list":
            limit = _option_int(data, "limit")
            offset = _option_int(data, "offset")
            if limit is None or offset is None:
                raise AssertionError("options --limit and --offset have defaults")
            return ProductListArgs(limit=limit, offset=offset, json_output=_flag(data, "json"))
        case "product_show":
            return ProductShowArgs(
                product_id=_required_str(data, "product_id"),
                json_output=_flag(data, "json"),
            )
        case "reconcile":
            return ReconcileArgs(json_output=_flag(data, "json"))
        case "outbox_dispatch":
            return OutboxDispatchArgs(
                once=_flag(data, "once"),
                json_output=_flag(data, "json"),
                limit=_option_int(data, "limit"),
                cleanup=_flag(data, "cleanup"),
            )
        case "outbox_replay":
            return OutboxReplayArgs(
                event_id=_required_str(data, "event_id"),
                consumer=_option_str(data, "consumer"),
                json_output=_flag(data, "json"),
            )
        case "outbox_skip":
            return OutboxSkipArgs(
                event_id=_required_str(data, "event_id"),
                consumer=_required_str(data, "consumer"),
                json_output=_flag(data, "json"),
            )
        case "doctor":
            return DoctorArgs(json_output=_flag(data, "json"))
        case "api_serve":
            port = _option_int(data, "port")
            if port is None:
                raise AssertionError("option --port is required")
            return ApiServeArgs(host=_required_str(data, "host"), port=port)
        case "release_verify":
            return ReleaseVerifyArgs(
                expected_digest=_option_str(data, "expected_digest"),
                digest_json=_option_str(data, "digest_json"),
                application=_option_str(data, "application"),
                observed_digest=_option_str(data, "observed_digest"),
                argo_sync=_option_str(data, "argo_sync"),
                argo_health=_option_str(data, "argo_health"),
                smoke_url=_option_str(data, "smoke_url"),
                smoke_digest_url=_option_str(data, "smoke_digest_url"),
                smoke_digest_header=_option_str(data, "smoke_digest_header"),
                evidence_dir=_option_str(data, "evidence_dir"),
                change=_option_str(data, "change"),
                run_id=_option_str(data, "run_id"),
                json_output=_flag(data, "json"),
            )
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
    # Imported here: cli.runner imports RunStatusArgs and the exit codes from
    # this module, so a module-level import would be circular.
    from dark_factory.cli import runner

    return runner.run_status_command(args)


def _advance_run(
    args: RunAdvanceArgs,
    *,
    executor: "StageExecutor | None" = None,
    revision_of: "RevisionResolver | None" = None,
    gate_facts: "FactsProvider | None" = None,
) -> int:
    # Imported here for the same reason as ``_show_run_status``.
    from dark_factory.cli import runner

    return runner.run_advance_command(
        args, executor=executor, revision_of=revision_of, gate_facts=gate_facts
    )


def _publish_run(args: RunPublishArgs) -> int:
    # Imported here: cli.runs imports RunPublishArgs and the exit codes from this
    # module, so a module-level import would be circular.
    from dark_factory.cli import runs

    return runs.run_publish_command(args)


def _withdraw_run(args: RunWithdrawArgs) -> int:
    # Imported here for the same reason as ``_show_run_status``.
    from dark_factory.cli import runner

    return runner.run_withdraw_command(args)


def _add_product(args: ProductAddArgs) -> int:
    # Imported here: cli.products imports the product args and the exit codes
    # from this module, so a module-level import would be circular.
    from dark_factory.cli import products

    return products.run_product_add_command(args)


def _validate_product(
    args: ProductValidateArgs, *, provisioning: "RepositoryProvisioningPort | None" = None
) -> int:
    # Imported here for the same reason as ``_add_product``.
    from dark_factory.cli import products

    return products.run_product_validate_command(args, provisioning=provisioning)


def _list_products(args: ProductListArgs) -> int:
    from dark_factory.cli import products

    return products.run_product_list_command(args)


def _show_product(args: ProductShowArgs) -> int:
    from dark_factory.cli import products

    return products.run_product_show_command(args)


def _reconcile(args: ReconcileArgs) -> int:
    # Imported here: cli.reconcile imports ReconcileArgs and the exit codes
    # from this module, so a module-level import would be circular.
    from dark_factory.cli import reconcile

    return reconcile.run_reconcile_command(args)


def _dispatch_outbox(args: OutboxDispatchArgs) -> int:
    # Imported here: cli.outbox imports the args dataclasses and the exit codes
    # from this module, so a module-level import would be circular.
    from dark_factory.cli import outbox

    return outbox.run_dispatch_command(args)


def _replay_outbox(args: OutboxReplayArgs) -> int:
    from dark_factory.cli import outbox

    return outbox.run_replay_command(args)


def _skip_outbox(args: OutboxSkipArgs) -> int:
    from dark_factory.cli import outbox

    return outbox.run_skip_command(args)


def _doctor(args: DoctorArgs) -> int:
    report = doctor.collect_report()
    print(doctor.render_json(report) if args.json_output else doctor.render_text(report))
    return EXIT_INVALID_INPUT if report.has_errors else EXIT_OK


def _serve_api(
    args: ApiServeArgs,
    *,
    ci_toggles: "CiStageTogglePort | None" = None,
    ci_repository: str | None = None,
    provisioning: "RepositoryProvisioningPort | None" = None,
) -> int:
    # Imported here: cli.api imports ApiServeArgs and the exit codes from this
    # module, so a module-level import would be circular.
    from dark_factory.cli import api

    return api.run_api_serve_command(
        args, ci_toggles=ci_toggles, ci_repository=ci_repository, provisioning=provisioning
    )


def _release_verify(args: ReleaseVerifyArgs) -> int:
    # Imported here: cli.release imports ReleaseVerifyArgs and the exit codes
    # from this module, so a module-level import would be circular.
    from dark_factory.cli import release

    return release.run_release_verify_command(args)


def dispatch(
    command: CommandArgs,
    *,
    executor: "StageExecutor | None" = None,
    revision_of: "RevisionResolver | None" = None,
    gate_facts: "FactsProvider | None" = None,
    ci_toggles: "CiStageTogglePort | None" = None,
    ci_repository: str | None = None,
    provisioning: "RepositoryProvisioningPort | None" = None,
) -> int:
    """Execute one parsed command via its handler (exhaustive over the tree).

    ``executor``, ``revision_of`` and ``gate_facts`` are the optional binding
    seams of ``factory run advance`` and are consumed only by that branch;
    ``ci_toggles``/``ci_repository`` are the CI stage switchboard seam of
    ``factory api serve`` (T059), and ``provisioning`` the product-validation
    seam of ``api serve`` (T066) and ``product validate`` (T070). They are
    values, not imports: this module is
    core and must not name ``dark_factory.runtime`` (ADR-024 p.5), so the
    composition root (``runtime.entrypoint``) hands the assembled bindings over
    as arguments. Every other command ignores them, and all of them default to
    ``None`` — the deterministic stage path and the unconfigured API, as before.
    """
    match command:
        case StageRunArgs():
            return _run_stage_run(command)
        case StageResumeArgs():
            return _resume_stage(command)
        case RunStatusArgs():
            return _show_run_status(command)
        case RunAdvanceArgs():
            return _advance_run(
                command, executor=executor, revision_of=revision_of, gate_facts=gate_facts
            )
        case RunPublishArgs():
            return _publish_run(command)
        case RunWithdrawArgs():
            return _withdraw_run(command)
        case ProductAddArgs():
            return _add_product(command)
        case ProductValidateArgs():
            return _validate_product(command, provisioning=provisioning)
        case ProductListArgs():
            return _list_products(command)
        case ProductShowArgs():
            return _show_product(command)
        case ReconcileArgs():
            return _reconcile(command)
        case OutboxDispatchArgs():
            return _dispatch_outbox(command)
        case OutboxReplayArgs():
            return _replay_outbox(command)
        case OutboxSkipArgs():
            return _skip_outbox(command)
        case DoctorArgs():
            return _doctor(command)
        case ApiServeArgs():
            return _serve_api(
                command,
                ci_toggles=ci_toggles,
                ci_repository=ci_repository,
                provisioning=provisioning,
            )
        case ReleaseVerifyArgs():
            return _release_verify(command)
        case _:
            assert_never(command)


def main(
    argv: Sequence[str] | None = None,
    *,
    executor: "StageExecutor | None" = None,
    revision_of: "RevisionResolver | None" = None,
    gate_facts: "FactsProvider | None" = None,
    ci_toggles: "CiStageTogglePort | None" = None,
    ci_repository: str | None = None,
    provisioning: "RepositoryProvisioningPort | None" = None,
) -> int:
    """Run one command from ``argv``; return the process exit code.

    Not the console-script target any more: ``factory`` points at
    ``dark_factory.runtime.entrypoint:main``, the composition root that assembles
    the runtime and passes ``executor``/``revision_of``/``gate_facts`` (ADR-025)
    and the CI stage switchboard (``ci_toggles``/``ci_repository``, T059) plus the
    provisioning seam (``provisioning``, T066; also consumed by ``product
    validate``, T070). This
    function stays the command tree's own entry point, usable without seams —
    ``python -m dark_factory.cli`` is the explicit core path, and both the
    wrapper and ``__main__.py`` raise ``SystemExit`` with the returned code.

    Without ``executor``/``revision_of`` the deterministic stage path runs,
    without ``gate_facts`` a waiting stage is never resolved by observed facts
    (its checkpoint is replayed), and without ``ci_toggles`` ``api serve``
    reports the CI toggles unconfigured: this module cannot reach the
    composition root itself (ADR-024 p.5).
    """
    return dispatch(
        parse_command(argv),
        executor=executor,
        revision_of=revision_of,
        gate_facts=gate_facts,
        ci_toggles=ci_toggles,
        ci_repository=ci_repository,
        provisioning=provisioning,
    )
