"""``factory reconcile``: one idempotent Reconciler pass (T-063, contract cli.md, ADR-006 p.5/p.9).

The command is the CLI face of the scheduled reconciliation CronJob
(T040/T042 deploy the manifest; expected interval 2-5 minutes,
``concurrencyPolicy: Forbid``, ``activeDeadlineSeconds`` 300): it runs exactly
one :class:`~dark_factory.orchestration.reconcile.service.GlobalReconciler`
pass over the PostgreSQL state store (``DATABASE_URL``, ADR-004/ADR-009) and
prints the pass report — human-readable text by default, stable JSON with
``--json``. Exit codes (contract cli.md): 0 for a finished pass (including
``lease_acquired=False`` — another reconciler holds the global lease, a normal
outcome, not an error) and 2 when the state store is unreachable or
misconfigured. Errors never echo the URL or the raw exception, which may
contain credentials (ADR-009).

The pass is idempotent and does not depend on webhooks (ADR-019): running the
command repeatedly changes nothing once the observed data is unchanged —
webhook events are only an accelerator for the scheduled pass.
"""

import asyncio
import json
import os
import sys
import uuid
from typing import Final

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from dark_factory.cli.main import EXIT_INVALID_INPUT, EXIT_OK, ReconcileArgs
from dark_factory.orchestration.reconcile.models import ReconcileReport
from dark_factory.orchestration.reconcile.service import GlobalReconciler
from dark_factory.orchestration.state.engine import (
    DEFAULT_DATABASE_URL,
    create_session_factory,
    create_state_engine,
)

DATABASE_URL_ENV_VAR: Final[str] = "DATABASE_URL"
"""State-store URL variable; the same one ``factory doctor`` checks (ADR-004)."""

RECONCILER_OWNER_ID_ENV_VAR: Final[str] = "DARK_FACTORY_RECONCILER_OWNER_ID"
"""Optional stable owner id of the reconciler lease; a unique default otherwise."""


def _database_url() -> str:
    """Configured state-store URL; the local default when unset (ADR-004)."""
    raw = os.environ.get(DATABASE_URL_ENV_VAR)
    return raw if raw and raw.strip() else DEFAULT_DATABASE_URL


def _default_owner_id() -> str:
    """Unique-per-process owner id so concurrent CLI passes never share a lease."""
    configured = os.environ.get(RECONCILER_OWNER_ID_ENV_VAR, "").strip()
    if configured:
        return configured
    return f"factory-reconcile-{os.getpid()}-{uuid.uuid4().hex[:8]}"


def render_text(report: ReconcileReport) -> str:
    """Human-readable one-line-per-run report of the pass."""
    header = (
        f"factory reconcile: owner={report.owner_id} lease_acquired={report.lease_acquired!r} "
        f"scanned={report.scanned}"
    )
    lines = [header]
    lines.extend(
        f"{entry.run_id} [{entry.desired_status.value}]: "
        + (
            f"{entry.action.kind.value} ({entry.action.anomaly.value}) applied={entry.applied!r}"
            if entry.action is not None
            else "no action"
        )
        for entry in report.entries
    )
    return "\n".join(lines)


def render_json(report: ReconcileReport) -> str:
    """Stable JSON report (``--json``): the pass journal as a single object."""
    return json.dumps(report.model_dump(mode="json"))


def run_reconcile_command(
    args: ReconcileArgs,
    *,
    session_factory: sessionmaker[Session] | None = None,
    owner_id: str | None = None,
) -> int:
    """Run one reconciliation pass and emit its report (contract cli.md).

    ``session_factory``/``owner_id`` are injection seams for tests; by default
    the state store is reached through ``DATABASE_URL`` and probed with a
    connection first so an unreachable store fails fast with exit code 2
    instead of a traceback. The reconciler never touches webhooks and never
    executes agent tasks: the pass plans and applies state-level recovery only.
    """
    resolved_owner = owner_id if owner_id is not None else _default_owner_id()
    if session_factory is not None:
        report = asyncio.run(GlobalReconciler(session_factory, owner_id=resolved_owner).run_pass())
        return _emit(args, report)
    try:
        engine = create_state_engine(_database_url())
        with engine.connect():
            pass  # liveness probe: fail fast with exit 2 when the store is unreachable
    except SQLAlchemyError:
        # The exception text may embed the URL with credentials (ADR-009).
        print(
            "factory reconcile: the state store is not reachable or misconfigured", file=sys.stderr
        )
        return EXIT_INVALID_INPUT
    factory = create_session_factory(engine)
    try:
        report = asyncio.run(GlobalReconciler(factory, owner_id=resolved_owner).run_pass())
    finally:
        engine.dispose()
    return _emit(args, report)


def _emit(args: ReconcileArgs, report: ReconcileReport) -> int:
    """Print the report in the requested format; a finished pass exits 0."""
    if args.json_output:
        print(render_json(report))
    else:
        print(render_text(report))
    return EXIT_OK
