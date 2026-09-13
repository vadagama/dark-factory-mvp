"""``factory stage run``: fix the input snapshot, execute one stage, report (T009).

Pipeline of the command (contract cli.md):

1. **Input snapshot** (FR-001): the ``--change`` file is read and parsed into
   a domain :class:`Change` *before* any stage work. An unreadable file or a
   schema mismatch exits with code 2 — the stage does not start. With
   ``--evidence-dir`` the raw snapshot file is copied byte-exact into
   ``<evidence-dir>/change_snapshot.yaml`` before execution begins.
2. **input_revision**: ``--input-revision`` when given, otherwise derived
   from the snapshot (see :func:`compute_input_revision`).
3. **operation_key** (ADR-006 p.3): ``operation_key(run_id, stage,
   input_revision)``. The run id comes from ``--run-id`` or is generated as
   ``run_<uuid4 hex>``. The key is part of the output/log (it is not a
   secret): in the text summary line and, in ``--json`` mode, as an
   ``operation_key=...`` line on stderr — stdout stays exactly one
   StageResult document.
4. **Idempotent replay** (T012, FR-017, ADR-006 p.3): with ``--evidence-dir``
   the committed StageResult of the operation is looked up before any
   execution. A ``succeeded``/``waiting`` result is returned as-is — replay:
   no second execution, the evidence stays untouched, the run manifest is
   not collected. A ``failed``/``blocked`` result or no committed result
   leads to a fresh execution — a new attempt of the same logical operation,
   whose external effects are deduplicated at the effect level
   (``orchestration.state.ensure_effect``). Without ``--evidence-dir`` there
   is nothing durable to check and the command always executes.
5. **Stage execution**: deterministic stage steps (T010,
   ``orchestration/stages/``): context assembly from the fixed snapshot,
   machine checks (budget/limit exhaustion, required-gate applicability,
   change-request availability), aggregation and the waiting/blocked
   decision. The deterministic path calls no harness/LLM (ADR-003) and
   evaluates no gate on a product SHA: machine gate execution runs in CI
   (FR-009), so required gates are reported ``pending`` and the attempt
   never claims ``succeeded`` (FR-009, SC-004). Run-state persistence
   arrives with the durable state-store wiring: the default budget snapshot
   applies and ``attempt_number`` stays 1 until then.
6. **Persist before wait** (ADR-006 p.8): with ``--evidence-dir`` the
   serialized StageResult is written to ``<evidence-dir>/stage_result.json``
   before the process exits with code 10. When that write fails the command
   exits with code 1: code 10 would claim a persisted result that does not
   exist.
7. **Run record** (T011, ADR-015 p.4/p.5): with ``--evidence-dir`` the run
   record — the compact immutable evidence index — is persisted to
   ``<evidence-dir>/run_record.json`` next to the snapshot and the
   StageResult. Its manifest references exact commits (environment, then the
   git HEAD), never ``latest``; when no commit can be determined the stage
   does not start (exit 2). ``factory stage resume`` and ``run status``
   handlers that consume the record arrive with the durable state-store
   wiring — they are not part of T011.

Exit codes (contract cli.md): 0 succeeded, 10 waiting, 20 blocked, 1 failed
(execution error), 2 invalid input/configuration (the stage did not start —
unreadable snapshot, schema mismatch, blank ``--run-id``/``--input-revision``,
unusable evidence directory, unresolvable commit refs). Secrets never reach
the output (ADR-009): only paths, identifiers and domain stage data are
printed. ``--route`` (default: standard) fixes gate applicability (ADR-005);
``--non-interactive`` is accepted for CI runs.
"""

import hashlib
import json
import os
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import yaml
from pydantic import ValidationError

from dark_factory.changes.enums import Route, Stage, StageStatus
from dark_factory.changes.keys import operation_key
from dark_factory.changes.next_action import StopAction, WaitForInputAction
from dark_factory.changes.run import Change, StageResult
from dark_factory.changes.run_records import to_json
from dark_factory.changes.usage import BudgetSnapshot
from dark_factory.cli.main import (
    EXIT_BLOCKED,
    EXIT_ERROR,
    EXIT_INVALID_INPUT,
    EXIT_OK,
    EXIT_WAITING,
    StageRunArgs,
)
from dark_factory.cli.run_records import (
    RunRecordError,
    build_run_record,
    collect_run_manifest,
    persist_run_record,
)
from dark_factory.orchestration.idempotency import (
    REPLAYABLE_RESULT_STATUSES,
    EvidenceOperationStore,
)
from dark_factory.orchestration.stages import build_context, run_deterministic_stage

SNAPSHOT_EVIDENCE_NAME: Final[str] = "change_snapshot.yaml"
"""Evidence file with the byte-exact copy of the input snapshot (FR-001)."""

DEFAULT_STAGE_ROUTE: Final[Route] = Route.STANDARD
"""Route applied when ``--route`` is omitted (contract cli.md).

The standard policy never skips a gate, while the quick route drops the UI
gate on construction — the conservative default is the full gate set
(ADR-005).
"""

