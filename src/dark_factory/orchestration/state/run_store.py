"""Durable run store: reconstruct, create and advance a ``ChangeRun`` (T-092, ADR-006).

The state store holds the operational truth (ADR-004), but until T-092 nothing
in ``src`` turned it into a *run*: ``flow.apply_result`` had no durable caller,
stage/attempt rows were never written and ``ExecutionRepository.update_status``
was never reached. This module is that missing writer. It owns the mapping
between the durable rows and the domain models and reuses the existing
repositories instead of composing their SQL again:

- ``ExecutionRepository`` — ``execution``/``stage``/``attempt`` rows and the
  revision + fencing guarded status change (ADR-006 p.3/p.4/p.6);
- ``StageResultRepository`` — the immutable per-attempt result (FR-014);
- ``OutboxRepository`` — the transactional event published in the same
  transaction as the state change (ADR-016 p.1/p.5).

Two id spaces meet here and must not be confused:

- ``stage.id`` / ``operation_key`` = ``<execution_id>:<stage>:<input_revision>``
  (``changes.keys``) — the *logical operation*, unique per input revision;
- ``stage_result.id`` / ``attempt_id`` = ``operation_key:attempt_number`` — the
  *physical attempt*, the idempotency key of one persisted decision.

The domain ``StageRun.id`` reconstructed by :meth:`RunStore.load` is the
operation key, so a reconstructed run maps one-to-one onto its rows.

One decision writes both of its halves: ``open_attempt`` opens the executed
operation and :meth:`RunStore.persist_decision` closes it *and* inserts the
``pending`` rows of the stages the decision created — the successor stage of an
advance is a plain row of its own operation, declared by the caller
(:class:`StagePlacement`) so the store never has to recompute a revision. A run
therefore chains: after one persisted decision its next stage already exists.

Every writer in this module works inside the caller's transaction and never
commits: :func:`~dark_factory.orchestration.runner.advance_run` composes them so
that a decision is one atomic commit (ADR-006 p.8), and the caller
(``session_scope``) commits exactly once.

A reworked stage is no longer keyed by the unchanged snapshot (slice S2):
:meth:`RunStore.stage_input_revision` stays the default revision of a stage — the
``Change`` snapshot's digest, which is deterministic without a provider — but
:func:`~dark_factory.orchestration.runner.advance_run` accepts an injected
``revision_of`` resolver (``ScmRevision``, ADR-006 p.4), and the composition root
supplies the SCM-backed one, so rework advances the revision and the re-entered
stage is a new logical operation instead of a resumed one.

The retry protocol is no longer a limitation: a ``failed``/``blocked`` attempt
is retried as the next physical attempt of the same logical operation
(ADR-006 p.7). ``open_attempt`` appends attempt N+1 to the same operation row
(``attempt_count`` follows), and ``RunStore.load`` reconstructs the stage run
with that number.
"""

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from dark_factory.changes.enums import Provider, Route, RunStatus, Stage, StageStatus
from dark_factory.changes.implementation_contract import ImplementationContract
from dark_factory.changes.run import (
    STAGE_STATUS_TRANSITIONS,
    STAGE_TERMINAL_STATUSES,
    Change,
    ChangeRun,
    InvalidStatusTransition,
    StageResult,
    StageRun,
)
from dark_factory.changes.run_records import to_json
from dark_factory.changes.usage import BudgetSnapshot
from dark_factory.flows.routes import route_profile
from dark_factory.orchestration.flow import FlowDecision
from dark_factory.orchestration.state.models import Attempt
from dark_factory.orchestration.state.models import Stage as StageRow
from dark_factory.orchestration.state.repositories import (
    ExecutionRepository,
    LeaseRepository,
    OutboxRepository,
    StateError,
)
from dark_factory.orchestration.state.stage_results import (
    RecordOutcome,
    StageResultRepository,
)
from dark_factory.ports.events import EventType

LEASE_RESOURCE_TYPE: Final[str] = "execution"
"""Lease resource type of a run (ADR-006 p.6): the run id is the resource id."""

STAGE_COMPLETED_CONSUMERS: Final[tuple[str, ...]] = ("flow", "tracker")
"""Consumers of ``run.stage_completed`` (contracts/events.md): flow continuation and tracker.

Unregistered consumer ids fall back to the dispatcher's no-op handler, so
naming the intended consumers now keeps the per-consumer delivery rows honest
without wiring handlers (T-090/T-063).
"""

DEFAULT_LEASE_TTL: Final[timedelta] = timedelta(minutes=5)
"""Lease lifetime of one run advance; the job renews or loses the lease (ADR-006 p.6)."""


