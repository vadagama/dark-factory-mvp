"""CI job script for one factory stage job (T025, contract cli.md, ADR-006).

``factory stage run`` persists the StageResult before any external wait
(ADR-006 p.8) and exits with the contract exit-code table; this thin module
is the job-side half of the same contract (one job = one Flow stage,
``.github/workflows/factory-stage.yml``): it reads the persisted StageResult
JSON, re-validates it against the contract, renders GitHub Actions outputs
and classifies the job outcome.

Exit codes (contract cli.md): 0 succeeded, 10 waiting, 20 blocked, 1 failed,
2 invalid input/configuration. ``waiting`` is not an error — the result is
persisted before the external wait and continuation is a new stage run — so
the job exits 0 for both ``succeeded`` and ``waiting``; ``blocked`` and
``failed`` fail the job with their contract codes plus a one-line
``::error::`` annotation carrying the stop reason. Annotations stay short
and echo only domain identifiers and the stop reason — never payloads or
secrets (ADR-009). FR-009/SC-004 stay with the deterministic CI jobs: this
script only transports the stage decision, it evaluates no gate.

Usage (see ``.github/workflows/factory-stage.yml``):

    factory stage run ... --json --non-interactive --evidence-dir factory-evidence \
        > stage_result.json
    python -m dark_factory.cli.ci_job stage_result.json --outputs-file "$GITHUB_OUTPUT"

Outputs (``key=value`` lines for ``$GITHUB_OUTPUT``, also on stdout):
``status``, ``next_action`` (the action type), ``run_id``, ``change_id``,
``usage_total_tokens`` and ``usage_cost`` (both empty when the stage
reported no usage — the deterministic path reports ``usage=None``).
"""

import argparse
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

from dark_factory.cli._common import os_error_reason
from dark_factory.cli.main import (
    EXIT_BLOCKED,
    EXIT_ERROR,
    EXIT_INVALID_INPUT,
    EXIT_OK,
    EXIT_WAITING,
)

_RESULT_STATUS_VALUES: Final[frozenset[str]] = frozenset(
    {"waiting", "succeeded", "failed", "blocked"}
)
"""Result statuses of a StageResult (data-model 1.3); mirrored by the US1 parity job."""

_MAX_ANNOTATION_REASON: Final[int] = 280
"""Reason cap keeping the ``::error::`` annotation short (ADR-009 hygiene)."""


class StageJobError(ValueError):
    """The StageResult of the stage job is unusable: exit 2, the job cannot continue."""


class StageJobOutcome(StrEnum):
    """Outcome of one factory stage job (contract cli.md exit-code table)."""

    OK = "ok"
    WAITING = "waiting"
    BLOCKED = "blocked"
    FAILED = "failed"
    INVALID_INPUT = "invalid_input"


EXIT_CODES_BY_OUTCOME: Final[dict[StageJobOutcome, int]] = {
    StageJobOutcome.OK: EXIT_OK,
    StageJobOutcome.WAITING: EXIT_WAITING,
    StageJobOutcome.BLOCKED: EXIT_BLOCKED,
    StageJobOutcome.FAILED: EXIT_ERROR,
    StageJobOutcome.INVALID_INPUT: EXIT_INVALID_INPUT,
}
"""Process exit code per outcome (contract cli.md table)."""

_OUTCOME_BY_EXIT_CODE: Final[dict[int, StageJobOutcome]] = {
    code: outcome for outcome, code in EXIT_CODES_BY_OUTCOME.items()
}

_OUTCOME_BY_STATUS: Final[dict[str, StageJobOutcome]] = {
    "succeeded": StageJobOutcome.OK,
    "waiting": StageJobOutcome.WAITING,
    "blocked": StageJobOutcome.BLOCKED,
    "failed": StageJobOutcome.FAILED,
}


@dataclass(frozen=True, slots=True)
class StageResultView:
    """Fields of a validated StageResult consumed by the job script (data-model 1.3)."""

    stage: str
    status: str
    run_id: str
    change_id: str
    next_action_type: str
    next_action_reason: str | None
    usage: Mapping[str, object] | None


