"""Durable run driver: execute one stage of a run and persist the whole decision (T-092).

Until T-092 nothing in ``src`` called ``flow.apply_result``: the domain FSM had
no durable caller, so a change could not travel from intake to a finished run.
This module is that caller — the *between-stages* half of ADR-005 — and it does
one thing: advance one run by exactly one stage decision, persisting **the whole
decision**: the executed stage and its attempt, the stage result, the run
status and the successor stage the decision advanced into
(:meth:`~dark_factory.orchestration.state.run_store.RunStore.persist_decision`).

Order of one advance, and why:

1. the run is reconstructed from the state store (ADR-004) and its next stage is
   resolved from the route (ADR-005);
2. the committed result of that stage operation is looked up **before any
   write**: a ``succeeded``/``waiting`` result is replayed — the advance reports
   it and writes nothing (ADR-006 p.3, the same policy as ``factory stage run``);
   a ``failed``/``blocked`` one would need a new attempt of the same operation
   (ADR-006 p.7), which the retry/resume protocol of slice S2 owns;
3. only then the run lease is acquired with its fencing token (ADR-006 p.6), and
   the run is moved to ``running`` through the domain transition table
   (``ChangeRun.apply_status``), not by a direct write;
4. the stage operation and its physical attempt are opened (ADR-006 p.3);
5. the fixed ``StageContext`` is assembled (FR-001) with the run's persisted
   budget, and the injected :class:`StageExecutor` produces the result;
6. ``apply_result`` is called — the single flow transition point — with the
   run's history, the merge context and the observed human decisions;
7. the decision is persisted atomically with its ``run.stage_completed`` event
   (ADR-006 p.8, ADR-016 p.1/p.5) and the lease is released.

``apply_result`` decides in memory and the durable writer mirrors that decision
through the same domain transition tables; the run status additionally passes
``ExecutionRepository.update_status``, which today validates the optimistic
revision and the fencing token, not the status table (see the known limitations
below).

The caller owns the transaction and the clock: ``advance_run`` never commits,
takes ``now`` for determinism and expects the caller (``session_scope``) to
commit once — an exception rolls the whole advance back, lease included.

Known limitations of slice S1 (T-092):

- **Retry and resume of a non-terminal stage** — bumping the attempt number and
  opening a new physical attempt for a ``failed``/``blocked`` operation is the
  retry protocol of ADR-006 p.7 and belongs to slice S2. Until it lands,
  re-advancing such a stage raises :class:`AttemptAlreadyCommittedError` rather
  than reopening a finalized attempt (which would also corrupt its
  ``finished_at``).
- **A new input revision for a reworked stage** — rework that re-enters a stage
  with the same revision maps onto that stage's existing operation row
  (ADR-006 p.3), so a reconstruction resumes the stage the rework left; a
  faithful rework needs the SCM-derived revision of slice S2.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol

from dark_factory.changes.enums import RunStatus, Stage
from dark_factory.changes.findings import Decision
from dark_factory.changes.run import (
    RUN_TERMINAL_STATUSES,
    STAGE_TERMINAL_STATUSES,
    Change,
    ChangeRun,
    StageResult,
    StageRun,
)
from dark_factory.flows.routes import route_profile
from dark_factory.orchestration.flow import FlowDecision, apply_result
from dark_factory.orchestration.idempotency import REPLAYABLE_RESULT_STATUSES
from dark_factory.orchestration.policy.merge import MergeRequestContext
from dark_factory.orchestration.stages import build_context, run_deterministic_stage
from dark_factory.orchestration.stages.context import StageContext
from dark_factory.orchestration.state.run_store import (
    DEFAULT_LEASE_TTL,
    OpenStage,
    StagePlacement,
)


class RunnerError(RuntimeError):
    """The run cannot be advanced."""


class RunNotFoundError(RunnerError):
    """No run with that id exists in the state store."""


class RunNotAdvanceableError(RunnerError):
    """The run is terminal or every stage of its route is already terminal."""


class AttemptAlreadyCommittedError(RunnerError):
    """The stage operation already has a committed result that cannot be replayed.

    A committed ``failed``/``blocked`` result allows a retry, and a retry is a
    *new* attempt of the same logical operation (ADR-006 p.3/p.7). Opening that
    attempt is the retry/resume protocol of slice S2; until it lands the driver
    refuses instead of re-executing into an attempt whose result is already
    committed.
    """


class StageExecutor(Protocol):
    """Executes one stage attempt and returns its immutable result (slice S2 seam).

    Slice S1 runs the deterministic path
    (:func:`~dark_factory.orchestration.stages.run_deterministic_stage`); slice S2
    replaces this implementation with the harness-backed stage without touching
    the driver, the flow or the persistence.
    """

    def __call__(self, context: StageContext) -> StageResult: ...


def deterministic_stage_executor(context: StageContext) -> StageResult:
    """Default S1 executor: the deterministic stage path (T010), no harness/LLM (ADR-003)."""
    return run_deterministic_stage(context)


class RunStorePort(Protocol):
    """Durable API the driver depends on (implemented by ``state.run_store.RunStore``).

    Narrow on purpose: it is what one advance needs, so a test can drive the
    driver from an in-memory store and slice S2 can swap the durable one.
    """

    @staticmethod
    def stage_input_revision(change: Change) -> str: ...

    def load(self, execution_id: str) -> ChangeRun | None: ...

    def load_history(self, execution_id: str) -> list[StageResult]: ...

    def committed_result(
        self,
        *,
        run_id: str,
        stage: Stage,
        attempt_number: int,
        input_revision: str,
    ) -> StageResult | None: ...

    def acquire_lease(self, *, run_id: str, owner_id: str, ttl: timedelta) -> int: ...

    def release_lease(self, *, run_id: str, owner_id: str, fencing_token: int) -> bool: ...

    def open_attempt(
        self,
        *,
        run: ChangeRun,
        stage: Stage,
        input_revision: str,
        attempt_number: int,
    ) -> OpenStage: ...

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
    ) -> bool: ...


class RunAdvanceOutcome(StrEnum):
    """How one advance ended; the CLI maps these onto the contract exit codes."""

    ADVANCED = "advanced"
    WAITING = "waiting"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    REPLAYED = "replayed"
    """Nothing was written: the committed result of the operation is authoritative."""


@dataclass(frozen=True, slots=True)
class RunAdvance:
    """Result of one advance: the outcome, the stage, the result and the decision.

    ``decision`` is ``None`` when nothing was written: on a replay the flow is
    not consulted at all, the committed result is authoritative
    (:attr:`RunAdvanceOutcome.REPLAYED`) and the run state is untouched.
    """

    outcome: RunAdvanceOutcome
    stage: Stage
    result: StageResult
    decision: FlowDecision | None


def next_stage(run: ChangeRun) -> Stage | None:
    """Stage the run must execute next: the first non-terminal one on its route.

    The route fixes the sequence (ADR-005); a stage with any non-terminal
    stage run is the one in flight — it may be fresh (``pending``), waiting for
    an external event or a human, or blocked and about to be retried.
    ``None`` means no stage is left to execute.
    """
    for stage in route_profile(run.route).stages:
        if any(
            stage_run.stage == stage and stage_run.status not in STAGE_TERMINAL_STATUSES
            for stage_run in run.stages
        ):
            return stage
    return None


def outcome_for(decision: FlowDecision) -> RunAdvanceOutcome:
    """Outcome of one decision, read from the run status it produced.

    A terminal status wins: ``succeeded`` completes the run, and the other
    terminals (``failed``, ``canceled``, ``superseded``) end the advance as
    ``failed`` — the driver has only these five outcomes for a decision.
    ``waiting`` means the run is parked with its result already persisted
    (ADR-006 p.8), ``blocked`` needs a human decision and ``running`` means the
    next stage may execute. :attr:`RunAdvanceOutcome.REPLAYED` is not produced
    here: a replay has no decision.
    """
    if decision.run_status in RUN_TERMINAL_STATUSES:
        if decision.run_status is RunStatus.SUCCEEDED:
            return RunAdvanceOutcome.COMPLETED
        return RunAdvanceOutcome.FAILED
    if decision.run_status is RunStatus.WAITING:
        return RunAdvanceOutcome.WAITING
    if decision.run_status is RunStatus.BLOCKED:
        return RunAdvanceOutcome.BLOCKED
    return RunAdvanceOutcome.ADVANCED


def advance_run(
    *,
    store: RunStorePort,
    change: Change,
    run_id: str,
    owner_id: str,
    executor: StageExecutor = deterministic_stage_executor,
    merge_context: MergeRequestContext | None = None,
    human_decisions: Sequence[Decision] = (),
    lease_ttl: timedelta = DEFAULT_LEASE_TTL,
    now: datetime | None = None,
) -> RunAdvance:
    """Advance ``run_id`` by exactly one stage decision, inside the caller's transaction.

    ``change`` is the validated snapshot the stage context is built from
    (FR-001); ``merge_context`` and ``human_decisions`` are the facts the flow
    needs for the merge policy and the R2+ control points (T-026, T-080) — the
    driver observes nothing itself. ``now`` overrides the wall clock for the
    deadline limit and the timestamps.

    The whole decision is persisted, not only its stage: the successor stage the
    flow advanced into is inserted as a ``pending`` row of its own operation, so
    the next advance finds it and the run chains.

    Replay comes first and writes nothing: a committed ``succeeded``/``waiting``
    result of the same operation and attempt is returned as
    :attr:`RunAdvanceOutcome.REPLAYED` (mirroring ``factory stage run``), and a
    committed ``failed``/``blocked`` result raises
    :class:`AttemptAlreadyCommittedError` — the retry/resume protocol of slice S2.

    Raises :class:`RunNotFoundError` for an unknown run and
    :class:`RunNotAdvanceableError` for a terminal one; a flow violation
    (``InvalidFlowTransition``, ``FlowStateError``, ``InvalidStatusTransition``)
    propagates, so an illegal transition can never be papered over.
    """
    reference_now = now if now is not None else datetime.now(UTC)
    run = store.load(run_id)
    if run is None:
        raise RunNotFoundError(f"run {run_id!r} does not exist")
    if run.status in RUN_TERMINAL_STATUSES:
        raise RunNotAdvanceableError(f"run {run_id!r} is terminal ({run.status.value})")
    stage = next_stage(run)
    if stage is None:
        raise RunNotAdvanceableError(f"run {run_id!r} has no stage left to execute")
    attempt_number = _attempt_number(run, stage)
    input_revision = _pinned_revision(store, run, stage, change)
    committed = _committed_result(store, run, stage, attempt_number, input_revision)
    if committed is not None:
        # Before any mutation, including the lease: a committed result is the
        # operation's outcome and a repeat must not touch the durable rows.
        return _replay(committed, run_id=run.id, stage=stage)

    expected_revision = run.state_revision
    fencing_token = store.acquire_lease(run_id=run.id, owner_id=owner_id, ttl=lease_ttl)
    if run.status is not RunStatus.RUNNING:
        run.apply_status(RunStatus.RUNNING)
    open_stage = store.open_attempt(
        run=run,
        stage=stage,
        input_revision=input_revision,
        attempt_number=attempt_number,
    )
    known_stage_runs = tuple(run.stages)
    context = build_context(
        change=change,
        stage=stage,
        route=run.route,
        run_id=run.id,
        input_revision=input_revision,
        budget=run.budget,
    )
    result = executor(context)
    decision = apply_result(
        run,
        result,
        history=store.load_history(run.id),
        now=reference_now,
        merge_context=merge_context,
        human_decisions=human_decisions,
    )
    created_stages = _created_stages(
        store, run, known=known_stage_runs, executed=stage, change=change
    )
    persisted = store.persist_decision(
        run=run,
        result=result,
        decision=decision,
        open_stage=open_stage,
        created_stages=created_stages,
        expected_revision=expected_revision,
        fencing_token=fencing_token,
        now=reference_now,
    )
    store.release_lease(run_id=run.id, owner_id=owner_id, fencing_token=fencing_token)
    if not persisted:
        # A concurrent writer committed this attempt between the replay check and
        # the write: nothing of this advance was written, so the committed result
        # is authoritative and the advance reports it instead of an advance.
        committed = _committed_result(store, run, stage, attempt_number, input_revision)
        if committed is None:  # pragma: no cover - the conflict is a committed row
            raise RunnerError(
                f"stage {stage.value} of run {run.id!r} was not persisted and has no "
                "replayable committed result"
            )
        return _replay(committed, run_id=run.id, stage=stage)
    return RunAdvance(
        outcome=outcome_for(decision),
        stage=decision.stage,
        result=result,
        decision=decision,
    )


def _replay(committed: StageResult, *, run_id: str, stage: Stage) -> RunAdvance:
    """Report a committed stage result as the outcome of an advance that wrote nothing.

    ``failed``/``blocked`` results do not replay: they allow a retry of the same
    logical operation, which is a *new* attempt (ADR-006 p.3/p.7) — the
    retry/resume protocol of slice S2. Until it lands the driver refuses loudly
    rather than reopening an attempt whose result is already committed.
    """
    if committed.status not in REPLAYABLE_RESULT_STATUSES:
        raise AttemptAlreadyCommittedError(
            f"stage {stage.value} of run {run_id!r} already has a committed "
            f"{committed.status.value} result; a retry needs a new attempt "
            "(ADR-006 p.7, slice S2)"
        )
    return RunAdvance(
        outcome=RunAdvanceOutcome.REPLAYED,
        stage=stage,
        result=committed,
        decision=None,
    )


def _committed_result(
    store: RunStorePort,
    run: ChangeRun,
    stage: Stage,
    attempt_number: int,
    input_revision: str,
) -> StageResult | None:
    """Committed result of the stage operation and attempt, if any (ADR-006 p.3)."""
    return store.committed_result(
        run_id=run.id, stage=stage, attempt_number=attempt_number, input_revision=input_revision
    )


def _active_stage_runs(run: ChangeRun, stage: Stage) -> list[StageRun]:
    """Non-terminal stage runs of ``stage``, in the order the run accumulated them."""
    return [
        stage_run
        for stage_run in run.stages
        if stage_run.stage == stage and stage_run.status not in STAGE_TERMINAL_STATUSES
    ]


def _attempt_number(run: ChangeRun, stage: Stage) -> int:
    """Attempt number of the stage operation: the one in flight, else the first.

    ``flow._ensure_stage_run`` rejects a result whose attempt number does not
    match the active stage run, so the physical attempt opened here and the
    in-memory stage run must agree on it. Bumping the number for a retry of the
    same operation is slice S2 (ADR-006 p.7).
    """
    active = _active_stage_runs(run, stage)
    return active[-1].attempt_number if active else 1


def _pinned_revision(store: RunStorePort, run: ChangeRun, stage: Stage, change: Change) -> str:
    """Input revision of the stage operation: the one in flight, else the snapshot's.

    A stage run opened earlier carries the revision its operation was keyed by;
    a stage entered for the first time has none yet and is keyed by the snapshot
    revision (``RunStore.stage_input_revision``). Both the stage row and the
    stage context receive the same value, and it is read from the same stage run
    as the attempt number, so the result recomposes the operation key of the row
    it belongs to (ADR-006 p.3).
    """
    active = _active_stage_runs(run, stage)
    if active and active[-1].input_revision is not None:
        return active[-1].input_revision
    return store.stage_input_revision(change)


def _created_stages(
    store: RunStorePort,
    run: ChangeRun,
    *,
    known: tuple[StageRun, ...],
    executed: Stage,
    change: Change,
) -> list[StagePlacement]:
    """Stage rows the decision created that the store does not have yet (T-092).

    ``flow._advance`` starts the successor stage in memory only (``_start``); its
    row is what makes the run chain, so the decision declares it and
    ``persist_decision`` inserts it. The executed stage is excluded: its row is
    the operation ``open_attempt`` already opened. Each declared revision is
    written back onto the stage run, so the decision in memory and the rows it
    produced agree on the operation identity.
    """
    created: list[StagePlacement] = []
    for stage_run in run.stages:
        if stage_run.stage == executed or any(stage_run is item for item in known):
            continue
        stage_run.input_revision = _pinned_revision(store, run, stage_run.stage, change)
        created.append(
            StagePlacement(stage=stage_run.stage, input_revision=stage_run.input_revision)
        )
    return created