def generated_run_id(change_id: str, input_revision: str) -> str:
    """Deterministic run id of a change snapshot: one run per (change, input revision).

    Deriving the id from the identity of the work makes
    :meth:`RunStore.create_run` idempotent: a repeated call for the same
    snapshot returns the existing run instead of starting a second one
    (ADR-006 p.3 — the same input revision is the same logical operation).
    """
    digest = hashlib.sha256(f"{change_id}:{input_revision}".encode()).hexdigest()
    return f"run_{digest[:32]}"


def _status_path(current: StageStatus, target: StageStatus) -> tuple[StageStatus, ...]:
    """Legal status walk from ``current`` to ``target``; raises outside the table.

    Mirrors ``flow._ensure_stage_run``: the T-003 table has no direct
    ``pending/waiting -> succeeded`` edge, so a stage (or an attempt resumed
    after an external wait) is entered as ``in_progress`` first and completed
    from there. An empty tuple means ``current`` already is ``target``.
    """
    if current is target:
        return ()
    if target in STAGE_STATUS_TRANSITIONS[current]:
        return (target,)
    if (
        StageStatus.IN_PROGRESS in STAGE_STATUS_TRANSITIONS[current]
        and target in STAGE_STATUS_TRANSITIONS[StageStatus.IN_PROGRESS]
    ):
        return (StageStatus.IN_PROGRESS, target)
    raise InvalidStatusTransition(
        f"Stage transition {current.value} -> {target.value} is not allowed"
    )


def _stage_completed_event_id(attempt_id: str, *, supersede_status: str | None = None) -> str:
    """Deterministic id of a stage-attempt completion event (ADR-016 p.3).

    A digest, not the attempt id itself: an operation key carrying a full
    SHA-256 input revision is already ~120 characters and ``outbox.event_id``
    is bound to ``String(128)``. Determinism is what consumers deduplicate on
    (``event_id``), and the attempt id is the identity of one decision.

    A resolution of a ``waiting`` checkpoint (T-092 S3, ADR-006 p.8) completes
    the same attempt the checkpoint's event already named, so it extends the
    payload with the resolved status: a distinct, still deterministic id — the
    same resolution replays to the same id and deduplicates, and it never
    collides with the checkpoint's own event.
    """
    payload = f"{attempt_id}:{EventType.RUN_STAGE_COMPLETED.value}"
    if supersede_status is not None:
        payload = f"{payload}:{supersede_status}"
    return f"evt_{hashlib.sha256(payload.encode()).hexdigest()[:32]}"


@dataclass(frozen=True, slots=True)
class StagePlacement:
    """A stage row one decision requires, and the input revision it is pinned to.

    :meth:`RunStore.persist_decision` ensures the row exists as ``pending`` — a
    successor stage the flow started in memory must exist durably for the run to
    chain. The revision is supplied by the caller instead of recomputed here: it
    is the value the driver pinned for that stage, so the row, the stage result
    and the next advance agree on the operation identity (ADR-006 p.3).
    """

    stage: Stage
    input_revision: str


@dataclass(frozen=True, slots=True)
class OpenStage:
    """Durable rows of one stage attempt opened for execution (T-092).

    ``stage_row_id`` and ``attempt_id`` address the rows the decision must
    write; ``attempt_number`` is the identity the domain stage run must agree
    with (``flow._ensure_stage_run`` rejects a result from a stale attempt).
    """

    stage_row_id: str
    attempt_id: str
    attempt_number: int
    input_revision: str


