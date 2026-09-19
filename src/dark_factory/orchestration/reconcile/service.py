"""Executor of reconciliation passes over the PostgreSQL state store (T-063, ADR-006 p.5/p.7/p.9).

One pass is the scheduled recovery loop of the factory (ADR-006 p.5): read the
desired state from PostgreSQL (the authoritative store, ADR-004), observe the
external state through the optional engine/observer seams, plan at most one
recovery action per run through the anomaly→action table
(``reconcile.rules``) and apply the state-level mutations. The pass result is
a :class:`~dark_factory.orchestration.reconcile.models.ReconcileReport` — the
journal entry written to the log (ADR-006 p.9).

Scheduling (the CronJob manifest itself is deployed in T040/T042, ``deploy/``):

- expected interval 2-5 minutes (``RECONCILE_INTERVAL_MINUTES``);
- ``concurrencyPolicy: Forbid`` keeps the common case serial — an
  optimisation only;
- ``activeDeadlineSeconds: 300`` bounds a stuck job below the lease TTL;
- correctness comes from the PostgreSQL global lease below: an accidentally
  parallel launch (K8s misconfiguration, operator rerun) changes nothing.

Double protection against parallel passes (ADR-006 p.6):

1. Kubernetes ``concurrencyPolicy: Forbid`` (optimisation, see above);
2. the global lease row ``("reconciler", "global")`` in ``execution_leases``:
   a second reconciler that cannot steal the lease reports
   ``lease_acquired=False`` and finishes without reading or changing anything.

The reconciler never executes agent tasks (ADR-006 p.5): ``PLAN_MERGE``,
``WAIT_FOR_HUMAN`` and ``UPDATE_BRANCH`` are journal-only actions planned for
their executors (trusted finalizer, human, provider wiring); the mutations the
pass applies are lease takeover, supersede, record-merge and escalate —
state-level recovery only. Webhooks are only an accelerator (ADR-019): every
external wait is also resolved by this scheduled pass, so reconciliation
never depends on a webhook having fired.

Idempotency (ADR-006 p.5): a repeated pass on the same observed data changes
nothing — taken-over leases are live again, superseded runs leave the scan
set, escalated runs are no longer in ``running``/``waiting``, recorded merges
are terminal, and the report-only actions are applied by their executors, not
by the pass.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from dark_factory.changes.clock import utc_now
from dark_factory.changes.enums import RunStatus, StageStatus
from dark_factory.changes.run import RUN_STATUS_TRANSITIONS
from dark_factory.orchestration.policy.merge import DEFAULT_MERGE_POLICY, MergePolicy
from dark_factory.orchestration.reconcile.models import (
    ReconcileAction,
    ReconcileActionKind,
    ReconcileReport,
    RunReconciliation,
)
from dark_factory.orchestration.reconcile.rules import (
    ExternalFacts,
    LeaseFacts,
    ReconcileObserver,
    RunFacts,
    plan_action,
    resolve_drift,
)
from dark_factory.orchestration.state.engine import session_scope
from dark_factory.orchestration.state.models import Attempt, Execution
from dark_factory.orchestration.state.models import (
    Stage as StageRow,
)
from dark_factory.orchestration.state.repositories import (
    ExecutionRepository,
    LeaseLostError,
    LeaseRepository,
    StateError,
)
from dark_factory.ports import (
    ReconcileDesired,
    ReconcileObserved,
    ReconcileResult,
    ReconciliationService,
    RunNotFoundError,
    WorkflowEnginePort,
)

RECONCILE_INTERVAL_MINUTES: Final[tuple[int, int]] = (2, 5)
"""Expected CronJob schedule window in minutes (ADR-006 p.5); the manifest is T040/T042."""

CONCURRENCY_POLICY: Final[str] = "Forbid"
"""CronJob concurrency policy (optimisation only; the global lease is the correctness guard)."""

ACTIVE_DEADLINE_SECONDS: Final[int] = 300
"""CronJob deadline bounding one pass below the lease TTL."""

GLOBAL_LEASE_TTL: Final[timedelta] = timedelta(minutes=5)
"""TTL of the global reconciler lease; renewed implicitly by every pass."""

EXECUTION_LEASE_TTL: Final[timedelta] = timedelta(minutes=5)
"""TTL granted to a taken-over execution lease (ADR-006 p.6)."""

GLOBAL_LEASE_RESOURCE_TYPE: Final[str] = "reconciler"
GLOBAL_LEASE_RESOURCE_ID: Final[str] = "global"
EXECUTION_LEASE_RESOURCE_TYPE: Final[str] = "execution"
"""Resource type of execution leases; ``update_status`` looks leases up by it (ADR-006 p.6)."""

_SCANNED_RUN_STATUSES: Final[frozenset[RunStatus]] = frozenset(
    {RunStatus.PENDING, RunStatus.RUNNING, RunStatus.WAITING, RunStatus.BLOCKED, RunStatus.FAILED}
)
"""Runs the pass reads; terminal runs need no recovery and are never scanned."""

_ACTIVE_RUN_STATUSES: Final[frozenset[RunStatus]] = frozenset(
    {RunStatus.PENDING, RunStatus.RUNNING, RunStatus.WAITING, RunStatus.BLOCKED}
)
"""Non-terminal, unfinished runs — the duplicate-runs input."""

_ACTION_TARGETS: Final[dict[ReconcileActionKind, RunStatus]] = {
    ReconcileActionKind.SUPERSEDE: RunStatus.SUPERSEDED,
    ReconcileActionKind.RECORD_MERGE: RunStatus.SUCCEEDED,
    ReconcileActionKind.ESCALATE: RunStatus.BLOCKED,
}
"""Recovery mutations the pass applies itself; every other action is journal-only."""


async def observe_statuses(
    engine: WorkflowEnginePort | None, run_ids: Sequence[str]
) -> dict[str, RunStatus]:
    """Observe the engine statuses of ``run_ids``; unknown runs are absent.

    The workflow engine is the CI side of the CI↔PostgreSQL comparison; a run
    the engine does not know is simply not observed (``in_sync=None`` in the
    journal). Webhooks are not involved: observation is pulled here.
    """
    if engine is None or not run_ids:
        return {}
    observed: dict[str, RunStatus] = {}
    for run_id in run_ids:
        try:
            observed[run_id] = await engine.get_status(run_id)
        except RunNotFoundError:
            continue
    return observed


def _detect_repeated_error(session: Session, execution_id: str) -> tuple[bool, str | None]:
    """Whether the two latest attempts of one stage both failed (ADR-006 p.7).

    Reads the attempt history of the execution; returns the failing stage's
    name for the journal. Deterministic: stages are visited in id order and
    attempts in attempt-number order.
    """
    rows = session.execute(
        select(StageRow, Attempt).where(
            Attempt.stage_id == StageRow.id, StageRow.execution_id == execution_id
        )
    ).all()
    attempts_by_stage: dict[str, list[Attempt]] = {}
    stage_names: dict[str, str] = {}
    for stage_row, attempt in rows:
        attempts_by_stage.setdefault(stage_row.id, []).append(attempt)
        stage_names[stage_row.id] = stage_row.stage
    failed = StageStatus.FAILED.value
    for stage_id in sorted(attempts_by_stage):
        attempts = sorted(attempts_by_stage[stage_id], key=lambda item: item.attempt_number)
        if len(attempts) >= 2 and attempts[-1].status == failed and attempts[-2].status == failed:
            return True, stage_names[stage_id]
    return False, None


class GlobalReconciler:
    """One idempotent reconciliation pass over the PostgreSQL state (ADR-006 p.5).

    The pass runs in a single transaction: the global lease acquire, the scan,
    the applied mutations and the lease release commit together or not at all
    (session rollback on failure, ADR-006 p.6). ``engine`` and ``observer``
    are optional seams — without them only the PostgreSQL-sourced rules
    (expired lease, duplicates, repeated error) fire, which keeps the pass
    useful in a bare environment. ``now`` is injectable for deterministic
    tests; production passes use the wall clock (repository leases always
    compare against the real clock).
    """

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        owner_id: str,
        engine: WorkflowEnginePort | None = None,
        observer: ReconcileObserver | None = None,
        lease_ttl: timedelta = GLOBAL_LEASE_TTL,
        merge_policy: MergePolicy = DEFAULT_MERGE_POLICY,
    ) -> None:
        self._session_factory = session_factory
        self._owner_id = owner_id
        self._engine = engine
        self._observer = observer
        self._lease_ttl = lease_ttl
        self._merge_policy = merge_policy

    async def run_pass(self, *, now: datetime | None = None) -> ReconcileReport:
        """Run one pass and return its journal report (ADR-006 p.9).

        ``lease_acquired=False`` means another reconciler holds the global
        lease: the report is empty and nothing was read or changed — the
        normal outcome under a concurrent launch.
        """
        moment = utc_now() if now is None else now
        with session_scope(self._session_factory) as session:
            leases = LeaseRepository(session)
            try:
                global_token = leases.acquire(
                    resource_type=GLOBAL_LEASE_RESOURCE_TYPE,
                    resource_id=GLOBAL_LEASE_RESOURCE_ID,
                    owner_id=self._owner_id,
                    ttl=self._lease_ttl,
                )
            except LeaseLostError:
                return ReconcileReport(
                    lease_acquired=False, owner_id=self._owner_id, scanned=0, entries=()
                )
            return await self._run_scoped_pass(
                session, leases=leases, global_token=global_token, now=moment
            )

    async def _run_scoped_pass(
        self,
        session: Session,
        *,
        leases: LeaseRepository,
        global_token: int,
        now: datetime,
    ) -> ReconcileReport:
        """Scan, plan and apply inside the caller's transaction, then release the lease."""
        executions = list(
            session.execute(
                select(Execution)
                .where(Execution.status.in_([status.value for status in _SCANNED_RUN_STATUSES]))
                .order_by(Execution.id)
            ).scalars()
        )
        entries: list[RunReconciliation] = []
        if executions:
            observed = await observe_statuses(self._engine, [row.id for row in executions])
            external = await self._observe_external(executions)
            active_by_change = self._group_active_by_change(executions)
            for execution in executions:
                entries.append(
                    await self._reconcile_one(
                        session,
                        execution,
                        leases=leases,
                        observed=observed,
                        external=external,
                        active_by_change=active_by_change,
                        now=now,
                    )
                )
        # Releasing inside the same transaction: a rollback (crash) restores the
        # lease so another pass can steal it; a commit drops it. After the commit
        # the fencing token restarts at 1 — per-execution fencing plus Forbid
        # keep that safe (documented limitation).
        leases.release(
            resource_type=GLOBAL_LEASE_RESOURCE_TYPE,
            resource_id=GLOBAL_LEASE_RESOURCE_ID,
            owner_id=self._owner_id,
            fencing_token=global_token,
        )
        return ReconcileReport(
            lease_acquired=True,
            owner_id=self._owner_id,
            scanned=len(entries),
            entries=tuple(entries),
        )

    async def _observe_external(self, executions: Sequence[Execution]) -> dict[str, ExternalFacts]:
        """Collect the observer's external facts per run; ``None`` observations are skipped."""
        if self._observer is None:
            return {}
        external: dict[str, ExternalFacts] = {}
        for execution in executions:
            facts = await self._observer.observe_external(
                run_id=execution.id, change_id=execution.change_id
            )
            if facts is not None:
                external[execution.id] = facts
        return external

    @staticmethod
    def _group_active_by_change(executions: Sequence[Execution]) -> dict[str, list[str]]:
        """Active (non-terminal) run ids grouped by change — the duplicate-runs input."""
        grouped: dict[str, list[str]] = {}
        for execution in executions:
            if RunStatus(execution.status) in _ACTIVE_RUN_STATUSES:
                grouped.setdefault(execution.change_id, []).append(execution.id)
        return grouped

    async def _reconcile_one(
        self,
        session: Session,
        execution: Execution,
        *,
        leases: LeaseRepository,
        observed: Mapping[str, RunStatus],
        external: Mapping[str, ExternalFacts],
        active_by_change: Mapping[str, Sequence[str]],
        now: datetime,
    ) -> RunReconciliation:
        """Plan and apply the recovery of one run; return its journal entry."""
        run_id = execution.id
        desired_status = RunStatus(execution.status)
        desired_revision = execution.state_revision
        facts = self._assemble_facts(
            session,
            execution,
            leases=leases,
            desired_status=desired_status,
            desired_revision=desired_revision,
            observed=observed,
            external=external,
            active_by_change=active_by_change,
        )
        action = plan_action(facts, now=now, policy=self._merge_policy)
        applied = False
        note: str | None = None
        if action is not None:
            applied, note = self._apply(session, execution, action, leases=leases)
        observed_status = observed.get(run_id)
        in_sync: bool | None = None
        if observed_status is not None:
            drift = resolve_drift(
                ReconcileDesired(
                    run_id=run_id, status=desired_status, state_revision=desired_revision
                ),
                ReconcileObserved(run_id=run_id, status=observed_status),
            )
            in_sync = drift.in_sync
        return RunReconciliation(
            run_id=run_id,
            change_id=execution.change_id,
            desired_status=desired_status,
            desired_revision=desired_revision,
            observed_status=observed_status,
            in_sync=in_sync,
            action=action,
            applied=applied,
            note=note,
        )

    def _assemble_facts(
        self,
        session: Session,
        execution: Execution,
        *,
        leases: LeaseRepository,
        desired_status: RunStatus,
        desired_revision: int,
        observed: Mapping[str, RunStatus],
        external: Mapping[str, ExternalFacts],
        active_by_change: Mapping[str, Sequence[str]],
    ) -> RunFacts:
        """Assemble the rule input of one run from PostgreSQL plus observations.

        Sibling ids are sorted so duplicate planning never depends on row
        order.
        """
        lease_row = leases.get(EXECUTION_LEASE_RESOURCE_TYPE, execution.id)
        lease = (
            LeaseFacts(
                owner_id=lease_row.owner_id,
                fencing_token=lease_row.fencing_token,
                expires_at=lease_row.expires_at,
            )
            if lease_row is not None
            else None
        )
        repeated, repeated_stage = _detect_repeated_error(session, execution.id)
        siblings = tuple(
            sorted(
                sibling_id
                for sibling_id in active_by_change.get(execution.change_id, ())
                if sibling_id != execution.id
            )
        )
        facts = external.get(execution.id, ExternalFacts())
        return RunFacts(
            run_id=execution.id,
            change_id=execution.change_id,
            desired_status=desired_status,
            desired_revision=desired_revision,
            observed_status=observed.get(execution.id),
            lease=lease,
            active_sibling_ids=siblings,
            change_request=facts.change_request,
            branch=facts.branch,
            merge=facts.merge,
            repeated_error=repeated,
            repeated_error_stage=repeated_stage,
        )

    def _apply(
        self,
        session: Session,
        execution: Execution,
        action: ReconcileAction,
        *,
        leases: LeaseRepository,
    ) -> tuple[bool, str | None]:
        """Apply the planned action; ``(applied, note)`` explains the outcome.

        Every mutation is guarded twice (ADR-006 p.6): the execution lease is
        (re)acquired first — a live foreign lease yields ``applied=False`` —
        and the status update is fenced by the fresh token and the expected
        revision. Domain-invalid transitions (for example recording a merge on
        a waiting run) are never forced; they stay journal-only for the next
        pass and their executors.
        """
        if action.kind is ReconcileActionKind.LEASE_TAKEOVER:
            # A takeover repairs the lease only: the run status stays untouched.
            try:
                token = leases.acquire(
                    resource_type=EXECUTION_LEASE_RESOURCE_TYPE,
                    resource_id=execution.id,
                    owner_id=self._owner_id,
                    ttl=self._lease_ttl,
                )
            except LeaseLostError:
                return False, "skipped: a live execution lease is held by another owner"
            return True, f"execution lease taken over with fencing token {token}"
        target = _ACTION_TARGETS.get(action.kind)
        if target is None:
            return False, (
                f"{action.kind.value} is planned for its executor, not applied by the pass"
            )
        current = RunStatus(execution.status)
        if target not in RUN_STATUS_TRANSITIONS[current]:
            return False, (
                f"transition {current.value} -> {target.value} is not allowed; "
                "left to the next pass and its executor"
            )
        try:
            token = leases.acquire(
                resource_type=EXECUTION_LEASE_RESOURCE_TYPE,
                resource_id=execution.id,
                owner_id=self._owner_id,
                ttl=self._lease_ttl,
            )
        except LeaseLostError:
            return False, "skipped: a live execution lease is held by another owner"
        try:
            ExecutionRepository(session).update_status(
                execution.id,
                target,
                expected_revision=execution.state_revision,
                fencing_token=token,
            )
        except StateError as error:
            return False, f"skipped: {_error_text(error)}"
        return True, f"status set to {target.value} under fencing token {token}"


def _error_text(error: Exception) -> str:
    """Short, secret-free error text for the journal note (ADR-009)."""
    return " ".join(str(error).split()) or error.__class__.__name__


class PostgresReconciliationService(ReconciliationService):
    """Port adapter resolving the CI↔PostgreSQL comparison after the fact (ADR-006 p.9).

    The service implements the ``ReconciliationService`` contract over the same
    pure resolution as the pass: the desired state read from PostgreSQL is
    authoritative, so the result always carries its status and revision and
    only flags the drift. It performs no I/O of its own — the caller supplies
    both sides.
    """

    async def reconcile(
        self, *, desired: ReconcileDesired, observed: ReconcileObserved
    ) -> ReconcileResult:
        return resolve_drift(desired, observed)
