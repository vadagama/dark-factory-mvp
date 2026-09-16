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

from dark_factory.changes.enums import Gate, GateStatus, RunStatus, Stage, StageStatus, StopOutcome
from dark_factory.changes.findings import GateResult
from dark_factory.changes.next_action import (
    ExecuteStageAction,
    NextAction,
    ReleaseAction,
    StopAction,
    WaitForInputAction,
)
from dark_factory.changes.run import Change, ChangeRun, StageResult, StageRun
from dark_factory.changes.usage import BudgetSnapshot
from dark_factory.orchestration.flow import FlowDecision, FlowStateError, InvalidFlowTransition
from dark_factory.orchestration.runner import (
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
from dark_factory.orchestration.state.run_store import OpenStage, StagePlacement
from tests.changes_factories import make_change, make_run

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
        if self.conflict_result is not None:
            # Another writer committed this attempt first: nothing is written.
            return False
        self.decisions.append(decision)
        self.expected_revisions.append(expected_revision)
        self.history.append(result)
        return True


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
    store: FakeStore, *, executor: StageExecutor, change: Change | None = None
) -> RunAdvance:
    return advance_run(
        store=store,
        change=change if change is not None else make_change(),
        run_id=store.run.id,
        owner_id="test-owner",
        executor=executor,
        lease_ttl=TTL,
        now=NOW,
    )


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
            gate_results=[_satisfied(Gate.SPECIFICATION)],
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
    assert context.required_gates == frozenset({Gate.SPECIFICATION})


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
            gate_results=[_satisfied(Gate.SPECIFICATION)],
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
