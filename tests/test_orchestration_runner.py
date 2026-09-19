"""Unit tests of the durable run driver (T-092, ADR-006 p.3/p.6/p.8).

The driver is orchestration over two seams — the durable store and the stage
executor — so an in-memory store and a scripted executor exercise every outcome
and the failure paths without a database. The PostgreSQL wiring of the same
driver (rows, outbox, replay) is covered by
``tests/integration/test_runner_advance.py``.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

from dark_factory.changes.conversations import ReworkOrder, ReworkSummary
from dark_factory.changes.enums import (
    ChangeRequestStatus,
    FindingOrigin,
    FindingSeverity,
    Gate,
    GateStatus,
    Phase,
    RunStatus,
    Stage,
    StageStatus,
    StopOutcome,
)
from dark_factory.changes.findings import GateResult
from dark_factory.changes.implementation_contract import ImplementationContract
from dark_factory.changes.next_action import (
    ExecuteStageAction,
    MergeAction,
    NextAction,
    ReleaseAction,
    ReworkAction,
    StopAction,
    WaitForCIAction,
    WaitForInputAction,
)
from dark_factory.changes.run import Change, ChangeRun, StageResult, StageRun
from dark_factory.changes.run_records import SmokeProbeEvidence
from dark_factory.changes.usage import BudgetSnapshot
from dark_factory.orchestration.flow import FlowDecision, FlowStateError, InvalidFlowTransition
from dark_factory.orchestration.runner import (
    FactsProvider,
    ReleaseFactsProvider,
    RevisionResolver,
    RunAdvance,
    RunAdvanceOutcome,
    RunNotAdvanceableError,
    RunNotFoundError,
    StageExecutor,
    advance_run,
    next_stage,
    outcome_for,
)
from dark_factory.orchestration.stages.context import StageContext
from dark_factory.orchestration.stages.gates import GateObservation
from dark_factory.orchestration.stages.release import ReleaseObservation
from dark_factory.orchestration.state.run_store import OpenStage, StagePlacement
from tests.changes_factories import (
    make_change,
    make_change_request,
    make_contract,
    make_merge_approval,
    make_run,
)

NOW = datetime(2026, 9, 16, 12, 0, 0, tzinfo=UTC)
TTL = timedelta(minutes=5)
REVISION = "rev-1"
"""Input revision the fake store pins on every stage it creates."""


@dataclass
class FakeStore:
    """In-memory stand-in for ``state.run_store.RunStore`` (the driver's durable seam).

    ``load`` hands back the very run the driver mutates, so a test sees the
    domain state and the persisted one on the same object — the durable store
    reaches the same result through its rows. ``committed_result`` reads the
    recorded results back, like ``StageResultRepository.get``, so a second
    advance over the same operation replays exactly as it does against
    ``conflict_result`` models the one thing the fake cannot produce
    on its own: another writer committing the same attempt *between* the driver's
    replay check and its write, which makes ``persist_decision`` report that it
    wrote nothing. ``on_acquire_lease`` models the other race: another advance
    committing the attempt after the pre-lease read but before the driver re-reads
    the run under the lease — the case the S2 re-binding exists for.
    """

    run: ChangeRun
    history: list[StageResult] = field(default_factory=list)
    decisions: list[FlowDecision] = field(default_factory=list)
    attempts: list[OpenStage] = field(default_factory=list)
    expected_revisions: list[int] = field(default_factory=list)
    released: list[int] = field(default_factory=list)
    lease_ttls: list[timedelta] = field(default_factory=list)
    created_stages: list[StagePlacement] = field(default_factory=list)
    conflict_result: StageResult | None = None
    persist_conflict: bool = False
    """A writer conflict at persist time only, invisible to the earlier reads (resume path)."""
    on_acquire_lease: Callable[[], None] | None = None
    results_reads: int = 0
    next_token: int = 1

    @staticmethod
    def stage_input_revision(change: Change) -> str:
        return REVISION

    def load(self, execution_id: str) -> ChangeRun | None:
        return self.run if execution_id == self.run.id else None

    def load_history(self, execution_id: str) -> list[StageResult]:
        return list(self.history)

    def committed_result(
        self,
        *,
        run_id: str,
        stage: Stage,
        attempt_number: int,
        input_revision: str,
    ) -> StageResult | None:
        self.results_reads += 1
        if self.conflict_result is not None and self.results_reads > 1:
            return self.conflict_result
        return next(
            (
                result
                for result in self.history
                if result.run_id == run_id
                and result.stage == stage
                and result.attempt_number == attempt_number
                and result.input_revision == input_revision
            ),
            None,
        )

    def acquire_lease(self, *, run_id: str, owner_id: str, ttl: timedelta) -> int:
        assert run_id == self.run.id
        self.lease_ttls.append(ttl)
        if self.on_acquire_lease is not None:
            self.on_acquire_lease()
        token = self.next_token
        self.next_token += 1
        return token

    def release_lease(self, *, run_id: str, owner_id: str, fencing_token: int) -> bool:
        assert run_id == self.run.id
        self.released.append(fencing_token)
        return True

    def open_attempt(
        self, *, run: ChangeRun, stage: Stage, input_revision: str, attempt_number: int
    ) -> OpenStage:
        key = f"{run.id}:{stage.value}:{input_revision}"
        open_stage = OpenStage(
            stage_row_id=key,
            attempt_id=f"{key}:{attempt_number}",
            attempt_number=attempt_number,
            input_revision=input_revision,
        )
        self.attempts.append(open_stage)
        return open_stage

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
    ) -> bool:
        self.created_stages.extend(created_stages)
        if self.conflict_result is not None or self.persist_conflict:
            # Another writer committed this attempt first: nothing is written.
            return False
        for index, existing in enumerate(self.history):
            if not self._same_operation(existing, result):
                continue
            # The store's supersede rule (T-092 S3): a waiting checkpoint is
            # replaced in place by its own final outcome; every other repeat of
            # a recorded result writes nothing.
            if existing.status is StageStatus.WAITING and result.status is not StageStatus.WAITING:
                self.history[index] = result
                break
            return False
        else:
            self.history.append(result)
        self.decisions.append(decision)
        self.expected_revisions.append(expected_revision)
        return True

    @staticmethod
    def _same_operation(first: StageResult, second: StageResult) -> bool:
        """Whether both results address the same attempt of the same operation."""
        return (
            first.run_id == second.run_id
            and first.stage == second.stage
            and first.attempt_number == second.attempt_number
            and first.input_revision == second.input_revision
        )


def _stage_run(run: ChangeRun, stage: Stage, status: StageStatus) -> StageRun:
    return StageRun(
        id=f"{run.id}:{stage.value}:1", stage=stage, status=status, input_revision=REVISION
    )


def _fresh_run() -> ChangeRun:
    """A run as ``RunStore.create_run`` leaves it: the first stage, pending."""
    run = make_run()
    run.stages.append(_stage_run(run, Stage.SPECIFICATION, StageStatus.PENDING))
    return run


def _run_at_release() -> ChangeRun:
    """A run whose first four stages succeeded and whose release stage is pending."""
    run = make_run()
    for stage in (
        Stage.SPECIFICATION,
        Stage.PLANNING,
        Stage.CONSTRUCTION,
        Stage.REVIEW_VERIFICATION,
    ):
        run.stages.append(_stage_run(run, stage, StageStatus.SUCCEEDED))
    run.stages.append(_stage_run(run, Stage.RELEASE, StageStatus.PENDING))
    run.status = RunStatus.RUNNING
    return run


def _executor(
    *, status: StageStatus, next_action: NextAction, gate_results: list[GateResult] | None = None
) -> StageExecutor:
    """Scripted stage executor: the same result for whatever context it is given."""

    def execute(context: StageContext) -> StageResult:
        return StageResult(
            stage=context.stage,
            run_id=context.run_id,
            change_id=context.change.id,
            attempt_number=context.attempt_number,
            input_revision=context.input_revision,
            status=status,
            next_action=next_action,
            gate_results=gate_results if gate_results is not None else [],
            produced_at=NOW,
        )

    return execute


def _waiting() -> StageExecutor:
    return _executor(
        status=StageStatus.WAITING, next_action=WaitForInputAction(reason="waiting for a human")
    )


def _satisfied(gate: Gate) -> GateResult:
    return GateResult(gate=gate, status=GateStatus.PASSED, sha=REVISION)


def _advance(
    store: FakeStore,
    *,
    executor: StageExecutor,
    change: Change | None = None,
    gate_facts: FactsProvider | None = None,
    release_facts: ReleaseFactsProvider | None = None,
    revision_of: RevisionResolver | None = None,
) -> RunAdvance:
    return advance_run(
        store=store,
        change=change if change is not None else make_change(),
        run_id=store.run.id,
        owner_id="test-owner",
        executor=executor,
        revision_of=revision_of,
        gate_facts=gate_facts,
        release_facts=release_facts,
        lease_ttl=TTL,
        now=NOW,
    )


HEAD = "9f2c7ab1"
"""Head SHA the fake provider reports; the facts of a resolution are bound to it."""


def _run_at_planning_waiting() -> ChangeRun:
    """A run whose specification succeeded and whose planning stage waits for CI."""
    run = make_run()
    run.stages.append(_stage_run(run, Stage.SPECIFICATION, StageStatus.SUCCEEDED))
    run.stages.append(_stage_run(run, Stage.PLANNING, StageStatus.WAITING))
    run.status = RunStatus.WAITING
    return run


def _run_at_construction_waiting() -> ChangeRun:
    """A run whose first two stages succeeded and whose construction stage waits for CI."""
    run = make_run()
    for stage in (Stage.SPECIFICATION, Stage.PLANNING):
        run.stages.append(_stage_run(run, stage, StageStatus.SUCCEEDED))
    run.stages.append(_stage_run(run, Stage.CONSTRUCTION, StageStatus.WAITING))
    run.status = RunStatus.WAITING
    return run


def _run_at_review_waiting() -> ChangeRun:
    """A run whose first three stages succeeded and whose review stage waits for CI."""
    run = make_run()
    for stage in (Stage.SPECIFICATION, Stage.PLANNING, Stage.CONSTRUCTION):
        run.stages.append(_stage_run(run, stage, StageStatus.SUCCEEDED))
    run.stages.append(_stage_run(run, Stage.REVIEW_VERIFICATION, StageStatus.WAITING))
    run.status = RunStatus.WAITING
    return run


def _ci_checkpoint(run: ChangeRun, change: Change, stage: Stage) -> StageResult:
    """The committed ``waiting`` checkpoint of a stage parked on CI (ADR-006 p.8)."""
    return StageResult(
        stage=stage,
        run_id=run.id,
        change_id=change.id,
        attempt_number=1,
        input_revision=REVISION,
        status=StageStatus.WAITING,
        next_action=WaitForCIAction(
            reason="waiting for the pipeline",
            change_request=make_change_request() if stage is Stage.REVIEW_VERIFICATION else None,
        ),
        produced_at=NOW,
    )


@dataclass
class FakeFacts:
    """Canned ``FactsProvider``: one observation for every stage it is asked about."""

    observation: GateObservation | None
    calls: list[Stage] = field(default_factory=list)

    def __call__(self, run: ChangeRun, stage: Stage, change: Change) -> GateObservation | None:
        self.calls.append(stage)
        return self.observation


class RefusingFacts:
    """A ``FactsProvider`` that must never be called in the scenarios under test."""

    def __call__(self, run: ChangeRun, stage: Stage, change: Change) -> GateObservation | None:
        raise AssertionError(f"gate facts must not be observed for stage {stage.value}")


# --- next_stage -----------------------------------------------------------


def test_next_stage_starts_with_the_first_stage_of_the_route() -> None:
    assert next_stage(_fresh_run()) is Stage.SPECIFICATION


def test_next_stage_skips_completed_stages() -> None:
    run = make_run()
    run.stages.append(_stage_run(run, Stage.SPECIFICATION, StageStatus.SUCCEEDED))
    run.stages.append(_stage_run(run, Stage.CONSTRUCTION, StageStatus.PENDING))

    assert next_stage(run) is Stage.CONSTRUCTION


@pytest.mark.parametrize(
    "status",
    [
        StageStatus.IN_PROGRESS,
        StageStatus.WAITING,
        StageStatus.BLOCKED,
        StageStatus.FAILED,
    ],
)
def test_next_stage_keeps_a_non_terminal_stage_in_flight(status: StageStatus) -> None:
    run = _fresh_run()
    run.stages[0].status = status

    assert next_stage(run) is Stage.SPECIFICATION


def test_next_stage_is_none_without_a_non_terminal_stage() -> None:
    run = make_run()

    assert next_stage(run) is None

    run = _fresh_run()
    run.stages[0].status = StageStatus.SUCCEEDED

    assert next_stage(run) is None


# --- outcome_for ----------------------------------------------------------


@pytest.mark.parametrize(
    ("run_status", "outcome"),
    [
        (RunStatus.RUNNING, RunAdvanceOutcome.ADVANCED),
        (RunStatus.WAITING, RunAdvanceOutcome.WAITING),
        (RunStatus.BLOCKED, RunAdvanceOutcome.BLOCKED),
        (RunStatus.SUCCEEDED, RunAdvanceOutcome.COMPLETED),
        (RunStatus.FAILED, RunAdvanceOutcome.FAILED),
        (RunStatus.CANCELED, RunAdvanceOutcome.FAILED),
        (RunStatus.SUPERSEDED, RunAdvanceOutcome.FAILED),
    ],
)
def test_outcome_for_maps_the_run_status(run_status: RunStatus, outcome: RunAdvanceOutcome) -> None:
    decision = FlowDecision(
        stage=Stage.SPECIFICATION,
        action=WaitForInputAction(reason="waiting"),
        stage_status=StageStatus.WAITING,
        run_status=run_status,
        next_stage=None,
    )

    assert outcome_for(decision) is outcome


# --- advance_run: outcomes ------------------------------------------------


def test_advance_run_stops_waiting_with_the_result_persisted() -> None:
    run = _fresh_run()
    store = FakeStore(run=run)

    advance = _advance(store, executor=_waiting())

    assert advance.outcome is RunAdvanceOutcome.WAITING
    assert advance.stage is Stage.SPECIFICATION
    assert advance.decision is not None
    assert advance.decision.run_status is RunStatus.WAITING
    assert run.status is RunStatus.WAITING
    # Persisted before the wait (ADR-006 p.8), the lease released after it, and
    # the revision the run was loaded with guards the write (ADR-006 p.4).
    assert len(store.decisions) == 1
    assert store.expected_revisions == [1]
    assert store.released == [1]


def test_advance_run_stops_blocked_when_the_flow_stops_the_run() -> None:
    run = _fresh_run()
    store = FakeStore(run=run)

    advance = _advance(
        store,
        executor=_executor(
            status=StageStatus.BLOCKED,
            next_action=StopAction(outcome=StopOutcome.BLOCKED, reason="budget exhausted"),
        ),
    )

    assert advance.outcome is RunAdvanceOutcome.BLOCKED
    assert run.status is RunStatus.BLOCKED
    assert run.stages[0].status is StageStatus.BLOCKED


def test_advance_run_fails_the_run_on_a_failed_stop() -> None:
    run = _fresh_run()
    store = FakeStore(run=run)

    advance = _advance(
        store,
        executor=_executor(
            status=StageStatus.FAILED,
            next_action=StopAction(outcome=StopOutcome.FAILED, reason="unrecoverable"),
        ),
    )

    assert advance.outcome is RunAdvanceOutcome.FAILED
    assert run.status is RunStatus.FAILED


def test_advance_run_advances_exactly_one_stage_and_keeps_running() -> None:
    run = _fresh_run()
    store = FakeStore(run=run)

    advance = _advance(
        store,
        executor=_executor(
            status=StageStatus.SUCCEEDED,
            next_action=ExecuteStageAction(next_stage=Stage.PLANNING),
            # The design stage requires both human gates (ADR-029 p.1).
            gate_results=[_satisfied(Gate.SPECIFICATION), _satisfied(Gate.UI)],
        ),
    )

    assert advance.outcome is RunAdvanceOutcome.ADVANCED
    assert run.status is RunStatus.RUNNING
    assert [stage_run.stage for stage_run in run.stages] == [Stage.SPECIFICATION, Stage.PLANNING]
    assert run.stages[0].status is StageStatus.SUCCEEDED
    assert run.stages[1].status is StageStatus.PENDING


def test_advance_run_completes_the_run_at_the_release_stage() -> None:
    run = _run_at_release()
    store = FakeStore(run=run)

    advance = _advance(
        store,
        executor=_executor(
            status=StageStatus.SUCCEEDED,
            next_action=ReleaseAction(target_environment="dev"),
            gate_results=[_satisfied(Gate.RELEASE)],
        ),
    )

    assert advance.outcome is RunAdvanceOutcome.COMPLETED
    assert run.status is RunStatus.SUCCEEDED
    assert store.decisions[0].run_status is RunStatus.SUCCEEDED


def test_advance_run_starts_a_pending_run_through_the_domain_transition() -> None:
    run = _fresh_run()
    store = FakeStore(run=run)

    _advance(store, executor=_waiting())

    # ``_fresh_run`` is pending (``make_run``), so the driver produced two domain
    # edges — pending -> running -> waiting — and both went through the tables.
    assert run.status is RunStatus.WAITING
    assert store.decisions[0].run_status is RunStatus.WAITING


def test_advance_run_persists_the_loaded_revision_as_the_expected_one() -> None:
    run = _fresh_run()
    run.state_revision = 7
    store = FakeStore(run=run)

    _advance(store, executor=_waiting())

    assert store.expected_revisions == [7]


# --- advance_run: the inputs of the attempt -------------------------------


def test_advance_run_builds_the_context_from_the_run_and_the_snapshot() -> None:
    run = _fresh_run()
    run.budget = BudgetSnapshot(max_rework_rounds=4, used_rework_rounds=2)
    store = FakeStore(run=run)
    change = make_change()
    seen: list[StageContext] = []

    def execute(context: StageContext) -> StageResult:
        seen.append(context)
        return StageResult(
            stage=context.stage,
            run_id=context.run_id,
            change_id=context.change.id,
            attempt_number=context.attempt_number,
            input_revision=context.input_revision,
            status=StageStatus.WAITING,
            next_action=WaitForInputAction(reason="waiting"),
            produced_at=NOW,
        )

    _advance(store, executor=execute, change=change)

    assert len(seen) == 1
    context = seen[0]
    assert context.change is change
    assert context.stage is Stage.SPECIFICATION
    assert context.route is run.route
    assert context.run_id == run.id
    assert context.input_revision == REVISION
    assert context.budget is run.budget
    # The design stage carries the UI gate since ADR-029 p.1.
    assert context.required_gates == frozenset({Gate.SPECIFICATION, Gate.UI})


def test_advance_run_opened_the_attempt_of_the_operation_and_leased_the_run() -> None:
    run = _fresh_run()
    store = FakeStore(run=run)

    _advance(store, executor=_waiting())

    assert store.attempts == [
        OpenStage(
            stage_row_id=f"{run.id}:specification:{REVISION}",
            attempt_id=f"{run.id}:specification:{REVISION}:1",
            attempt_number=1,
            input_revision=REVISION,
        )
    ]
    assert store.lease_ttls == [TTL]


def test_advance_run_keys_a_new_stage_operation_by_the_snapshot_revision() -> None:
    run = _fresh_run()
    run.stages[0].input_revision = None
    store = FakeStore(run=run)

    _advance(store, executor=_waiting())

    assert store.attempts[0].input_revision == REVISION


# --- advance_run: the whole decision (created stages, replay, refusal) ---


def _committed_result(run: ChangeRun, change: Change) -> StageResult:
    """A committed result of the first stage, as an executor or another writer left it."""
    return StageResult(
        stage=Stage.SPECIFICATION,
        run_id=run.id,
        change_id=change.id,
        input_revision=REVISION,
        status=StageStatus.WAITING,
        next_action=WaitForInputAction(reason="committed by another writer"),
        produced_at=NOW,
    )


def test_advance_run_declares_the_successor_stage_the_decision_created() -> None:
    run = _fresh_run()
    store = FakeStore(run=run)

    advance = _advance(
        store,
        executor=_executor(
            status=StageStatus.SUCCEEDED,
            next_action=ExecuteStageAction(next_stage=Stage.PLANNING),
            gate_results=[_satisfied(Gate.SPECIFICATION), _satisfied(Gate.UI)],
        ),
    )

    assert advance.outcome is RunAdvanceOutcome.ADVANCED
    # The successor exists in memory only after apply_result; the driver declares
    # it so the store inserts the row the run needs to chain (D1).
    assert store.created_stages == [StagePlacement(stage=Stage.PLANNING, input_revision=REVISION)]
    assert run.stages[1].input_revision == REVISION


def test_advance_run_declares_no_new_stage_when_the_run_waits() -> None:
    run = _fresh_run()
    store = FakeStore(run=run)

    _advance(store, executor=_waiting())

    # The executed stage is excluded on purpose: its row is the operation
    # open_attempt already opened, not a new one.
    assert store.created_stages == []


def test_advance_run_replays_a_committed_result_without_touching_the_store() -> None:
    run = _fresh_run()
    store = FakeStore(run=run)

    first = _advance(store, executor=_waiting())
    second = _advance(store, executor=_waiting())

    assert first.outcome is RunAdvanceOutcome.WAITING
    assert second.outcome is RunAdvanceOutcome.REPLAYED
    assert second.decision is None
    assert second.result == first.result
    # A replay is inert: no second attempt, no lease, no decision, no write, and
    # the run is not moved through the transition table either (D2).
    assert len(store.attempts) == 1
    assert store.lease_ttls == [TTL]
    assert store.decisions == [first.decision]
    assert store.released == [1]
    assert run.status is RunStatus.WAITING


def test_advance_run_does_not_replay_a_result_of_another_input_revision() -> None:
    run = _fresh_run()
    change = make_change()
    store = FakeStore(run=run)
    store.history.append(
        _committed_result(run, change).model_copy(update={"input_revision": "rev-other"})
    )

    advance = _advance(store, executor=_waiting(), change=change)

    # The identity is (run, stage, input revision, attempt): another revision is
    # another logical operation, so this advance executes instead of replaying.
    assert advance.outcome is RunAdvanceOutcome.WAITING
    assert len(store.attempts) == 1


def test_advance_run_retries_a_blocked_attempt_as_a_new_attempt() -> None:
    run = _fresh_run()
    store = FakeStore(run=run)

    blocked = _advance(
        store,
        executor=_executor(
            status=StageStatus.BLOCKED,
            next_action=StopAction(outcome=StopOutcome.BLOCKED, reason="budget exhausted"),
        ),
    )
    assert blocked.outcome is RunAdvanceOutcome.BLOCKED
    assert [attempt.attempt_number for attempt in store.attempts] == [1]

    resumed = _advance(store, executor=_waiting())

    # FAILED/BLOCKED -> IN_PROGRESS: the repeat is a new physical attempt of the
    # same logical operation (ADR-006 p.7) — not a refusal and not a replay.
    assert resumed.outcome is RunAdvanceOutcome.WAITING
    assert [attempt.attempt_number for attempt in store.attempts] == [1, 2]
    assert store.attempts[-1].attempt_id.endswith(":2")
    assert store.attempts[-1].input_revision == REVISION
    assert resumed.result.attempt_number == 2
    assert run.stages[0].attempt_number == 2
    assert run.stages[0].status is StageStatus.WAITING


def test_advance_run_retries_a_failed_attempt_of_a_running_stage() -> None:
    run = _fresh_run()
    run.status = RunStatus.RUNNING
    run.stages[0].status = StageStatus.FAILED
    store = FakeStore(run=run)

    advance = _advance(store, executor=_waiting())

    # A rework leaves the stage FAILED while the run keeps running: the next
    # advance retries the operation as attempt 2 (ADR-006 p.7).
    assert advance.outcome is RunAdvanceOutcome.WAITING
    assert [attempt.attempt_number for attempt in store.attempts] == [2]
    assert advance.result.attempt_number == 2


def test_advance_run_rebinds_the_replay_check_to_the_state_under_the_lease() -> None:
    run = _fresh_run()
    change = make_change()
    store = FakeStore(run=run)
    committed = _committed_result(run, change)

    def commit_elsewhere() -> None:
        # Another advance wins the race after our pre-lease read but before we
        # could re-check: it commits the very attempt we were about to execute.
        store.history.append(committed)
        run.stages[0].status = StageStatus.WAITING

    store.on_acquire_lease = commit_elsewhere

    advance = _advance(store, executor=_waiting(), change=change)

    # The attempt number and the replay check are derived from the run under the
    # lease (ADR-024, условие 2): the committed result wins and the stage is not
    # executed a second time.
    assert advance.outcome is RunAdvanceOutcome.REPLAYED
    assert advance.result is committed
    assert advance.decision is None
    assert store.attempts == []
    assert store.decisions == []
    assert store.released == [1]


def test_advance_run_reports_a_replay_when_the_write_persisted_nothing() -> None:
    run = _fresh_run()
    change = make_change()
    committed = _committed_result(run, change)
    store = FakeStore(run=run, conflict_result=committed)

    advance = _advance(store, executor=_waiting(), change=change)

    # Another writer committed the attempt between the replay check and the
    # write: nothing of this advance was written, so the committed result is
    # authoritative and the lease is released instead of left behind (D4).
    assert advance.outcome is RunAdvanceOutcome.REPLAYED
    assert advance.decision is None
    assert advance.result is committed
    assert store.decisions == []
    assert store.released == [1]


# --- advance_run: failure paths ------------------------------------------


def test_advance_run_rejects_an_unknown_run() -> None:
    store = FakeStore(run=make_run())

    with pytest.raises(RunNotFoundError):
        advance_run(store=store, change=make_change(), run_id="run-missing", owner_id="test-owner")


def test_advance_run_refuses_a_terminal_run() -> None:
    run = _fresh_run()
    run.status = RunStatus.SUCCEEDED
    store = FakeStore(run=run)

    with pytest.raises(RunNotAdvanceableError):
        _advance(store, executor=_waiting())


def test_advance_run_refuses_a_run_without_an_active_stage() -> None:
    run = _fresh_run()
    run.stages[0].status = StageStatus.SUCCEEDED
    store = FakeStore(run=run)

    with pytest.raises(RunNotAdvanceableError):
        _advance(store, executor=_waiting())


def test_advance_run_surfaces_an_illegal_flow_transition() -> None:
    run = _fresh_run()
    store = FakeStore(run=run)

    with pytest.raises(InvalidFlowTransition):
        _advance(
            store,
            executor=_executor(
                status=StageStatus.SUCCEEDED,
                next_action=ExecuteStageAction(next_stage=Stage.RELEASE),
            ),
        )

    # ``apply_result`` is the only transition point: the illegal pair is refused
    # before anything is persisted and before the lease is released.
    assert store.decisions == []
    assert store.released == []
    assert run.status is RunStatus.RUNNING


def test_advance_run_refuses_a_result_status_that_contradicts_the_action() -> None:
    run = _fresh_run()
    store = FakeStore(run=run)

    with pytest.raises(FlowStateError):
        _advance(
            store,
            executor=_executor(
                status=StageStatus.FAILED,
                next_action=ExecuteStageAction(next_stage=Stage.PLANNING),
            ),
        )

    assert store.decisions == []


# --- SCM-derived revisions (ADR-006 p.4, slice S2) --------------------------


def test_advance_run_uses_the_injected_revision_resolver() -> None:
    # A stage entered for the first time has no pinned revision yet: its revision
    # comes from the injected resolver, not from the change snapshot's digest.
    run = _fresh_run()
    run.stages[0].input_revision = None
    store = FakeStore(run=run)
    calls: list[tuple[str, Stage]] = []

    def revision_of(change: Change, stage: Stage) -> str:
        calls.append((change.id, stage))
        return "scm-rev-1"

    advance = advance_run(
        store=store,
        change=make_change(),
        run_id=run.id,
        owner_id="test-owner",
        executor=_waiting(),
        revision_of=revision_of,
        lease_ttl=TTL,
        now=NOW,
    )

    # The advance re-derives the identity under the lease (ADR-024, условие 2),
    # so the resolver is consulted once per derivation — always for the same
    # (change, stage) pair, since it is deterministic.
    assert set(calls) == {("chg-001", Stage.SPECIFICATION)}
    assert advance.result.input_revision == "scm-rev-1"
    assert store.attempts[0].input_revision == "scm-rev-1"


def test_advance_run_defaults_to_the_store_revision_without_a_resolver() -> None:
    run = _fresh_run()
    run.stages[0].input_revision = None
    store = FakeStore(run=run)

    advance = _advance(store, executor=_waiting())

    assert advance.result.input_revision == REVISION


def test_advance_run_pins_the_created_successor_with_the_resolver() -> None:
    # The successor stage the decision creates must carry the same SCM-derived
    # revision the next advance will resolve, or the two would disagree on the
    # operation identity (ADR-006 p.3).
    run = _fresh_run()
    store = FakeStore(run=run)
    calls: list[tuple[str, Stage]] = []

    def revision_of(change: Change, stage: Stage) -> str:
        calls.append((change.id, stage))
        return f"scm-{stage.value}"

    advance = advance_run(
        store=store,
        change=make_change(),
        run_id=run.id,
        owner_id="test-owner",
        executor=_executor(
            status=StageStatus.SUCCEEDED,
            next_action=ExecuteStageAction(next_stage=Stage.PLANNING),
            gate_results=[_satisfied(Gate.SPECIFICATION), _satisfied(Gate.UI)],
        ),
        revision_of=revision_of,
        lease_ttl=TTL,
        now=NOW,
    )

    assert advance.outcome is RunAdvanceOutcome.ADVANCED
    # The executed stage was pinned, so only the created successor consulted the
    # resolver — the executed stage keeps the revision of its own operation.
    assert calls == [("chg-001", Stage.PLANNING)]
    assert run.stages[1].input_revision == "scm-planning"
    assert store.created_stages == [
        StagePlacement(stage=Stage.PLANNING, input_revision="scm-planning")
    ]


# --- wait resolution (T-092 S3, ADR-006 p.8) --------------------------------


def test_advance_run_replays_a_waiting_stage_without_gate_facts() -> None:
    """Without ``gate_facts`` the checkpoint is replayed and nothing at all is written."""
    run = _run_at_construction_waiting()
    change = make_change()
    store = FakeStore(run=run)
    checkpoint = _ci_checkpoint(run, change, Stage.CONSTRUCTION)
    store.history.append(checkpoint)

    advance = _advance(store, executor=_waiting(), change=change)

    assert advance.outcome is RunAdvanceOutcome.REPLAYED
    assert advance.result is checkpoint
    assert advance.decision is None
    assert store.lease_ttls == []
    assert store.attempts == []
    assert store.decisions == []
    assert store.released == []
    assert run.status is RunStatus.WAITING


def test_advance_run_replays_a_waiting_stage_on_an_unresolved_observation() -> None:
    """A pipeline still running resolves nothing: replay before any lease is taken."""
    run = _run_at_construction_waiting()
    change = make_change()
    store = FakeStore(run=run)
    checkpoint = _ci_checkpoint(run, change, Stage.CONSTRUCTION)
    store.history.append(checkpoint)
    facts = FakeFacts(GateObservation(head_sha=HEAD, merged=False, pipeline_status="in_progress"))

    advance = _advance(store, executor=_waiting(), change=change, gate_facts=facts)

    assert advance.outcome is RunAdvanceOutcome.REPLAYED
    assert advance.result is checkpoint
    # The observation is read-only and taken before the lease: an unresolved one
    # parks the wait again without a lease row, an attempt or a decision.
    assert facts.calls == [Stage.CONSTRUCTION]
    assert store.lease_ttls == []
    assert store.attempts == []
    assert store.decisions == []
    assert store.history == [checkpoint]
    assert run.status is RunStatus.WAITING


def test_advance_run_resolves_a_waiting_specification_stage_on_a_merged_request() -> None:
    """The observed merge of the spec request completes the human gate (T-043 increment 1).

    A purely human-gated stage parks on ``wait_for_input``; the human merges
    the stage's change request — the strongest form of the decision — and the
    next advance resumes the same attempt into planning.
    """
    run = make_run()
    run.stages.append(_stage_run(run, Stage.SPECIFICATION, StageStatus.WAITING))
    run.status = RunStatus.WAITING
    change = make_change()
    store = FakeStore(run=run)
    store.history.append(_ci_checkpoint(run, change, Stage.SPECIFICATION))
    facts = FakeFacts(GateObservation(head_sha=HEAD, merged=True, pipeline_status="success"))

    def revision_of(change: Change, stage: Stage) -> str:
        return f"scm-{stage.value}"

    advance = _advance(
        store, executor=_waiting(), change=change, gate_facts=facts, revision_of=revision_of
    )

    assert advance.outcome is RunAdvanceOutcome.ADVANCED
    assert advance.decision is not None
    assert advance.decision.next_stage is Stage.PLANNING
    assert advance.result.attempt_number == 1
    assert advance.result.input_revision == REVISION
    assert advance.result.status is StageStatus.SUCCEEDED
    assert isinstance(advance.result.next_action, ExecuteStageAction)
    assert [(item.gate, item.status, item.sha) for item in advance.result.gate_results] == [
        (Gate.SPECIFICATION, GateStatus.PASSED, HEAD),
        (Gate.UI, GateStatus.PASSED, HEAD),
    ]
    assert facts.calls == [Stage.SPECIFICATION, Stage.SPECIFICATION]
    assert store.created_stages == [
        StagePlacement(stage=Stage.PLANNING, input_revision="scm-planning")
    ]
    assert store.history == [advance.result]
    assert run.status is RunStatus.RUNNING
    assert run.stages[0].status is StageStatus.SUCCEEDED
    assert run.stages[1].status is StageStatus.PENDING


def test_advance_run_resolves_a_waiting_specification_stage_on_an_approval() -> None:
    """An approved, version-bound review of the spec request resolves the human gate."""
    run = make_run()
    run.stages.append(_stage_run(run, Stage.SPECIFICATION, StageStatus.WAITING))
    run.status = RunStatus.WAITING
    change = make_change()
    store = FakeStore(run=run)
    store.history.append(_ci_checkpoint(run, change, Stage.SPECIFICATION))
    approval = make_merge_approval(sha=HEAD).model_copy(update={"gate": Gate.SPECIFICATION})
    facts = FakeFacts(
        GateObservation(
            head_sha=HEAD, merged=False, pipeline_status="success", approvals=(approval,)
        )
    )

    advance = _advance(store, executor=_waiting(), change=change, gate_facts=facts)

    assert advance.outcome is RunAdvanceOutcome.ADVANCED
    assert advance.decision is not None
    assert advance.decision.next_stage is Stage.PLANNING
    assert advance.result.status is StageStatus.SUCCEEDED
    assert [(item.gate, item.status, item.sha) for item in advance.result.gate_results] == [
        (Gate.SPECIFICATION, GateStatus.PASSED, HEAD),
        (Gate.UI, GateStatus.PASSED, HEAD),
    ]
    assert run.stages[1].status is StageStatus.PENDING


def test_advance_run_keeps_a_merged_machine_gated_stage_waiting() -> None:
    """A merge observation resolves only the human-gated stages and the review stage."""
    run = _run_at_construction_waiting()
    change = make_change()
    store = FakeStore(run=run)
    store.history.append(_ci_checkpoint(run, change, Stage.CONSTRUCTION))
    facts = FakeFacts(GateObservation(head_sha=HEAD, merged=True, pipeline_status="success"))

    advance = _advance(store, executor=_waiting(), change=change, gate_facts=facts)

    assert advance.outcome is RunAdvanceOutcome.REPLAYED
    assert advance.result.status is StageStatus.WAITING
    assert store.history == [advance.result]


def test_advance_run_resolves_a_waiting_construction_stage_on_pipeline_success() -> None:
    """A green pipeline at the head SHA resumes the same attempt into the review stage."""
    run = _run_at_construction_waiting()
    change = make_change()
    store = FakeStore(run=run)
    store.history.append(_ci_checkpoint(run, change, Stage.CONSTRUCTION))
    facts = FakeFacts(GateObservation(head_sha=HEAD, merged=False, pipeline_status="success"))

    def revision_of(change: Change, stage: Stage) -> str:
        return f"scm-{stage.value}"

    advance = _advance(
        store, executor=_waiting(), change=change, gate_facts=facts, revision_of=revision_of
    )

    assert advance.outcome is RunAdvanceOutcome.ADVANCED
    assert advance.decision is not None
    assert advance.decision.next_stage is Stage.REVIEW_VERIFICATION
    # The resolution is the final outcome of the *same* attempt: the number the
    # checkpoint parked is kept, never incremented (ADR-006 p.8).
    assert advance.result.attempt_number == 1
    assert advance.result.input_revision == REVISION
    assert [attempt.attempt_number for attempt in store.attempts] == [1]
    assert advance.result.status is StageStatus.SUCCEEDED
    assert isinstance(advance.result.next_action, ExecuteStageAction)
    assert [(item.gate, item.status, item.sha) for item in advance.result.gate_results] == [
        (Gate.CODE, GateStatus.PASSED, HEAD),
    ]
    # The facts are read twice per resumed advance: once before the lease, once
    # re-derived under it (ADR-024, условие 2).
    assert facts.calls == [Stage.CONSTRUCTION, Stage.CONSTRUCTION]
    # The successor stage is declared with the revision the resolver pins for it.
    assert store.created_stages == [
        StagePlacement(stage=Stage.REVIEW_VERIFICATION, input_revision="scm-review_verification")
    ]
    assert run.stages[3].input_revision == "scm-review_verification"
    # The checkpoint was superseded in place by the succeeded outcome.
    assert store.history == [advance.result]
    assert store.released == [1]
    assert run.status is RunStatus.RUNNING
    assert run.stages[2].status is StageStatus.SUCCEEDED
    assert run.stages[3].status is StageStatus.PENDING


def test_advance_run_parks_a_planning_attempt_on_wait_for_ci() -> None:
    """A produced planning attempt parks on its machine gate in CI (T-043 regression).

    The live pilot crashed here: the planning executor returned ``wait_for_ci``,
    but the transition table refused the pair.
    """
    run = make_run()
    run.stages.append(_stage_run(run, Stage.SPECIFICATION, StageStatus.SUCCEEDED))
    run.stages.append(_stage_run(run, Stage.PLANNING, StageStatus.PENDING))
    run.status = RunStatus.RUNNING
    store = FakeStore(run=run)

    advance = _advance(
        store,
        executor=_executor(
            status=StageStatus.WAITING,
            next_action=WaitForCIAction(
                reason="waiting for the pipeline", change_request=make_change_request()
            ),
        ),
    )

    assert advance.outcome is RunAdvanceOutcome.WAITING
    assert advance.stage is Stage.PLANNING
    assert advance.decision is not None
    assert advance.decision.run_status is RunStatus.WAITING
    assert run.status is RunStatus.WAITING
    assert run.stages[1].status is StageStatus.WAITING
    assert len(store.decisions) == 1
    assert store.released == [1]


def test_advance_run_resolves_a_waiting_planning_stage_on_pipeline_success() -> None:
    """A green pipeline at the head SHA resumes the planning attempt into construction."""
    run = _run_at_planning_waiting()
    change = make_change()
    store = FakeStore(run=run)
    store.history.append(_ci_checkpoint(run, change, Stage.PLANNING))
    facts = FakeFacts(GateObservation(head_sha=HEAD, merged=False, pipeline_status="success"))

    def revision_of(change: Change, stage: Stage) -> str:
        return f"scm-{stage.value}"

    advance = _advance(
        store, executor=_waiting(), change=change, gate_facts=facts, revision_of=revision_of
    )

    assert advance.outcome is RunAdvanceOutcome.ADVANCED
    assert advance.decision is not None
    assert advance.decision.next_stage is Stage.CONSTRUCTION
    assert advance.result.attempt_number == 1
    assert advance.result.input_revision == REVISION
    assert [attempt.attempt_number for attempt in store.attempts] == [1]
    assert advance.result.status is StageStatus.SUCCEEDED
    assert isinstance(advance.result.next_action, ExecuteStageAction)
    assert [(item.gate, item.status, item.sha) for item in advance.result.gate_results] == [
        (Gate.PLANNING, GateStatus.PASSED, HEAD)
    ]
    assert facts.calls == [Stage.PLANNING, Stage.PLANNING]
    assert store.created_stages == [
        StagePlacement(stage=Stage.CONSTRUCTION, input_revision="scm-construction")
    ]
    assert run.stages[2].input_revision == "scm-construction"
    assert store.history == [advance.result]
    assert store.released == [1]
    assert run.status is RunStatus.RUNNING
    assert run.stages[1].status is StageStatus.SUCCEEDED
    assert run.stages[2].status is StageStatus.PENDING


def test_advance_run_resolves_a_pipeline_failure_into_a_rework_round() -> None:
    """A red pipeline feeds the bounded rework loop: the stage fails, the run continues."""
    run = _run_at_construction_waiting()
    change = make_change()
    store = FakeStore(run=run)
    store.history.append(_ci_checkpoint(run, change, Stage.CONSTRUCTION))
    facts = FakeFacts(GateObservation(head_sha=HEAD, merged=False, pipeline_status="failure"))

    advance = _advance(store, executor=_waiting(), change=change, gate_facts=facts)

    assert advance.outcome is RunAdvanceOutcome.ADVANCED
    assert advance.result.status is StageStatus.FAILED
    rework = advance.result.next_action
    assert isinstance(rework, ReworkAction)
    # The round is declared against the budget the builder saw (used=0), and the
    # flow spends it: the run's counter shows the spent round below.
    assert rework.round == 1
    assert rework.max_rounds == 3
    assert "failure" in rework.reason
    assert [item.origin for item in advance.result.findings] == [
        FindingOrigin.CI,
    ]
    assert all(item.severity is FindingSeverity.BLOCKER for item in advance.result.findings)
    assert [item.category for item in advance.result.findings] == ["code"]
    # The rework handler spent the round and re-enters construction: the same
    # stage-run occurrence ends FAILED while the run keeps running.
    assert run.budget.used_rework_rounds == 1
    assert run.status is RunStatus.RUNNING
    assert run.stages[2].status is StageStatus.FAILED
    assert advance.decision is not None
    assert advance.decision.next_stage is Stage.CONSTRUCTION
    # The checkpoint was replaced by the failed outcome of the same attempt.
    assert store.history == [advance.result]
    assert store.history[0].attempt_number == 1
    assert store.history[0].input_revision == REVISION


def test_advance_run_blocks_a_resolution_whose_rework_limit_is_spent() -> None:
    """The flow vetoes the rework the resolution declares when the limit is spent (FR-008)."""
    run = _run_at_construction_waiting()
    run.budget = BudgetSnapshot(max_rework_rounds=3, used_rework_rounds=3)
    change = make_change()
    store = FakeStore(run=run)
    store.history.append(_ci_checkpoint(run, change, Stage.CONSTRUCTION))
    facts = FakeFacts(GateObservation(head_sha=HEAD, merged=False, pipeline_status="failure"))

    advance = _advance(store, executor=_waiting(), change=change, gate_facts=facts)

    assert advance.outcome is RunAdvanceOutcome.BLOCKED
    assert advance.decision is not None
    assert isinstance(advance.decision.action, StopAction)
    assert "rework limit" in advance.decision.action.reason
    assert run.status is RunStatus.BLOCKED
    assert run.stages[2].status is StageStatus.BLOCKED
    # No second round was declared: the attempt is not retried and no successor
    # stage was started.
    assert [attempt.attempt_number for attempt in store.attempts] == [1]
    assert store.created_stages == []
    # The supersede itself still happened: the resolution outcome is durable.
    assert advance.result.status is StageStatus.FAILED
    rework = advance.result.next_action
    assert isinstance(rework, ReworkAction)
    assert rework.round == 4
    assert store.history == [advance.result]
    assert store.released == [1]


def test_advance_run_advances_to_release_on_an_observed_merge_with_approval() -> None:
    """A merged CR with a version-bound approval completes the review stage (ADR-011 p.2)."""
    run = _run_at_review_waiting()
    change = make_change()
    store = FakeStore(run=run)
    store.history.append(_ci_checkpoint(run, change, Stage.REVIEW_VERIFICATION))
    facts = FakeFacts(
        GateObservation(
            head_sha=HEAD,
            merged=True,
            pipeline_status="success",
            approvals=(make_merge_approval(HEAD),),
        )
    )

    advance = _advance(store, executor=_waiting(), change=change, gate_facts=facts)

    # The advance itself proves the merge context was right: the policy authorizes
    # a human merge only on the observed approval bound to the observed head SHA.
    assert advance.outcome is RunAdvanceOutcome.ADVANCED
    assert advance.decision is not None
    assert advance.decision.next_stage is Stage.RELEASE
    assert advance.result.status is StageStatus.SUCCEEDED
    merge = advance.result.next_action
    assert isinstance(merge, MergeAction)
    assert merge.change_request.number == 12
    # The observed merge is reflected in the ref, not the stale open status.
    assert merge.change_request.status is ChangeRequestStatus.MERGED
    assert [(item.gate, item.status, item.sha) for item in advance.result.gate_results] == [
        (Gate.REVIEW, GateStatus.PASSED, HEAD),
        (Gate.VERIFICATION, GateStatus.PASSED, HEAD),
    ]
    assert run.status is RunStatus.RUNNING
    assert run.stages[3].status is StageStatus.SUCCEEDED
    assert run.stages[4].stage is Stage.RELEASE
    assert store.history == [advance.result]
    assert store.created_stages == [StagePlacement(stage=Stage.RELEASE, input_revision=REVISION)]
    assert store.released == [1]


def test_advance_run_parks_the_observed_merge_for_the_human() -> None:
    """Merged but unapproved: the builder returns the merge result, the flow parks (FR-010)."""
    run = _run_at_review_waiting()
    change = make_change()
    store = FakeStore(run=run)
    store.history.append(_ci_checkpoint(run, change, Stage.REVIEW_VERIFICATION))
    facts = FakeFacts(
        GateObservation(head_sha=HEAD, merged=True, pipeline_status="success", approvals=())
    )

    advance = _advance(store, executor=_waiting(), change=change, gate_facts=facts)

    # The superseding result is the succeeded merge result; the decision the flow
    # produced from it is the wait for the human merge (ADR-011 p.2, manual mode).
    assert advance.outcome is RunAdvanceOutcome.WAITING
    assert advance.result.status is StageStatus.SUCCEEDED
    assert isinstance(advance.result.next_action, MergeAction)
    assert store.history == [advance.result]
    assert advance.decision is not None
    assert advance.decision.action.type == "wait_for_input"
    assert run.status is RunStatus.WAITING
    assert run.stages[3].status is StageStatus.WAITING
    assert store.created_stages == []
    assert store.released == [1]


def test_advance_run_replays_when_a_concurrent_writer_resolved_the_wait() -> None:
    """Re-derived under the lease, a concurrently committed resolution wins (ADR-024, условие 2)."""
    run = _run_at_construction_waiting()
    change = make_change()
    resolved = StageResult(
        stage=Stage.CONSTRUCTION,
        run_id=run.id,
        change_id=change.id,
        attempt_number=1,
        input_revision=REVISION,
        status=StageStatus.SUCCEEDED,
        next_action=ExecuteStageAction(next_stage=Stage.REVIEW_VERIFICATION),
        gate_results=[_satisfied(Gate.CODE)],
        produced_at=NOW,
    )
    store = FakeStore(run=run, conflict_result=resolved)
    store.history.append(_ci_checkpoint(run, change, Stage.CONSTRUCTION))
    facts = FakeFacts(GateObservation(head_sha=HEAD, merged=False, pipeline_status="success"))

    advance = _advance(store, executor=_waiting(), change=change, gate_facts=facts)

    # The committed result is no longer waiting: the concurrent resolution is
    # authoritative, nothing of this advance is written, and the lease is
    # released instead of left behind.
    assert advance.outcome is RunAdvanceOutcome.REPLAYED
    assert advance.result is resolved
    assert advance.decision is None
    assert store.attempts == []
    assert store.decisions == []
    assert store.released == [1]


def test_advance_run_replays_a_resume_whose_persist_wrote_nothing() -> None:
    """A writer conflict at persist time turns the whole resume into a replay (D4)."""
    run = _run_at_construction_waiting()
    change = make_change()
    checkpoint = _ci_checkpoint(run, change, Stage.CONSTRUCTION)
    store = FakeStore(run=run, persist_conflict=True)
    store.history.append(checkpoint)
    facts = FakeFacts(GateObservation(head_sha=HEAD, merged=False, pipeline_status="success"))

    advance = _advance(store, executor=_waiting(), change=change, gate_facts=facts)

    # The resume ran its full path but persisted nothing: the committed
    # checkpoint — still the only row — is reported, the supersede did not
    # happen, and the lease was released.
    assert advance.outcome is RunAdvanceOutcome.REPLAYED
    assert advance.result is checkpoint
    assert advance.decision is None
    assert store.decisions == []
    assert store.history == [checkpoint]
    assert store.released == [1]


def test_advance_run_does_not_observe_facts_without_a_waiting_checkpoint() -> None:
    """Facts are observed only for a parked stage: never on a replayed or fresh one."""
    change = make_change()

    # A committed non-waiting result replays before any observation.
    replay_run = _fresh_run()
    replay_store = FakeStore(run=replay_run)
    replay_store.history.append(
        StageResult(
            stage=Stage.SPECIFICATION,
            run_id=replay_run.id,
            change_id=change.id,
            input_revision=REVISION,
            status=StageStatus.SUCCEEDED,
            next_action=ExecuteStageAction(next_stage=Stage.PLANNING),
            gate_results=[_satisfied(Gate.SPECIFICATION), _satisfied(Gate.UI)],
            produced_at=NOW,
        )
    )

    replay = _advance(replay_store, executor=_waiting(), change=change, gate_facts=RefusingFacts())
    assert replay.outcome is RunAdvanceOutcome.REPLAYED

    # A stage without a committed result executes through the normal path.
    fresh_store = FakeStore(run=_fresh_run())
    fresh = _advance(fresh_store, executor=_waiting(), change=change, gate_facts=RefusingFacts())
    assert fresh.outcome is RunAdvanceOutcome.WAITING
    assert len(fresh_store.attempts) == 1


# --- release wait resolution (T-092 S4, ADR-024 §7 S4) -----------------------


def _run_at_release_waiting() -> ChangeRun:
    """A run whose four work stages succeeded and whose release stage waits for the rollout."""
    run = make_run()
    for stage in (
        Stage.SPECIFICATION,
        Stage.PLANNING,
        Stage.CONSTRUCTION,
        Stage.REVIEW_VERIFICATION,
    ):
        run.stages.append(_stage_run(run, stage, StageStatus.SUCCEEDED))
    run.stages.append(_stage_run(run, Stage.RELEASE, StageStatus.WAITING))
    run.status = RunStatus.WAITING
    return run


def _release_checkpoint(run: ChangeRun, change: Change) -> StageResult:
    """The committed ``waiting`` checkpoint of a release stage parked on the rollout."""
    return StageResult(
        stage=Stage.RELEASE,
        run_id=run.id,
        change_id=change.id,
        attempt_number=1,
        input_revision=REVISION,
        status=StageStatus.WAITING,
        next_action=WaitForCIAction(reason="waiting for the rollout"),
        produced_at=NOW,
    )


@dataclass
class FakeReleaseFacts:
    """Canned ``ReleaseFactsProvider``: one observation for every call it takes."""

    observation: ReleaseObservation | None
    calls: list[Stage] = field(default_factory=list)

    def __call__(self, run: ChangeRun, stage: Stage, change: Change) -> ReleaseObservation | None:
        self.calls.append(stage)
        return self.observation


def _released_observation() -> ReleaseObservation:
    """A full observation the release decision releases (digest, Argo, smoke)."""
    return ReleaseObservation(
        expected_digest="sha256:3f7a1c9d",
        observed_digest="sha256:3f7a1c9d",
        argo_sync_raw="Synced",
        argo_health_raw="Healthy",
        smoke=(SmokeProbeEvidence(name="http-health", passed=True, detail="HTTP 200"),),
    )


def test_advance_run_replays_a_waiting_release_stage_without_release_facts() -> None:
    """Without ``release_facts`` the release checkpoint is replayed — byte-identical to S3."""
    run = _run_at_release_waiting()
    change = make_change()
    store = FakeStore(run=run)
    checkpoint = _release_checkpoint(run, change)
    store.history.append(checkpoint)

    advance = _advance(store, executor=_waiting(), change=change, gate_facts=FakeFacts(None))

    assert advance.outcome is RunAdvanceOutcome.REPLAYED
    assert advance.result is checkpoint
    assert advance.decision is None
    assert store.lease_ttls == []
    assert store.attempts == []
    assert store.released == []
    assert run.status is RunStatus.WAITING


def test_advance_run_never_resolves_a_release_wait_from_gate_facts() -> None:
    """Pipeline observations never wake a release wait (the S4 guard, fail-closed)."""
    run = _run_at_release_waiting()
    change = make_change()
    store = FakeStore(run=run)
    store.history.append(_release_checkpoint(run, change))
    # Even a green pipeline at a merged head resolves nothing for the release stage.
    facts = FakeFacts(GateObservation(head_sha=HEAD, merged=True, pipeline_status="success"))

    advance = _advance(store, executor=_waiting(), change=change, gate_facts=facts)

    assert advance.outcome is RunAdvanceOutcome.REPLAYED
    assert advance.result.status is StageStatus.WAITING
    assert store.released == []


def test_advance_run_replays_a_release_wait_on_an_unresolved_observation() -> None:
    """Partial release facts resolve nothing: replay before any lease is taken."""
    run = _run_at_release_waiting()
    change = make_change()
    store = FakeStore(run=run)
    checkpoint = _release_checkpoint(run, change)
    store.history.append(checkpoint)
    facts = FakeReleaseFacts(
        ReleaseObservation(
            expected_digest="sha256:3f7a1c9d",
            observed_digest="sha256:3f7a1c9d",
            argo_sync_raw="Synced",
            argo_health_raw=None,  # health not observed yet: partial, fail-closed
        )
    )

    advance = _advance(store, executor=_waiting(), change=change, release_facts=facts)

    assert advance.outcome is RunAdvanceOutcome.REPLAYED
    assert advance.result is checkpoint
    assert facts.calls == [Stage.RELEASE]
    assert store.lease_ttls == []
    assert store.attempts == []
    assert store.decisions == []
    assert store.history == [checkpoint]


def test_advance_run_resolves_a_released_stage_into_a_completed_run() -> None:
    """Released release facts complete the run, superseding the checkpoint."""
    run = _run_at_release_waiting()
    change = make_change()
    store = FakeStore(run=run)
    store.history.append(_release_checkpoint(run, change))
    facts = FakeReleaseFacts(_released_observation())

    advance = _advance(store, executor=_waiting(), change=change, release_facts=facts)

    assert advance.outcome is RunAdvanceOutcome.COMPLETED
    assert advance.decision is not None
    assert isinstance(advance.result.next_action, ReleaseAction)
    assert advance.result.status is StageStatus.SUCCEEDED
    assert advance.result.attempt_number == 1
    assert advance.result.input_revision == REVISION
    assert [(item.gate, item.status) for item in advance.result.gate_results] == [
        (Gate.RELEASE, GateStatus.PASSED)
    ]
    # The facts are read twice per resumed advance: before the lease and under it.
    assert facts.calls == [Stage.RELEASE, Stage.RELEASE]
    # The release stage is the last of the route: no successor stage is created.
    assert store.created_stages == []
    # The checkpoint was superseded in place by the succeeded outcome of the same attempt.
    assert store.history == [advance.result]
    assert store.released == [1]
    assert run.status is RunStatus.SUCCEEDED
    assert run.stages[4].status is StageStatus.SUCCEEDED


def test_advance_run_resolves_a_failed_verification_into_a_blocked_run() -> None:
    """A failed verification blocks the run with the rollback signal (ADR-011 p.6)."""
    run = _run_at_release_waiting()
    change = make_change()
    store = FakeStore(run=run)
    store.history.append(_release_checkpoint(run, change))
    facts = FakeReleaseFacts(
        ReleaseObservation(
            expected_digest="sha256:3f7a1c9d",
            observed_digest="sha256:deadbeef",  # the deployment is not the promoted one
            argo_sync_raw="Synced",
            argo_health_raw="Healthy",
        )
    )

    advance = _advance(store, executor=_waiting(), change=change, release_facts=facts)

    assert advance.outcome is RunAdvanceOutcome.BLOCKED
    assert advance.decision is not None
    assert isinstance(advance.decision.action, StopAction)
    assert "does not match the observed digest" in advance.decision.action.reason
    assert advance.result.status is StageStatus.BLOCKED
    assert advance.result.release is not None
    assert advance.result.release.rollback_signal is not None
    assert store.history == [advance.result]
    assert store.released == [1]
    assert run.status is RunStatus.BLOCKED
    assert run.stages[4].status is StageStatus.BLOCKED


# --- the Construction entry gate is attributed to Construction (T-063) -------


def _run_at_construction_pending(*, contract: ImplementationContract | None) -> ChangeRun:
    """A run whose specification and planning succeeded and whose construction is pending."""
    run = make_run()
    run.implementation_contract = contract
    for stage in (Stage.SPECIFICATION, Stage.PLANNING):
        run.stages.append(_stage_run(run, stage, StageStatus.SUCCEEDED))
    run.stages.append(_stage_run(run, Stage.CONSTRUCTION, StageStatus.PENDING))
    run.status = RunStatus.RUNNING
    return run


def _advance_construction(store: FakeStore) -> RunAdvance:
    """Advance with the default executor — the deterministic path carries the gate."""
    return advance_run(
        store=store,
        change=make_change(),
        run_id=store.run.id,
        owner_id="test-owner",
        lease_ttl=TTL,
        now=NOW,
    )


def _opened_stages(store: FakeStore) -> set[str]:
    """Stage values of the attempts the driver opened, from the fake's attempt rows."""
    return {attempt.stage_row_id.split(":")[1] for attempt in store.attempts}


def test_the_construction_entry_gate_blocks_construction_not_planning() -> None:
    """T-063 (B2): a missing contract stops Construction, never the outgoing stage.

    The gate belongs to the stage being entered, so the stop and its diagnostics
    are attributed to the Construction attempt the driver opened — planning keeps
    its ``succeeded`` status and its attempt number.
    """
    run = _run_at_construction_pending(contract=None)
    store = FakeStore(run=run)

    advance = _advance_construction(store)

    assert advance.outcome is RunAdvanceOutcome.BLOCKED
    assert advance.stage is Stage.CONSTRUCTION
    assert advance.decision is not None
    assert advance.decision.stage_status is StageStatus.BLOCKED
    assert advance.decision.next_stage is None
    assert advance.result.attempt_number == 1
    assert isinstance(advance.result.next_action, StopAction)
    assert "no implementation contract attached" in advance.result.next_action.reason
    assert run.status is RunStatus.BLOCKED
    [planning] = [stage for stage in run.stages if stage.stage is Stage.PLANNING]
    assert planning.status is StageStatus.SUCCEEDED
    assert planning.attempt_number == 1
    assert _opened_stages(store) == {Stage.CONSTRUCTION.value}


def test_a_retry_of_the_entry_stop_never_re_runs_planning() -> None:
    """T-063 (B2): the retry is the next Construction attempt and only that.

    Before the fix the stop marked planning blocked, so the driver's retry
    re-executed the completed planning — a second LLM run and a second change
    request. The completed work must be inert: one planning occurrence, its own
    attempt, and no planning attempt ever opened again.
    """
    run = _run_at_construction_pending(contract=None)
    store = FakeStore(run=run)

    first = _advance_construction(store)
    second = _advance_construction(store)

    assert first.result.attempt_number == 1
    assert second.outcome is RunAdvanceOutcome.BLOCKED
    assert second.stage is Stage.CONSTRUCTION
    assert second.result.attempt_number == 2
    planning_occurrences = [stage for stage in run.stages if stage.stage is Stage.PLANNING]
    assert len(planning_occurrences) == 1
    assert planning_occurrences[0].status is StageStatus.SUCCEEDED
    assert planning_occurrences[0].attempt_number == 1
    assert _opened_stages(store) == {Stage.CONSTRUCTION.value}
    assert [result.stage for result in store.history] == [Stage.CONSTRUCTION, Stage.CONSTRUCTION]


def test_construction_advances_once_the_contract_is_approved() -> None:
    """The gate blocks only the missing approval: an approved contract lets construction run."""
    run = _run_at_construction_pending(contract=make_contract())
    store = FakeStore(run=run)

    advance = _advance_construction(store)

    assert advance.outcome is RunAdvanceOutcome.WAITING
    assert advance.stage is Stage.CONSTRUCTION
    assert isinstance(advance.result.next_action, WaitForInputAction)
    [planning] = [stage for stage in run.stages if stage.stage is Stage.PLANNING]
    assert planning.status is StageStatus.SUCCEEDED


# --- rework orders on a human-gated stage (T081, ADR-034 p.3) -----------------------


def _rework_order(order_id: str, revision: str, *comment_ids: str) -> ReworkOrder:
    return ReworkOrder(
        id=order_id,
        change_id="chg-001",
        phase=Phase.REQUIREMENTS,
        revisions={"spec/requirements/REQ-001.md": revision},
        comment_ids=tuple(comment_ids),
        issued_by="alice",
    )


def test_advance_run_turns_a_pending_rework_order_into_a_rework_round() -> None:
    """The operator's send-back resolves the waiting spec stage into a rework round."""
    run = make_run()
    run.stages.append(_stage_run(run, Stage.SPECIFICATION, StageStatus.WAITING))
    run.status = RunStatus.WAITING
    change = make_change()
    store = FakeStore(run=run)
    store.history.append(_ci_checkpoint(run, change, Stage.SPECIFICATION))
    facts = FakeFacts(
        GateObservation(
            head_sha=HEAD,
            merged=False,
            pipeline_status=None,
            rework_orders=(_rework_order("rw_1", HEAD, "cmt_1"),),
        )
    )

    advance = _advance(store, executor=_waiting(), change=change, gate_facts=facts)

    # A rework round is an advance into the rework target: the run is running again.
    assert advance.outcome is RunAdvanceOutcome.ADVANCED
    assert advance.result.status is StageStatus.FAILED
    assert advance.decision is not None
    assert isinstance(advance.decision.action, ReworkAction)
    assert advance.decision.action.round == 1
    assert "cmt_1" in advance.decision.action.reason
    # The round is spent on the run budget and the stage re-enters itself: the
    # failed stage run is retryable (ADR-006 p.7), so the next advance opens the
    # next attempt at the revision the round advances.
    assert run.budget.used_rework_rounds == 1
    assert advance.decision.next_stage is Stage.SPECIFICATION
    assert [stage.status for stage in run.stages] == [StageStatus.FAILED]
    assert run.status is RunStatus.RUNNING


def test_advance_run_escalates_a_rework_order_when_the_limit_is_exhausted() -> None:
    """An exhausted rework limit refuses the round and blocks with the loop's reason."""
    run = make_run()
    run.budget.used_rework_rounds = run.budget.max_rework_rounds
    run.stages.append(_stage_run(run, Stage.SPECIFICATION, StageStatus.WAITING))
    run.status = RunStatus.WAITING
    change = make_change()
    store = FakeStore(run=run)
    store.history.append(_ci_checkpoint(run, change, Stage.SPECIFICATION))
    facts = FakeFacts(
        GateObservation(
            head_sha=HEAD,
            merged=False,
            pipeline_status=None,
            rework_orders=(_rework_order("rw_1", HEAD, "cmt_1"),),
        )
    )

    advance = _advance(store, executor=_waiting(), change=change, gate_facts=facts)

    assert advance.outcome is RunAdvanceOutcome.BLOCKED
    assert advance.decision is not None
    assert isinstance(advance.decision.action, StopAction)
    assert "rework limit exhausted" in advance.decision.action.reason
    assert run.status is RunStatus.BLOCKED


def test_a_done_rework_order_does_not_resolve_the_wait() -> None:
    """Only a *pending* order is a send-back; history alone leaves the stage parked."""
    run = make_run()
    run.stages.append(_stage_run(run, Stage.SPECIFICATION, StageStatus.WAITING))
    run.status = RunStatus.WAITING
    change = make_change()
    store = FakeStore(run=run)
    store.history.append(_ci_checkpoint(run, change, Stage.SPECIFICATION))
    done = _rework_order("rw_1", "older", "cmt_1")
    done.start(round=1, run_id=run.id)
    done.finish(ReworkSummary(changed=("x",)))
    facts = FakeFacts(
        GateObservation(head_sha=HEAD, merged=False, pipeline_status=None, rework_orders=(done,))
    )

    advance = _advance(store, executor=_waiting(), change=change, gate_facts=facts)

    assert advance.outcome is RunAdvanceOutcome.REPLAYED