_EXIT_CODE_BY_STATUS: Final[dict[StageStatus, int]] = {
    StageStatus.SUCCEEDED: EXIT_OK,
    StageStatus.WAITING: EXIT_WAITING,
    StageStatus.BLOCKED: EXIT_BLOCKED,
    StageStatus.FAILED: EXIT_ERROR,
}


class InvalidStageInput(ValueError):
    """Invalid command input or configuration; the stage did not start (exit 2)."""


@dataclass(frozen=True, slots=True)
class ChangeSnapshot:
    """Fixed input of a stage run: domain Change plus the raw snapshot bytes (FR-001)."""

    change: Change
    raw: bytes


def compute_input_revision(raw_snapshot: bytes) -> str:
    """Derived input revision: SHA-256 hex digest of the raw snapshot file bytes.

    Rule fixed in T009 (contract cli.md): identical snapshot file content
    yields an identical revision, any change of the file bytes yields a new
    one. The digest is taken over the raw bytes, not the parsed model, so the
    persisted snapshot copy always reproduces the revision.
    """
    return hashlib.sha256(raw_snapshot).hexdigest()


def generate_run_id() -> str:
    """New run id: ``run_`` + uuid4 hex (the contract examples use ``run_`` ids)."""
    return f"run_{uuid.uuid4().hex}"


def load_change_snapshot(path: str) -> ChangeSnapshot:
    """Read and validate the change snapshot file (FR-001).

    Raises :class:`InvalidStageInput` when the file cannot be read or does
    not match the ``Change`` schema: the stage must not start (exit 2). The
    raw bytes are kept for the evidence copy and for the derived input
    revision. The file is parsed as bytes so the YAML decoder handles the
    encoding (UTF-8 with or without BOM, UTF-16) per the YAML specification.
    """
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise InvalidStageInput(
            f"cannot read change snapshot {path!r}: {_os_error_reason(exc)}"
        ) from exc
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise InvalidStageInput(f"change snapshot {path!r} is not valid YAML") from exc
    try:
        change = Change.model_validate(data)
    except ValidationError as exc:
        raise InvalidStageInput(
            f"change snapshot {path!r} does not match the Change schema:"
            f" {_format_validation_errors(exc)}"
        ) from exc
    return ChangeSnapshot(change=change, raw=raw)


def persist_input_snapshot(evidence_dir: Path, raw: bytes) -> None:
    """Copy the raw snapshot into the evidence dir before execution starts (FR-001)."""
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / SNAPSHOT_EVIDENCE_NAME).write_bytes(raw)


def execute_stage(
    *,
    change: Change,
    stage: Stage,
    route: Route,
    run_id: str,
    input_revision: str,
) -> StageResult:
    """Execute one stage attempt through the deterministic steps (T010).

    Context assembly, machine checks, aggregation and the stage decision run
    without harness/LLM (ADR-003); the checks see only the fixed snapshot and
    the carried budget, and no gate is evaluated on a product SHA (FR-009),
    so the attempt ends ``waiting`` or ``blocked`` — never ``succeeded``
    (FR-009, SC-004). The route fixes gate applicability (ADR-005). The
    run-state store arrives with the durable state-store wiring: the budget
    is the default snapshot — exhaustion outcomes stay reachable for callers
    that carry a persisted budget — and ``attempt_number`` stays 1.
    """
    context = build_context(
        change=change,
        stage=stage,
        route=route,
        run_id=run_id,
        input_revision=input_revision,
        budget=BudgetSnapshot(),
    )
    return run_deterministic_stage(context)


def exit_code_for(status: StageStatus) -> int:
    """CLI exit code for a StageResult status (contract cli.md table).

    ``StageResult`` validates that ``status`` is a result status, so every
    key of the mapping is reachable and nothing else can arrive here.
    """
    return _EXIT_CODE_BY_STATUS[status]


def render_text(result: StageResult, key: str) -> str:
    """Human-readable summary: status line plus the next-action reason."""
    lines = [
        f"stage {result.stage.value}: {result.status.value}"
        f" (run_id={result.run_id}, change_id={result.change_id},"
        f" operation_key={key}, next_action={result.next_action.type})"
    ]
    if isinstance(result.next_action, WaitForInputAction | StopAction):
        lines.append(f"reason: {result.next_action.reason}")
    return "\n".join(lines)


def render_json(result: StageResult) -> str:
    """Stable JSON of the immutable StageResult (contract cli.md, data-model 1.3)."""
    return to_json(result)


