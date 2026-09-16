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
2. the committed result of the operation the advance would execute is looked up
   **before any write**: a ``succeeded``/``waiting`` result is replayed — the
   advance reports it and writes nothing, the lease row included (ADR-006 p.3,
   ADR-024 p.3, the same policy as ``factory stage run``);
3. otherwise the run lease is acquired with its fencing token (ADR-006 p.6) and
   everything is re-derived from the run **under the lease**: a concurrent
   advance may have committed the very attempt in the window between 1 and 3, so
   the attempt number and the replay check are bound to the state the lease
   protects instead of to a stale read (ADR-024, условие 2 приёмки S2);
4. the physical attempt is opened (ADR-006 p.3): for a committed
   ``failed``/``blocked`` attempt the number advances — the retry of the same
   logical operation (ADR-006 p.7) — and the inserted attempt row is the durable
   guard that a second writer can never execute the same attempt twice;
5. the fixed ``StageContext`` is assembled (FR-001) with the run's persisted
   budget and the attempt number, and the injected :class:`StageExecutor`
   produces the result;
6. ``apply_result`` is called — the single flow transition point — with the
   run's history, the merge context and the observed human decisions;
7. the decision is persisted atomically with its ``run.stage_completed`` event
   (ADR-006 p.8, ADR-016 p.1/p.5) and the lease is released.

``apply_result`` decides in memory and the durable writer mirrors that decision
through the same domain transition tables; the run status additionally passes
``ExecutionRepository.update_status``, which validates the optimistic revision,
the fencing token and (since S2) the domain ``RUN_STATUS_TRANSITIONS`` table.

The caller owns the transaction and the clock: ``advance_run`` never commits,
takes ``now`` for determinism and expects the caller (``session_scope``) to
commit once — an exception rolls the whole advance back, lease included.

Known limitation of slice S1 (T-092) lifted in S2 by injection:

- **A reworked stage with an unchanged input revision** — rework re-enters a
  stage with the same change snapshot, so the S1 revision (the snapshot digest)
  maps the reworked stage onto its own earlier operation row (ADR-006 p.3) and a
  reconstruction resumes the stage the rework left. The driver now accepts a
  ``revision_of`` resolver (``ScmRevision``, ADR-006 p.4): the composition root
  supplies the SCM-backed one, which reads the product revision the stage starts
  from, so rework advances the revision and the re-entered stage is a new
  operation. Without a resolver the S1 snapshot digest stays the default, so the
  behaviour of an uninstrumented caller is unchanged.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol

from dark_factory.changes.enums import RunStatus, Stage
from dark_factory.changes.findings import Decision
from dark_factory.changes.run import (
    RETRYABLE_STAGE_STATUSES,
    RUN_TERMINAL_STATUSES,
    STAGE_TERMINAL_STATUSES,
    Change,
    ChangeRun,
    StageResult,
    StageRun,
)
from dark_factory.flows.routes import route_profile
from dark_factory.orchestration.flow import FlowDecision, apply_result
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