def classify_exit_code(code: int) -> StageJobOutcome:
    """Classify a ``factory stage run`` exit code (contract cli.md table).

    0 succeeded, 10 waiting, 20 blocked, 1 failed, 2 invalid input. The
    workflow shell gate (``.github/workflows/factory-stage.yml``) applies the
    same table when it treats 0 and 10 as job-continuing; this function
    encodes it for tests and programmatic callers.
    """
    outcome = _OUTCOME_BY_EXIT_CODE.get(code)
    if outcome is None:
        raise ValueError(f"unknown factory stage run exit code: {code}")
    return outcome


def job_exit_code(outcome: StageJobOutcome) -> int:
    """Process exit code of the CI job for one classified outcome.

    ``ok`` and ``waiting`` are job-continuing and both exit 0: per contract
    cli.md ``waiting`` is not an error — the StageResult is persisted before
    the external wait (ADR-006 p.8) and continuation is a new stage run, not
    a retry of this job. ``blocked`` (20), ``failed`` (1) and
    ``invalid_input`` (2) fail the job with their contract exit codes.
    """
    if outcome in (StageJobOutcome.OK, StageJobOutcome.WAITING):
        return EXIT_OK
    return EXIT_CODES_BY_OUTCOME[outcome]


def outcome_for_status(status: str) -> StageJobOutcome:
    """Outcome for a validated ``StageResult.status`` (contract cli.md table)."""
    return _OUTCOME_BY_STATUS[status]


def parse_stage_result(data: object) -> StageResultView:
    """Validate one serialized StageResult and extract the job-relevant fields.

    Mirrors the assertions of the US1 parity job (``ci.yml``):
    ``schema_version`` is 1, ``status`` is one of the result statuses,
    ``run_id``/``change_id`` are non-empty strings and ``next_action.type``
    is a non-empty string. Unknown fields are tolerated (the contract may
    grow); violations raise :class:`StageJobError` with a short message
    naming the field — the raw document is never echoed (ADR-009).
    """
    if not isinstance(data, Mapping):
        raise StageJobError("stage result must be a JSON object")
    result: Mapping[str, object] = data
    if result.get("schema_version") != 1:
        raise StageJobError("schema_version must be 1")
    status = result.get("status")
    if not isinstance(status, str) or status not in _RESULT_STATUS_VALUES:
        allowed = ", ".join(sorted(_RESULT_STATUS_VALUES))
        raise StageJobError(f"status must be one of: {allowed}")
    next_action = result.get("next_action")
    if not isinstance(next_action, Mapping):
        raise StageJobError("next_action must be a JSON object")
    action_type = next_action.get("type")
    if not isinstance(action_type, str) or not action_type:
        raise StageJobError("next_action.type must be a non-empty string")
    reason = next_action.get("reason")
    usage = result.get("usage")
    return StageResultView(
        stage=_non_empty_string(result, "stage"),
        status=status,
        run_id=_non_empty_string(result, "run_id"),
        change_id=_non_empty_string(result, "change_id"),
        next_action_type=action_type,
        next_action_reason=reason if isinstance(reason, str) and reason else None,
        usage=usage if isinstance(usage, Mapping) else None,
    )