def run_stage_command(args: StageRunArgs) -> int:
    """Handle ``factory stage run``; return the process exit code (contract cli.md)."""
    try:
        snapshot = load_change_snapshot(args.change)
        input_revision = _resolve_input_revision(args.input_revision, snapshot.raw)
        run_id = _resolve_run_id(args.run_id)
    except InvalidStageInput as exc:
        return _report_invalid_input(str(exc), json_output=args.json_output)

    route = args.route if args.route is not None else DEFAULT_STAGE_ROUTE
    key = operation_key(run_id, args.stage, input_revision)

    if args.evidence_dir is not None:
        evidence_dir = Path(args.evidence_dir)
        store = EvidenceOperationStore(evidence_dir)
        existing = store.find_existing(key)
        if existing is not None and existing.status in REPLAYABLE_RESULT_STATUSES:
            # ADR-006 p.3: the committed result of this operation is authoritative —
            # replay it without a second execution; the evidence is not touched and
            # the run manifest is not collected on this path.
            return _emit_result(existing, key, json_output=args.json_output)
        try:
            persist_input_snapshot(evidence_dir, snapshot.raw)
        except OSError as exc:
            return _report_invalid_input(
                f"cannot fix the input snapshot in {args.evidence_dir!r}: {_os_error_reason(exc)}",
                json_output=args.json_output,
            )
        try:
            # ADR-015 p.5: the stage must not start without a reproducible record.
            manifest = collect_run_manifest(os.environ)
        except RunRecordError as exc:
            return _report_invalid_input(str(exc), json_output=args.json_output)

    started_at = datetime.now(UTC)
    result = execute_stage(
        change=snapshot.change,
        stage=args.stage,
        route=route,
        run_id=run_id,
        input_revision=input_revision,
    )

    if args.evidence_dir is not None:
        try:
            store.record(key, result)
        except OSError as exc:
            # ADR-006 p.8: exit 10 would claim a persisted result — it does not
            # exist, so the command fails as an execution error instead.
            return _report_execution_error(
                f"cannot persist the stage result to {args.evidence_dir!r}:"
                f" {_os_error_reason(exc)}",
                json_output=args.json_output,
            )
        try:
            record = build_run_record(
                change=snapshot.change,
                run_id=run_id,
                stage=args.stage,
                route=route,
                input_revision=input_revision,
                result=result,
                manifest=manifest,
                started_at=started_at,
            )
            persist_run_record(evidence_dir, record)
        except RunRecordError as exc:
            return _report_execution_error(
                f"cannot build the run record: {exc}", json_output=args.json_output
            )
        except OSError as exc:
            # ADR-006 p.8: exit 10 would claim a persisted run record — it does
            # not exist, so the command fails as an execution error too.
            return _report_execution_error(
                f"cannot persist the run record to {args.evidence_dir!r}: {_os_error_reason(exc)}",
                json_output=args.json_output,
            )

    return _emit_result(result, key, json_output=args.json_output)


def _emit_result(result: StageResult, key: str, *, json_output: bool) -> int:
    """Emit one StageResult to the user and return its exit code (contract cli.md).

    In ``--json`` mode the ``operation_key=...`` line goes to stderr so stdout
    stays exactly one StageResult document; text mode prints the summary line.
    """
    if json_output:
        print(f"operation_key={key}", file=sys.stderr)
        print(render_json(result))
    else:
        print(render_text(result, key))
    return exit_code_for(result.status)


def _resolve_input_revision(value: str | None, raw_snapshot: bytes) -> str:
    """Explicit ``--input-revision`` or the revision derived from the snapshot."""
    if value is None:
        return compute_input_revision(raw_snapshot)
    if not value.strip():
        raise InvalidStageInput("--input-revision must be a non-empty string")
    return value


def _resolve_run_id(value: str | None) -> str:
    """Explicit ``--run-id`` or a generated ``run_<uuid4 hex>``."""
    if value is None:
        return generate_run_id()
    if not value.strip():
        raise InvalidStageInput("--run-id must be a non-empty string")
    return value


def _os_error_reason(exc: OSError) -> str:
    """Short OS error text without echoing the raw exception (ADR-009 hygiene)."""
    return exc.strerror or exc.__class__.__name__


def _format_validation_errors(exc: ValidationError) -> str:
    """First pydantic error locations and messages; the input data is not echoed."""
    errors = exc.errors()
    shown = [
        f"{'.'.join(str(part) for part in error['loc']) or '<root>'}: {error['msg']}"
        for error in errors[:3]
    ]
    if len(errors) > 3:
        shown.append(f"and {len(errors) - 3} more")
    return "; ".join(shown)


def _report_invalid_input(message: str, *, json_output: bool) -> int:
    """Report invalid input/configuration (exit 2, the stage did not start)."""
    return _report_error("invalid_input", message, EXIT_INVALID_INPUT, json_output=json_output)


def _report_execution_error(message: str, *, json_output: bool) -> int:
    """Report an execution failure after the stage started (exit 1)."""
    return _report_error("execution_error", message, EXIT_ERROR, json_output=json_output)


def _report_error(tag: str, message: str, code: int, *, json_output: bool) -> int:
    """Emit one error report: JSON payload on stdout, or a line on stderr."""
    if json_output:
        print(json.dumps({"error": tag, "detail": message}))
    else:
        print(f"factory stage run: {message}", file=sys.stderr)
    return code
