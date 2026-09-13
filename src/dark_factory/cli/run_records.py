"""Run-record persistence for ``factory stage run`` (T011, ADR-015 p.4/p.5).

The run record is the compact immutable evidence index of a run (ADR-015
p.4): with ``--evidence-dir`` the CLI persists it as ``run_record.json`` next
to the input snapshot and the StageResult — a local stand-in for the
``dark-factory-runs`` repository and CI artifacts until the durable state
store is wired. The manifest references exact revisions of every input,
never ``latest`` (ADR-015 p.5): commits resolve from the environment (set by
CI) or, as the last resort, from the git HEAD of the checkout the CLI runs
in. The record is built from the fixed input snapshot and the immutable
StageResult, so the evidence index stays reproducible from the evidence it
indexes.
"""

import importlib.metadata
import subprocess
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Final

from dark_factory.changes.enums import Route, RunStatus, Stage, StageStatus
from dark_factory.changes.run import SCHEMA_VERSION, Change, ChangeRun, StageResult, StageRun
from dark_factory.changes.run_records import RunManifest, RunRecord, from_json, to_json

RUN_RECORD_EVIDENCE_NAME: Final[str] = "run_record.json"
"""Evidence file with the serialized RunRecord (ADR-015 p.4)."""

DISTRIBUTION_NAME: Final[str] = "dark-factory"
"""Installed distribution the factory version is read from."""

FACTORY_COMMIT_ENV: Final[str] = "DARK_FACTORY_COMMIT"
"""Environment variable with the exact commit of the factory checkout."""

PRODUCT_COMMIT_ENV: Final[str] = "DARK_FACTORY_PRODUCT_COMMIT"
"""Environment variable with the exact commit of the product repository."""

FALLBACK_COMMIT_ENV: Final[str] = "GITHUB_SHA"
"""CI fallback commit (GitHub Actions); in the factory's own CI job it is the factory commit."""

_RUN_STATUS_BY_RESULT_STATUS: Final[dict[StageStatus, RunStatus]] = {
    StageStatus.WAITING: RunStatus.WAITING,
    StageStatus.BLOCKED: RunStatus.BLOCKED,
    StageStatus.FAILED: RunStatus.FAILED,
    # One succeeded stage does not complete the run: the run stays running
    # until the final stage of the route reports success.
    StageStatus.SUCCEEDED: RunStatus.RUNNING,
}


class RunRecordError(ValueError):
    """The run record cannot be built from the current environment."""


def _git_head() -> str | None:
    """HEAD commit of the checkout the CLI runs in; ``None`` when it cannot be determined.

    Kept as a module-level function so tests can monkeypatch it.
    """
    try:
        completed = subprocess.run(
            ("git", "rev-parse", "HEAD"),
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def _clean_env(value: str | None) -> str | None:
    """Stripped environment value; empty and whitespace-only values count as absent."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _factory_version() -> str:
    """Version of the installed ``dark-factory`` distribution; ``0.0.0`` without metadata."""
    try:
        return importlib.metadata.version(DISTRIBUTION_NAME)
    except importlib.metadata.PackageNotFoundError:
        return "0.0.0"


def _commit_ref(env: Mapping[str, str | None], commit_env: str, label: str) -> str:
    """Resolve one exact commit ref: explicit env var, CI fallback, then git HEAD.

    Raises :class:`RunRecordError` naming the environment variables when no
    source is available; the values themselves are never echoed (ADR-009).
    """
    value = (
        _clean_env(env.get(commit_env)) or _clean_env(env.get(FALLBACK_COMMIT_ENV)) or _git_head()
    )
    if value is None:
        raise RunRecordError(
            f"cannot determine the {label} commit:"
            f" set {commit_env} (or {FALLBACK_COMMIT_ENV}) to the exact commit"
        )
    return value


def collect_run_manifest(env: Mapping[str, str | None]) -> RunManifest:
    """Collect the run manifest of the current environment (ADR-015 p.5).

    Commits resolve in order: the explicit env var, the CI fallback
    (``GITHUB_SHA`` — in the factory's own CI job it is the factory commit),
    the git HEAD of the current checkout. ``factory_version`` comes from the
    installed distribution metadata (``0.0.0`` when unavailable). The
    pack/blueprint/gitops/okf inputs are not wired yet and stay absent.
    """
    return RunManifest(
        factory_version=_factory_version(),
        factory_commit=_commit_ref(env, FACTORY_COMMIT_ENV, "factory"),
        product_commit=_commit_ref(env, PRODUCT_COMMIT_ENV, "product"),
    )


def build_run_record(
    *,
    change: Change,
    run_id: str,
    stage: Stage,
    route: Route,
    input_revision: str,
    result: StageResult,
    manifest: RunManifest,
    started_at: datetime,
) -> RunRecord:
    """Build the run record from the fixed input snapshot and the immutable StageResult.

    The run and stage state are moved through the single status transition
    points (T-003) instead of ``orchestration.flow.apply_result``: the
    deterministic executor may emit ``wait_for_input`` for the release stage,
    which the stage-level flow table (T-004) does not allow for a result. One
    succeeded stage does not complete the run — the run stays ``running`` —
    and the run budget stays the default snapshot: usage accounting arrives
    with the state store / harness tasks, and the deterministic path reports
    ``usage=None``.
    """
    run = ChangeRun(id=run_id, change_id=change.id, route=route, provider=change.product.provider)
    run.apply_status(RunStatus.RUNNING)

    stage_run = StageRun(
        id=f"{run.id}:{stage.value}:1",
        stage=stage,
        attempt_number=result.attempt_number,
        input_revision=input_revision,
        started_at=started_at,
    )
    stage_run.apply_status(StageStatus.IN_PROGRESS)
    stage_run.apply_status(result.status)
    run.stages.append(stage_run)

    mapped_status = _RUN_STATUS_BY_RESULT_STATUS[result.status]
    if mapped_status is not RunStatus.RUNNING:
        run.apply_status(mapped_status)

    return RunRecord(
        schema_version=SCHEMA_VERSION,
        manifest=manifest,
        change=change,
        run=run,
        stage_results=[result],
        decisions=[],
    )


def persist_run_record(evidence_dir: Path, record: RunRecord) -> Path:
    """Write the run record into the evidence dir and return the file path (ADR-015 p.4)."""
    evidence_dir.mkdir(parents=True, exist_ok=True)
    path = evidence_dir / RUN_RECORD_EVIDENCE_NAME
    path.write_text(to_json(record), encoding="utf-8")
    return path


def load_run_record(path: Path) -> RunRecord:
    """Read a run record back from the evidence dir.

    Reader for the ``stage resume`` / ``run status`` handlers that arrive with
    the durable state-store wiring (not part of T011).
    """
    return from_json(RunRecord, path.read_text(encoding="utf-8"))