def load_stage_result(path: str) -> StageResultView:
    """Read and parse the ``stage_result.json`` written by ``factory stage run``."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise StageJobError(f"stage result {path!r} is not valid UTF-8") from exc
    except OSError as exc:
        raise StageJobError(f"cannot read stage result {path!r}: {os_error_reason(exc)}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise StageJobError(f"stage result {path!r} is not valid JSON: {exc.msg}") from exc
    return parse_stage_result(data)


def render_outputs(view: StageResultView) -> str:
    """Render the GitHub Actions outputs as ``key=value`` lines for ``$GITHUB_OUTPUT``.

    ``usage_total_tokens`` is the reported ``total_tokens`` or, when absent,
    prompt + completion summed; ``usage_cost`` is the decimal cost string.
    Empty values are emitted (not omitted) so callers can reference the
    outputs unconditionally. Values are single-line by construction:
    identifiers, the status, the action type, integers and the cost string.
    """
    tokens, cost = _usage_totals(view.usage)
    lines = (
        f"status={view.status}",
        f"next_action={view.next_action_type}",
        f"run_id={view.run_id}",
        f"change_id={view.change_id}",
        f"usage_total_tokens={tokens}",
        f"usage_cost={cost}",
    )
    return "".join(f"{line}\n" for line in lines)


def error_annotation(view: StageResultView) -> str:
    """One-line ``::error::`` workflow command with the stop reason (ADR-009).

    The stop reason (``next_action.reason``, the action type as fallback) is
    collapsed to a single line and truncated to a short text. Only the domain
    reason is echoed — never payloads or secrets (ADR-009).
    """
    reason = _one_line(view.next_action_reason or view.next_action_type)
    if len(reason) > _MAX_ANNOTATION_REASON:
        reason = f"{reason[:_MAX_ANNOTATION_REASON]}..."
    return f"::error::stage {view.stage} {view.status}: {reason}"


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point of ``python -m dark_factory.cli.ci_job`` (T025).

    Reads the StageResult of one stage run, publishes the GitHub Actions
    outputs and returns the process exit code (contract cli.md): 0 for
    ``succeeded``/``waiting`` (waiting is not an error, ADR-006 p.8), 20 for
    ``blocked`` and 1 for ``failed`` with a ``::error::`` stop-reason
    annotation, 2 when the stage result itself is unusable. Outputs are
    rendered before the failure exit so callers reading the job outputs with
    ``if: always()`` still see the stage decision.
    """
    args = _parse_args(argv)
    try:
        view = load_stage_result(args.stage_result)
    except StageJobError as exc:
        return _fail_invalid(str(exc))
    outputs = render_outputs(view)
    print(outputs, end="")
    if args.outputs_file is not None:
        try:
            Path(args.outputs_file).write_text(outputs, encoding="utf-8")
        except OSError as exc:
            return _fail_invalid(
                f"cannot write the outputs file {args.outputs_file!r}: {os_error_reason(exc)}"
            )
    outcome = outcome_for_status(view.status)
    if outcome in (StageJobOutcome.BLOCKED, StageJobOutcome.FAILED):
        print(error_annotation(view))
        return EXIT_CODES_BY_OUTCOME[outcome]
    return job_exit_code(outcome)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m dark_factory.cli.ci_job",
        description=(
            "Validate a factory StageResult, render GitHub Actions outputs"
            " and classify the job exit code (T025, contract cli.md)."
        ),
    )
    parser.add_argument(
        "stage_result",
        help="Path to the stage_result.json written by factory stage run.",
    )
    parser.add_argument(
        "--outputs-file",
        help="File for the GitHub Actions outputs ($GITHUB_OUTPUT); stdout only when omitted.",
    )
    return parser.parse_args(argv)


def _non_empty_string(result: Mapping[str, object], key: str) -> str:
    """Validated non-empty string field; :class:`StageJobError` names the field."""
    value = result.get(key)
    if not isinstance(value, str) or not value:
        raise StageJobError(f"{key} must be a non-empty string")
    return value


def _usage_totals(usage: Mapping[str, object] | None) -> tuple[str, str]:
    """``(usage_total_tokens, usage_cost)`` output values.

    Both empty when the stage reported no usage (the deterministic path
    reports ``usage=None``). Tokens: ``total_tokens`` when reported, else
    prompt + completion summed; cost is the decimal string of the result.
    """
    if usage is None:
        return "", ""
    total = usage.get("total_tokens")
    if isinstance(total, int) and not isinstance(total, bool) and total >= 0:
        tokens = total
    else:
        prompt = _usage_int(usage.get("prompt_tokens"))
        completion = _usage_int(usage.get("completion_tokens"))
        tokens = prompt + completion
    cost = usage.get("cost")
    return str(tokens), "" if cost is None else str(cost)


def _usage_int(value: object) -> int:
    """Non-negative int of the usage block; 0 for anything else."""
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return 0


def _one_line(text: str) -> str:
    """Collapse whitespace (including newlines) to single spaces."""
    return " ".join(text.split())


def _fail_invalid(message: str) -> int:
    """Report an unusable stage result: one ``::error::`` annotation, exit 2."""
    print(f"::error::invalid stage result: {_one_line(message)}")
    return EXIT_INVALID_INPUT


if __name__ == "__main__":
    raise SystemExit(main())
