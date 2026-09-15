"""``factory run publish``: publish a run record into ``dark-factory-runs`` (T-061).

The command is the CLI face of the record protocol of ADR-015 p.4: it takes the
``run_record.json`` that ``factory stage run --evidence-dir`` (T011) or
``factory release verify --evidence-dir`` (T034/T-045) already produced, screens
it (secrets, size, evidence chain) and writes the compact immutable index into a
``dark-factory-runs`` checkout.

The publish step is deliberately separate from the stage execution: stage jobs
run untrusted agent work in sandboxed pods (ADR-018), while writing into the
audit repository is a trusted publisher action — the same separation the merge
policy makes between agent and finalizer (T-026).

The runs-repository root comes from ``--runs-root`` or ``DARK_FACTORY_RUNS_ROOT``
(the checkout is not hardcoded: locally it is a working copy, in CI a job
workspace). Exit codes (contract cli.md): 0 when the record was published or was
already there unchanged, 1 when the record was rejected for publication
(secrets, size, evidence chain, immutability), 2 for invalid input or
configuration — an unreadable record or no configured runs root, in which case
nothing is written. Diagnostics name paths, ids and rules only; record content
and raw exceptions are never echoed (ADR-009).
"""

import json
import os
import sys
from pathlib import Path
from typing import Final

from pydantic import ValidationError

from dark_factory.changes.run_records import RunRecord, from_json
from dark_factory.cli.main import EXIT_ERROR, EXIT_INVALID_INPUT, EXIT_OK, RunPublishArgs
from dark_factory.execution.runs import PublishResult, RunRecordStore
from dark_factory.execution.runs.errors import RunRecordStoreError

RUNS_ROOT_ENV_VAR: Final[str] = "DARK_FACTORY_RUNS_ROOT"
"""Environment variable with the ``dark-factory-runs`` checkout to publish into."""


class InvalidRecordInput(ValueError):
    """The run record cannot be read; nothing is published (exit 2)."""


def run_publish_command(args: RunPublishArgs) -> int:
    """Publish one run record and report the outcome (contract cli.md).

    The record is validated before the store is touched, so an invalid input
    never leaves a partial tree in the runs repository.
    """
    runs_root = _resolve_runs_root(args.runs_root)
    if runs_root is None:
        return _report_invalid_input(
            f"no runs repository: pass --runs-root or set {RUNS_ROOT_ENV_VAR}",
            json_output=args.json_output,
        )
    try:
        record = load_record(args.record)
    except InvalidRecordInput as exc:
        return _report_invalid_input(str(exc), json_output=args.json_output)

    try:
        result = RunRecordStore(runs_root).publish(record)
    except RunRecordStoreError as exc:
        return _report_rejected(str(exc), json_output=args.json_output)
    except OSError as exc:
        return _report_rejected(
            f"cannot write the run record into the runs repository: {_os_error_reason(exc)}",
            json_output=args.json_output,
        )
    _emit(result, json_output=args.json_output)
    return EXIT_OK


def load_record(path: str) -> RunRecord:
    """Read a persisted run record; raises :class:`InvalidRecordInput` (exit 2).

    The file is the ``run_record.json`` written by the stage and release
    commands (T011, T034). Neither the path content nor the validation detail of
    a failed parse is echoed beyond the schema mismatch itself (ADR-009).
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise InvalidRecordInput(
            f"cannot read the run record {path!r}: {_os_error_reason(exc)}"
        ) from exc
    try:
        return from_json(RunRecord, text)
    except ValidationError as exc:
        raise InvalidRecordInput(
            f"the run record {path!r} does not match the RunRecord schema"
        ) from exc


def render_json(result: PublishResult) -> str:
    """Stable JSON outcome of a publish (``--json``)."""
    return json.dumps(_payload(result))


def render_text(result: PublishResult) -> str:
    """Human-readable one-line outcome of a publish."""
    payload = _payload(result)
    return f"factory run publish: {payload['outcome']} {payload['change_id']} at {payload['path']}"


def _payload(result: PublishResult) -> dict[str, str]:
    """Report payload: outcome plus the immutable address of the record."""
    return {
        "outcome": result.outcome.value,
        "change_id": result.ref.change_id,
        "run_id": result.ref.run_id,
        "path": result.ref.relative_path,
    }


def _emit(result: PublishResult, *, json_output: bool) -> None:
    print(render_json(result) if json_output else render_text(result))


def _resolve_runs_root(value: str | None) -> Path | None:
    """``--runs-root`` or ``DARK_FACTORY_RUNS_ROOT``; ``None`` when neither is set."""
    candidate = value if value is not None else os.environ.get(RUNS_ROOT_ENV_VAR)
    if candidate is None or not candidate.strip():
        return None
    return Path(candidate)


def _os_error_reason(exc: OSError) -> str:
    """Short OS error text without echoing the raw exception (ADR-009 hygiene)."""
    return exc.strerror or exc.__class__.__name__


def _report_invalid_input(message: str, *, json_output: bool) -> int:
    """Report invalid input/configuration (exit 2, nothing published)."""
    return _report("invalid_input", message, EXIT_INVALID_INPUT, json_output=json_output)


def _report_rejected(message: str, *, json_output: bool) -> int:
    """Report a record the publisher refused (exit 1, nothing published)."""
    return _report("run_record_rejected", message, EXIT_ERROR, json_output=json_output)


def _report(tag: str, message: str, code: int, *, json_output: bool) -> int:
    """Emit one error report: JSON payload on stdout, or a line on stderr."""
    if json_output:
        print(json.dumps({"error": tag, "detail": message}))
    else:
        print(f"factory run publish: {message}", file=sys.stderr)
    return code