class RevisionResolver(Protocol):
    """Resolves the input revision a stage starts from (ADR-006 p.4, slice S2).

    The default is the change snapshot's digest (``RunStore.stage_input_revision``,
    slice S1). The composition root injects an SCM-backed resolver instead, so the
    revision of a stage is the product commit it consumes and rework — which does
    not change the snapshot — still produces a new revision and therefore a new
    logical operation.
    """

    def __call__(self, change: Change, stage: Stage) -> str: ...


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
    executor: StageExecutor | None = None,
    revision_of: RevisionResolver | None = None,
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
    deadline limit and the timestamps. ``revision_of`` optionally supplies the
    input revision of a stage entered for the first time (ADR-006 p.4, slice S2:
    the SCM-derived revision); without it the change snapshot's digest is used,
    so an uninstrumented caller sees the unchanged S1 behaviour. ``executor``
    defaults to the deterministic stage path ("waiting"/"blocked" only); the
    composition root injects the harness-backed one, which is how the working
    path reaches adapters without any core module importing them (ADR-024 p.5).

    The whole decision is persisted, not only its stage: the successor stage the
    flow advanced into is inserted as a ``pending`` row of its own operation, so
    the next advance finds it and the run chains.

    Replay comes first and writes nothing: a committed result of the operation
    the advance would execute is returned as :attr:`RunAdvanceOutcome.REPLAYED`
    before any mutation, so a repeat never touches the durable rows — the lease
    row included (ADR-024 p.3).

    A stage whose attempt ended ``failed``/``blocked`` is retried: the next
    execution gets the next attempt number and opens a new physical attempt of
    the same logical operation (ADR-006 p.7). The attempt number and the replay
    check are re-derived from the run **under the lease** (ADR-024, условие 2),
    because a concurrent advance may commit the attempt between the first read
    and the write; the attempt row the store inserts is the durable guard that
    no second writer executes the same attempt.

    Raises :class:`RunNotFoundError` for an unknown run and
    :class:`RunNotAdvanceableError` for a terminal one; a flow violation
    (``InvalidFlowTransition``, ``FlowStateError``, ``InvalidStatusTransition``)
    propagates, so an illegal transition can never be papered over.
    """
    reference_now = now if now is not None else datetime.now(UTC)
    stage_executor = executor if executor is not None else deterministic_stage_executor
    run = _advanceable_run(store, run_id)
    stage = _next_stage_to_execute(run)
    attempt_number = _attempt_number(run, stage)
    input_revision = _pinned_revision(store, run, stage, change, revision_of)
    committed = _committed_result(store, run, stage, attempt_number, input_revision)
    if committed is not None:
        # Fast replay (ADR-024 p.3): a committed result of the operation is
        # authoritative and a repeat is inert — no lease row, no attempt, no
        # mutation of any kind.
        return _replay(committed, stage=stage)

    # From here the advance writes. Take the lease first and re-derive the whole
    # identity from the run under it (ADR-024, условие 2): the attempt number of
    # a retry is a race otherwise, and the row open_attempt inserts is the
    # durable guard against a second execution of the same attempt.
    fencing_token = store.acquire_lease(run_id=run.id, owner_id=owner_id, ttl=lease_ttl)
    run = _advanceable_run(store, run_id)
    stage = _next_stage_to_execute(run)
    attempt_number = _attempt_number(run, stage)
    input_revision = _pinned_revision(store, run, stage, change, revision_of)
    committed = _committed_result(store, run, stage, attempt_number, input_revision)
    if committed is not None:
        # A concurrent advance committed the very attempt in the window above:
        # the committed result is authoritative, nothing of this advance is
        # written, and the lease is released instead of left behind.
        store.release_lease(run_id=run.id, owner_id=owner_id, fencing_token=fencing_token)
        return _replay(committed, stage=stage)

    expected_revision = run.state_revision
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
        attempt_number=attempt_number,
    )
    result = stage_executor(context)
    decision = apply_result(
        run,
        result,
        history=store.load_history(run.id),
        now=reference_now,
        merge_context=merge_context,
        human_decisions=human_decisions,
    )
    created_stages = _created_stages(
        store, run, known=known_stage_runs, executed=stage, change=change, revision_of=revision_of
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
        # A concurrent writer committed this attempt between the last replay check
        # and the write: nothing of this advance was written, so the committed
        # result is authoritative and the advance reports it instead of an advance.
        committed = _committed_result(store, run, stage, attempt_number, input_revision)
        if committed is None:  # pragma: no cover - the conflict is a committed row
            raise RunnerError(
                f"stage {stage.value} of run {run.id!r} was not persisted and has no "
                "committed result"
            )
        return _replay(committed, stage=stage)
    return RunAdvance(
        outcome=outcome_for(decision),
        stage=decision.stage,
        result=result,
        decision=decision,
    )


def _advanceable_run(store: RunStorePort, run_id: str) -> ChangeRun:
    """The run of ``run_id``, or a loud error when it cannot be advanced."""
    run = store.load(run_id)
    if run is None:
        raise RunNotFoundError(f"run {run_id!r} does not exist")
    if run.status in RUN_TERMINAL_STATUSES:
        raise RunNotAdvanceableError(f"run {run_id!r} is terminal ({run.status.value})")
    return run


def _next_stage_to_execute(run: ChangeRun) -> Stage:
    """Next stage of ``run``, or a loud error when its route is exhausted."""
    stage = next_stage(run)
    if stage is None:
        raise RunNotAdvanceableError(f"run {run.id!r} has no stage left to execute")
    return stage


def _replay(committed: StageResult, *, stage: Stage) -> RunAdvance:
    """Report a committed stage result as the outcome of an advance that wrote nothing.

    A committed result of the operation is authoritative for any status: the
    advance neither executes the stage again nor touches the durable rows. A
    ``failed``/``blocked`` result does not *block* a retry — the next attempt
    gets the next number (``_attempt_number``, ADR-006 p.7), so a retry never
    looks at this attempt's committed row — but when such a result is what a
    racing writer committed for the very attempt this advance had opened, it is
    reported honestly instead of as an advance that did not happen.
    """
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
    """Attempt number the next execution of ``stage`` gets (ADR-006 p.7).

    A stage whose active attempt ended ``failed``/``blocked`` is retried: the
    next execution is the *next* physical attempt of the same logical operation,
    so a committed non-replayable result is never re-used as the identity of a
    new attempt. Every other active state keeps its number: a stage entered for
    the first time starts at attempt 1 and a ``waiting`` attempt is resumed with
    its own result already committed (ADR-006 p.8).

    ``flow._ensure_stage_run`` accepts exactly this pairing — the same attempt
    for a resume, the next one for a retry — and rejects anything else as stale.
    """
    active = _active_stage_runs(run, stage)
    if not active:
        return 1
    current = active[-1]
    if current.status in RETRYABLE_STAGE_STATUSES:
        return current.attempt_number + 1
    return current.attempt_number


def _pinned_revision(
    store: RunStorePort,
    run: ChangeRun,
    stage: Stage,
    change: Change,
    revision_of: RevisionResolver | None,
) -> str:
    """Input revision of the stage operation: the one in flight, else a fresh one.

    A stage run opened earlier carries the revision its operation was keyed by;
    a stage entered for the first time has none yet. Its revision comes from the
    injected ``revision_of`` resolver (the SCM-derived revision of ADR-006 p.4)
    when the caller has one, and from the change snapshot otherwise
    (``RunStore.stage_input_revision``, the S1 default). Both the stage row and
    the stage context receive the same value, and it is read from the same stage
    run as the attempt number, so the result recomposes the operation key of the
    row it belongs to (ADR-006 p.3).
    """
    active = _active_stage_runs(run, stage)
    if active and active[-1].input_revision is not None:
        return active[-1].input_revision
    if revision_of is not None:
        return revision_of(change, stage)
    return store.stage_input_revision(change)


def _created_stages(
    store: RunStorePort,
    run: ChangeRun,
    *,
    known: tuple[StageRun, ...],
    executed: Stage,
    change: Change,
    revision_of: RevisionResolver | None,
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
        stage_run.input_revision = _pinned_revision(
            store, run, stage_run.stage, change, revision_of
        )
        created.append(
            StagePlacement(stage=stage_run.stage, input_revision=stage_run.input_revision)
        )
    return created
