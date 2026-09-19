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
from typing import Final

from sqlalchemy.orm import Session, sessionmaker

from dark_factory.cli._common import (
    StateStoreUnreachableError,
    default_owner_id,
    open_state_store,
    report_state_store_unreachable,
)
from dark_factory.cli.main import EXIT_OK, ReconcileArgs
from dark_factory.orchestration.reconcile.models import ReconcileReport
from dark_factory.orchestration.reconcile.service import GlobalReconciler

_COMMAND: Final[str] = "reconcile"
"""Subcommand name of the error reports (``factory reconcile: ...``)."""

RECONCILER_OWNER_ID_ENV_VAR: Final[str] = "DARK_FACTORY_RECONCILER_OWNER_ID"
"""Optional stable owner id of the reconciler lease; a unique default otherwise."""

_OWNER_ID_PREFIX: Final[str] = "factory-reconcile"
"""Prefix of the unique-per-process owner id (``factory-reconcile-<pid>-<hex>``)."""


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
    resolved_owner = (
        owner_id
        if owner_id is not None
        else default_owner_id(RECONCILER_OWNER_ID_ENV_VAR, _OWNER_ID_PREFIX)
    )
    if session_factory is not None:
        report = asyncio.run(GlobalReconciler(session_factory, owner_id=resolved_owner).run_pass())
        return _emit(args, report)
    try:
        with open_state_store() as factory:
            report = asyncio.run(GlobalReconciler(factory, owner_id=resolved_owner).run_pass())
    except StateStoreUnreachableError:
        return report_state_store_unreachable(_COMMAND, json_output=False)
    return _emit(args, report)


def _emit(args: ReconcileArgs, report: ReconcileReport) -> int:
    """Print the report in the requested format; a finished pass exits 0."""
    if args.json_output:
        print(render_json(report))
    else:
        print(render_text(report))
    return EXIT_OK