class RunStore:
    """Durable store of runs: reconstruct, create, advance and persist (T-092).

    One instance wraps one :class:`~sqlalchemy.orm.Session` and therefore one
    transaction; nothing here commits.
    """

    def __init__(self, session: Session) -> None:
        self._session = session
        self._executions = ExecutionRepository(session)
        self._results = StageResultRepository(session)
        self._outbox = OutboxRepository(session)
        self._leases = LeaseRepository(session)

    # --- reads -------------------------------------------------------------

    def load(self, execution_id: str) -> ChangeRun | None:
        """Reconstruct the domain run of ``execution_id``; ``None`` when it does not exist.

        Lossless for everything ``flow.apply_result`` reads or mutates: status
        and revision of the run, every stage occurrence (status, attempt
        number, pinned input revision, revisions and timestamps), the budget
        snapshot and the approved contract. A ``NULL`` budget or contract means
        "not set" and reads back as the default snapshot and ``None``.
        """
        execution = self._executions.get(execution_id)
        if execution is None:
            return None
        route = Route(execution.route)
        return ChangeRun(
            id=execution.id,
            change_id=execution.change_id,
            route=route,
            provider=Provider(execution.provider),
            status=RunStatus(execution.status),
            state_revision=execution.state_revision,
            stages=self._load_stages(execution_id, route),
            budget=(
                BudgetSnapshot.model_validate(execution.budget)
                if execution.budget is not None
                else BudgetSnapshot()
            ),
            implementation_contract=(
                ImplementationContract.model_validate(execution.implementation_contract)
                if execution.implementation_contract is not None
                else None
            ),
            created_at=execution.created_at,
            updated_at=execution.updated_at,
            finished_at=execution.finished_at,
        )

    def load_history(self, execution_id: str) -> list[StageResult]:
        """Every persisted result of the run, in ``(produced_at, stage, attempt)`` order.

        The flow uses the history for the completion invariants (ADR-009 p.9)
        and never loads it itself, so the caller supplies it.
        """
        return self._results.list_for_run(execution_id)

    def committed_result(
        self,
        *,
        run_id: str,
        stage: Stage,
        attempt_number: int,
        input_revision: str,
    ) -> StageResult | None:
        """Committed result of one stage operation and attempt, if any (ADR-006 p.3).

        The identity is the operation key (run, stage, input revision) plus the
        attempt number: a result of the same stage and attempt under another
        input revision belongs to a different logical operation and does not
        match. Whether the status replays is the caller's policy
        (``orchestration.idempotency.REPLAYABLE_RESULT_STATUSES``), not the
        store's — this method only reads.
        """
        result = self._results.get(run_id, stage, attempt_number)
        if result is None or result.input_revision != input_revision:
            return None
        return result

    def _load_stages(self, execution_id: str, route: Route) -> list[StageRun]:
        """Stage rows of the run as domain stage runs, in route order.

        Ordered by the position of the stage on the route and then by row id:
        the flow reads the last non-terminal occurrence of a stage, and an S1
        run has at most one occurrence per stage.
        """
        sequence = {stage: index for index, stage in enumerate(route_profile(route).stages)}
        rows = sorted(
            self._session.execute(
                select(StageRow).where(StageRow.execution_id == execution_id)
            ).scalars(),
            key=lambda row: (sequence[Stage(row.stage)], row.id),
        )
        return [
            StageRun(
                id=row.id,
                stage=Stage(row.stage),
                status=StageStatus(row.status),
                attempt_number=max(row.attempt_count, 1),
                input_revision=row.input_revision,
                state_revision=row.state_revision,
                started_at=row.started_at,
                finished_at=row.finished_at,
            )
            for row in rows
        ]

    @staticmethod
    def stage_input_revision(change: Change) -> str:
        """Default input revision of a ``Change`` snapshot: SHA-256 of its canonical JSON.

        Deterministic: identical snapshots yield an identical revision, any
        changed field yields a new one (``changes.keys.operation_key`` is built
        on it, ADR-006 p.3). It is the *default*: the driver accepts an injected
        ``revision_of`` resolver (slice S2), and the composition root supplies the
        SCM-backed one so a stage is keyed by the product commit it starts from
        (ADR-006 p.4). Without a provider this function stays the answer, and it
        is the single place that has to change for a different default.
        """
        return hashlib.sha256(to_json(change).encode("utf-8")).hexdigest()

    # --- creation ----------------------------------------------------------

    def create_run(
        self,
        *,
        change_id: str,
        route: Route,
        provider: Provider,
        budget: BudgetSnapshot,
        input_revision: str,
        implementation_contract: ImplementationContract | None = None,
        initial_stage_revision: str | None = None,
    ) -> ChangeRun:
        """Create the run of a change snapshot and its first stage, or return the existing one.

        Idempotent by the generated execution id (``generated_run_id``): the
        second call for the same change and input revision returns the run
        already created, without touching its budget or contract — a persisted
        budget carries accumulated spend and rework rounds (FR-016) and must
        never be reset by a repeat. ``input_revision`` is required because the
        run pins the revision of the work it executes: the first stage row is
        keyed by it and every later stage of the same run reuses it.

        ``initial_stage_revision`` re-keys the *first stage row* (ADR-006 p.4):
        the run stays identified by the change snapshot's digest — the id a
        repeat resolves — while its first stage, entered for the first time, is
        keyed by the SCM-derived revision the composition root's resolver
        supplies. Without the override the run id and the stage operation would
        disagree with the SCM resolver: the executor would mint its workspace at
        a digest that is not a git object (found in T-043 increment 1).
        """
        execution_id = generated_run_id(change_id, input_revision)
        existing = self.load(execution_id)
        if existing is not None:
            return existing
        execution = self._executions.create(
            execution_id=execution_id, change_id=change_id, route=route, provider=provider
        )
        execution.budget = budget.model_dump(mode="json")
        execution.implementation_contract = (
            implementation_contract.model_dump(mode="json")
            if implementation_contract is not None
            else None
        )
        self._executions.get_or_create_stage(
            execution_id=execution_id,
            stage=route_profile(route).initial_stage,
            input_revision=(
                initial_stage_revision if initial_stage_revision is not None else input_revision
            ),
        )
        run = self.load(execution_id)
        if run is None:  # pragma: no cover - defensive, the row was just created
            raise StateError(f"execution {execution_id!r} was not created")
        return run

    # --- leases ------------------------------------------------------------

    def acquire_lease(self, *, run_id: str, owner_id: str, ttl: timedelta) -> int:
        """Acquire the run lease and return its fencing token (ADR-006 p.6)."""
        return self._leases.acquire(
            resource_type=LEASE_RESOURCE_TYPE, resource_id=run_id, owner_id=owner_id, ttl=ttl
        )

    def release_lease(self, *, run_id: str, owner_id: str, fencing_token: int) -> bool:
        """Release the run lease held with ``fencing_token``; ``False`` when it was lost."""
        return self._leases.release(
            resource_type=LEASE_RESOURCE_TYPE,
            resource_id=run_id,
            owner_id=owner_id,
            fencing_token=fencing_token,
        )

    # --- writes ------------------------------------------------------------

    def advance_stage(self, stage_row_id: str, target: StageStatus, *, now: datetime) -> None:
        """Move a stage row to ``target`` through the domain transition table.

        Writes what the domain ``StageRun.apply_status`` writes in memory —
        status, monotonic ``state_revision``, ``started_at`` on the first entry
        into ``in_progress`` and ``finished_at`` on a terminal status — and
        raises ``InvalidStatusTransition`` outside the table. A stage entered
        from ``pending``/``waiting``/``blocked`` passes through ``in_progress``:
        the table has no direct edge to ``succeeded``.
        """
        row = self._session.get(StageRow, stage_row_id)
        if row is None:
            raise StateError(f"stage {stage_row_id!r} does not exist")
        for status in _status_path(StageStatus(row.status), target):
            row.status = status.value
            row.state_revision += 1
            if status is StageStatus.IN_PROGRESS and row.started_at is None:
                row.started_at = now
            if status in STAGE_TERMINAL_STATUSES:
                row.finished_at = now

    def finish_attempt(self, attempt_id: str, target: StageStatus, *, now: datetime) -> None:
        """Finalize an attempt row: status ``target`` and ``finished_at = now``.

        ``append_attempt`` only ever inserts ``in_progress`` and nothing closed
        the row, so an attempt outlived its result. The same status walk as
        ``advance_stage`` applies, and finalizing an attempt that already
        carries ``target`` is a no-op — the decision was applied before.
        """
        attempt = self._session.get(Attempt, attempt_id)
        if attempt is None:
            raise StateError(f"attempt {attempt_id!r} does not exist")
        current = StageStatus(attempt.status)
        for status in _status_path(current, target):
            attempt.status = status.value
        if current is not target:
            attempt.finished_at = now

    def open_attempt(
        self,
        *,
        run: ChangeRun,
        stage: Stage,
        input_revision: str,
        attempt_number: int,
    ) -> OpenStage:
        """Open the stage row of one attempt and return its durable identity.

        ``get_or_create_stage`` keys the row by ``(run, stage, input revision)``
        (ADR-006 p.3), so the same work always lands on the same logical
        operation; ``append_attempt`` adds the physical attempt.

        A finalized attempt is never reopened: neither its status nor its
        ``finished_at`` is touched, so a committed outcome can never be rewritten
        by a repeat. The caller handles the committed result first: it replays a
        committed result of this attempt, or — after a ``failed``/``blocked``
        attempt — asks for the *next* attempt number, which ``append_attempt``
        inserts as a fresh attempt of the same operation (ADR-006 p.7).

        The one exception is a ``waiting`` attempt (T-092 S3): it is the
        external-wait checkpoint of ADR-006 p.8 — the attempt is not finished,
        it is parked on facts the provider has not produced yet. Resolving the
        wait re-opens the *same* physical attempt (``waiting -> in_progress``,
        a legal edge of the T-003 table, ``finished_at`` cleared) — never a new
        attempt number, so the resolution result recomposes the checkpoint's
        attempt id and supersedes it in the result store. A waiting attempt has
        no row of its own to bump a revision on; the domain and stage-row
        revisions move with the resolution's applied status, as on every path.

        Reaching any other non-``in_progress`` attempt here is a programming
        error and raises.
        """
        row = self._executions.get_or_create_stage(
            execution_id=run.id, stage=stage, input_revision=input_revision
        )
        attempt = self._executions.append_attempt(row, attempt_number=attempt_number)
        status = StageStatus(attempt.status)
        if status is StageStatus.WAITING:
            for step in _status_path(status, StageStatus.IN_PROGRESS):
                attempt.status = step.value
            attempt.finished_at = None
        elif status is not StageStatus.IN_PROGRESS:
            raise StateError(
                f"attempt {attempt.id!r} is already finalized ({attempt.status}); "
                "a committed result must be replayed or a new attempt opened "
                "(ADR-006 p.3/p.7)"
            )
        return OpenStage(
            stage_row_id=row.id,
            attempt_id=attempt.id,
            attempt_number=attempt.attempt_number,
            input_revision=input_revision,
        )

    def persist_decision(
        self,
        *,
        run: ChangeRun,
        result: StageResult,
        decision: FlowDecision,
        open_stage: OpenStage,
        created_stages: Sequence[StagePlacement],
        expected_revision: int,
        fencing_token: int,
        now: datetime,
        consumers: Iterable[str] = STAGE_COMPLETED_CONSUMERS,
    ) -> bool:
        """Persist one flow decision — the whole of it — atomically; ``False`` when replayed.

        In the caller's transaction it writes the immutable stage result
        (FR-014), the ``pending`` rows of every stage the decision created that
        the store does not have yet (so the run chains into its successor), the
        advance of the executed stage row, the finalization of its attempt, the
        run status under the optimistic revision and the lease fencing token
        (ADR-006 p.4/p.6) and publishes ``run.stage_completed`` to the outbox
        (ADR-016 p.1/p.5). No commit happens here: the caller commits once.

        A replay of the same attempt is refused before any of the above — the
        ``stage_result`` primary key *is* the attempt id, so ``False`` means
        nothing at all was written and the caller reports the already committed
        result (:func:`~dark_factory.orchestration.runner.advance_run` checks it
        even earlier, before any mutation). Call this *before* any external wait:
        the result must be durable before the job ends (ADR-006 p.8).

        A resolution of a ``waiting`` checkpoint is not a replay (T-092 S3):
        the result store supersedes the checkpoint in place (same row, same
        attempt) and the event carries a distinct, deterministic id derived
        from the resolved status, so it never collides with the checkpoint's
        own ``run.stage_completed`` event.
        """
        outcome = self._results.record_outcome(result)
        if outcome is RecordOutcome.REPLAYED:
            return False
        for placement in created_stages:
            self._executions.get_or_create_stage(
                execution_id=run.id, stage=placement.stage, input_revision=placement.input_revision
            )
        self.advance_stage(open_stage.stage_row_id, decision.stage_status, now=now)
        self.finish_attempt(open_stage.attempt_id, decision.stage_status, now=now)
        execution = self._executions.update_status(
            run.id,
            decision.run_status,
            expected_revision=expected_revision,
            fencing_token=fencing_token,
        )
        # ``apply_result`` may have applied more than one run status edge
        # (pending -> running -> waiting), while ``update_status`` advances the
        # persisted revision by one. Keep them equal so the reconstructed run
        # reports the revision the domain reached, never a lower one.
        execution.state_revision = max(execution.state_revision, run.state_revision)
        self._outbox.publish(
            event_id=_stage_completed_event_id(
                open_stage.attempt_id,
                supersede_status=(
                    result.status.value if outcome is RecordOutcome.SUPERSEDED else None
                ),
            ),
            event_type=EventType.RUN_STAGE_COMPLETED.value,
            change_id=run.change_id,
            run_id=run.id,
            aggregate_id=run.id,
            aggregate_version=execution.state_revision,
            stage=result.stage,
            causation_id=open_stage.attempt_id,
            payload={
                "attempt_number": result.attempt_number,
                "result_status": result.status.value,
                "stage_status": decision.stage_status.value,
                "run_status": decision.run_status.value,
                "next_action": decision.action.type,
                "next_stage": decision.next_stage.value if decision.next_stage else None,
            },
            consumers=consumers,
        )
        return True
