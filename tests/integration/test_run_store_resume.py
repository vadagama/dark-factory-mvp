"""Durable wait-resolution semantics of the run store (T-092 S3, ADR-006 p.8).

The unit suite drives the same protocol over an in-memory store; here the
row-level half lives: the supersede of a ``waiting`` checkpoint by its own
final outcome, the re-open of a waiting attempt, and the distinct, deterministic
event id of a resolution. Skipped without ``DARK_FACTORY_TEST_DATABASE_URL``
like every PostgreSQL-backed test (ADR-004).
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from dark_factory.changes.enums import Gate, GateStatus, Route, Stage, StageStatus, StopOutcome
from dark_factory.changes.findings import GateResult
from dark_factory.changes.keys import attempt_id, operation_key
from dark_factory.changes.next_action import ExecuteStageAction, StopAction
from dark_factory.changes.run import Change, StageResult
from dark_factory.changes.usage import BudgetSnapshot
from dark_factory.orchestration.flow import FlowDecision, apply_result
from dark_factory.orchestration.runner import (
    RunAdvance,
    RunAdvanceOutcome,
    StageExecutor,
    advance_run,
)
from dark_factory.orchestration.stages.context import StageContext
from dark_factory.orchestration.state.change_store import ChangeRepository
from dark_factory.orchestration.state.engine import session_scope
from dark_factory.orchestration.state.models import Attempt, OutboxEvent
from dark_factory.orchestration.state.models import StageResult as StageResultRow
from dark_factory.orchestration.state.repositories import StateError
from dark_factory.orchestration.state.run_store import (
    DEFAULT_LEASE_TTL,
    OpenStage,
    RunStore,
    StagePlacement,
)
from dark_factory.orchestration.state.stage_results import (
    RecordOutcome,
    StageResultRepository,
)
from tests.changes_factories import make_change

NOW = datetime(2026, 9, 16, 12, 0, 0, tzinfo=UTC)


def _seed_change(session_factory: sessionmaker[Session], change: Change) -> None:
    with session_scope(session_factory) as session:
        ChangeRepository(session).create(change)


def _create_run(session_factory: sessionmaker[Session], change: Change) -> str:
    with session_scope(session_factory) as session:
        run = RunStore(session).create_run(
            change_id=change.id,
            route=Route.STANDARD,
            provider=change.product.provider,
            budget=BudgetSnapshot(),
            input_revision=RunStore.stage_input_revision(change),
        )
        return run.id


def _advance(
    session_factory: sessionmaker[Session],
    change: Change,
    run_id: str,
    *,
    executor: StageExecutor | None = None,
) -> RunAdvance:
    with session_scope(session_factory) as session:
        return advance_run(
            store=RunStore(session),
            change=change,
            run_id=run_id,
            owner_id="test-owner",
            executor=executor,
            now=NOW,
        )


def _blocking_executor(context: StageContext) -> StageResult:
    """Scripted executor that ends the attempt ``blocked`` — a retryable status."""
    return StageResult(
        stage=context.stage,
        run_id=context.run_id,
        change_id=context.change.id,
        attempt_number=context.attempt_number,
        input_revision=context.input_revision,
        status=StageStatus.BLOCKED,
        next_action=StopAction(outcome=StopOutcome.BLOCKED, reason="budget exhausted"),
        produced_at=NOW,
    )


def _park_on_wait(
    session_factory: sessionmaker[Session], change: Change, run_id: str
) -> StageResult:
    """Drive one deterministic advance so the first stage parks on its wait."""
    return _advance(session_factory, change, run_id).result


def _resolution_result(
    run_id: str, change: Change, revision: str, *, attempt_number: int
) -> StageResult:
    """The final outcome of a parked attempt: a green specification gate (FR-009)."""
    return StageResult(
        stage=Stage.SPECIFICATION,
        run_id=run_id,
        change_id=change.id,
        attempt_number=attempt_number,
        input_revision=revision,
        status=StageStatus.SUCCEEDED,
        next_action=ExecuteStageAction(next_stage=Stage.PLANNING, reason="wait resolved"),
        gate_results=[GateResult(gate=Gate.SPECIFICATION, status=GateStatus.PASSED, sha=revision)],
        produced_at=NOW,
    )


def test_record_supersedes_a_waiting_checkpoint_with_the_resolution(
    session_factory: sessionmaker[Session],
) -> None:
    change = make_change()
    _seed_change(session_factory, change)
    revision = RunStore.stage_input_revision(change)
    run_id = _create_run(session_factory, change)
    _park_on_wait(session_factory, change, run_id)
    resolution = _resolution_result(run_id, change, revision, attempt_number=1)

    with session_scope(session_factory) as session:
        repository = StageResultRepository(session)
        assert repository.record_outcome(resolution) is RecordOutcome.SUPERSEDED
        # The supersede is one-shot: the checkpoint is already replaced, so a
        # repeat of the resolution writes nothing (FR-014) — the boolean API
        # reports it like any repeat of a final outcome.
        assert repository.record(resolution) is False
        # The resolution is readable under the same attempt id, and it replaced
        # the checkpoint in place: one row per attempt, now final (ADR-006 p.8).
        assert repository.get(run_id, Stage.SPECIFICATION, 1) == resolution
        rows = list(session.execute(select(StageResultRow)).scalars())
        assert len(rows) == 1
        assert rows[0].status == StageStatus.SUCCEEDED.value


def test_record_keeps_a_waiting_repeat_inert(session_factory: sessionmaker[Session]) -> None:
    change = make_change()
    _seed_change(session_factory, change)
    run_id = _create_run(session_factory, change)
    checkpoint = _park_on_wait(session_factory, change, run_id)

    with session_scope(session_factory) as session:
        repository = StageResultRepository(session)
        # waiting -> waiting: a repeat of the checkpoint writes nothing — a
        # waiting result carries no outcome, so it cannot supersede anything.
        assert repository.record_outcome(checkpoint) is RecordOutcome.REPLAYED
        assert repository.record(checkpoint) is False
        assert repository.get(run_id, Stage.SPECIFICATION, 1) == checkpoint
        assert len(list(session.execute(select(StageResultRow)).scalars())) == 1


def test_record_never_rewrites_a_final_outcome(session_factory: sessionmaker[Session]) -> None:
    change = make_change()
    _seed_change(session_factory, change)
    revision = RunStore.stage_input_revision(change)
    run_id = _create_run(session_factory, change)
    checkpoint = _park_on_wait(session_factory, change, run_id)
    resolution = _resolution_result(run_id, change, revision, attempt_number=1)

    with session_scope(session_factory) as session:
        repository = StageResultRepository(session)
        assert repository.record_outcome(resolution) is RecordOutcome.SUPERSEDED
        # final -> waiting and final -> final are both inert (FR-014): a
        # committed outcome is never rewritten, not even by its own resolution.
        assert repository.record_outcome(checkpoint) is RecordOutcome.REPLAYED
        assert repository.record_outcome(resolution) is RecordOutcome.REPLAYED
        assert repository.record(resolution) is False
        assert repository.get(run_id, Stage.SPECIFICATION, 1) == resolution


def test_open_attempt_reopens_a_waiting_attempt_and_refuses_a_final_one(
    session_factory: sessionmaker[Session],
) -> None:
    change = make_change()
    _seed_change(session_factory, change)
    revision = RunStore.stage_input_revision(change)
    run_id = _create_run(session_factory, change)
    _park_on_wait(session_factory, change, run_id)
    key = operation_key(run_id, Stage.SPECIFICATION, revision)

    with session_scope(session_factory) as session:
        store = RunStore(session)
        run = store.load(run_id)
        assert run is not None
        open_stage = store.open_attempt(
            run=run, stage=Stage.SPECIFICATION, input_revision=revision, attempt_number=1
        )
        assert open_stage.attempt_id == attempt_id(key, 1)
        attempt = session.get(Attempt, open_stage.attempt_id)
        assert attempt is not None
        # The external-wait checkpoint re-opens as the same physical attempt:
        # in_progress again, finished_at cleared, no second attempt row
        # (ADR-006 p.8) — the resolution must recompose this attempt id.
        assert attempt.status == StageStatus.IN_PROGRESS.value
        assert attempt.finished_at is None
        assert len(list(session.execute(select(Attempt)).scalars())) == 1
        store.finish_attempt(open_stage.attempt_id, StageStatus.SUCCEEDED, now=NOW)

    # A finalized attempt is never reopened: the committed outcome is durable.
    with pytest.raises(StateError), session_scope(session_factory) as session:
        store = RunStore(session)
        run = store.load(run_id)
        assert run is not None
        store.open_attempt(
            run=run, stage=Stage.SPECIFICATION, input_revision=revision, attempt_number=1
        )


def test_the_resolution_persists_a_distinct_deterministic_event(
    session_factory: sessionmaker[Session],
) -> None:
    change = make_change()
    _seed_change(session_factory, change)
    revision = RunStore.stage_input_revision(change)
    run_id = _create_run(session_factory, change)
    _park_on_wait(session_factory, change, run_id)
    key = operation_key(run_id, Stage.SPECIFICATION, revision)

    with session_scope(session_factory) as session:
        checkpoint_event = session.execute(
            select(OutboxEvent).where(OutboxEvent.aggregate_id == run_id)
        ).scalar_one()

    resolution = _resolution_result(run_id, change, revision, attempt_number=1)
    opened: OpenStage | None = None
    decision: FlowDecision | None = None
    with session_scope(session_factory) as session:
        store = RunStore(session)
        run = store.load(run_id)
        assert run is not None
        expected_revision = run.state_revision
        token = store.acquire_lease(run_id=run_id, owner_id="test-owner", ttl=DEFAULT_LEASE_TTL)
        opened = store.open_attempt(
            run=run, stage=Stage.SPECIFICATION, input_revision=revision, attempt_number=1
        )
        decision = apply_result(run, resolution)
        assert store.persist_decision(
            run=run,
            result=resolution,
            decision=decision,
            open_stage=opened,
            created_stages=[StagePlacement(stage=Stage.PLANNING, input_revision=revision)],
            expected_revision=expected_revision,
            fencing_token=token,
            now=NOW,
        )

    with session_scope(session_factory) as session:
        events = list(
            session.execute(
                select(OutboxEvent)
                .where(OutboxEvent.aggregate_id == run_id)
                .order_by(OutboxEvent.sequence)
            ).scalars()
        )
        assert len(events) == 2
        # The resolution completes the attempt the checkpoint's event already
        # named, so it carries its own distinct id (T-092 S3, ADR-016 p.3).
        assert events[1].event_id != checkpoint_event.event_id
        assert events[1].causation_id == attempt_id(key, 1)
        assert events[1].payload["result_status"] == StageStatus.SUCCEEDED.value

    assert opened is not None and decision is not None
    with session_scope(session_factory) as session:
        store = RunStore(session)
        run = store.load(run_id)
        assert run is not None
        token = store.acquire_lease(run_id=run_id, owner_id="test-owner", ttl=DEFAULT_LEASE_TTL)
        # The supersede already happened, so a repeat of the resolution is a
        # replay: nothing is published and the deterministic id stays unique.
        assert (
            store.persist_decision(
                run=run,
                result=resolution,
                decision=decision,
                open_stage=opened,
                created_stages=[],
                expected_revision=run.state_revision,
                fencing_token=token,
                now=NOW,
            )
            is False
        )

    with session_scope(session_factory) as session:
        assert len(list(session.execute(select(OutboxEvent)).scalars())) == 2


def test_a_resolved_wait_supersedes_only_its_own_checkpoint_row(
    session_factory: sessionmaker[Session],
) -> None:
    """Checkpoint, commit, reopen, re-open, resolution: the history stays honest."""
    change = make_change()
    _seed_change(session_factory, change)
    revision = RunStore.stage_input_revision(change)
    run_id = _create_run(session_factory, change)
    key = operation_key(run_id, Stage.SPECIFICATION, revision)

    # Attempt 1 ends blocked (a final outcome), attempt 2 parks on the wait.
    blocked = _advance(session_factory, change, run_id, executor=_blocking_executor)
    assert blocked.outcome is RunAdvanceOutcome.BLOCKED
    _park_on_wait(session_factory, change, run_id)

    with session_scope(session_factory) as session:
        first_row = session.get(StageResultRow, attempt_id(key, 1))
        assert first_row is not None
        assert first_row.status == StageStatus.BLOCKED.value
        blocked_payload = dict(first_row.payload)

    resolution = _resolution_result(run_id, change, revision, attempt_number=2)
    with session_scope(session_factory) as session:
        store = RunStore(session)
        run = store.load(run_id)
        assert run is not None
        expected_revision = run.state_revision
        token = store.acquire_lease(run_id=run_id, owner_id="test-owner", ttl=DEFAULT_LEASE_TTL)
        open_stage = store.open_attempt(
            run=run, stage=Stage.SPECIFICATION, input_revision=revision, attempt_number=2
        )
        decision = apply_result(run, resolution)
        assert store.persist_decision(
            run=run,
            result=resolution,
            decision=decision,
            open_stage=open_stage,
            created_stages=[StagePlacement(stage=Stage.PLANNING, input_revision=revision)],
            expected_revision=expected_revision,
            fencing_token=token,
            now=NOW,
        )
        store.release_lease(run_id=run_id, owner_id="test-owner", fencing_token=token)

    with session_scope(session_factory) as session:
        repository = StageResultRepository(session)
        # The blocked first attempt is untouched (FR-014); the waiting checkpoint
        # of attempt 2 is gone, superseded in place by the resolution.
        assert repository.get(run_id, Stage.SPECIFICATION, 1) == blocked.result
        assert repository.get(run_id, Stage.SPECIFICATION, 2) == resolution
        rows = list(session.execute(select(StageResultRow)).scalars())
        assert len(rows) == 2
        first_row = session.get(StageResultRow, attempt_id(key, 1))
        assert first_row is not None
        assert dict(first_row.payload) == blocked_payload

        reconstructed = RunStore(session).load(run_id)
        assert reconstructed is not None
        specification = reconstructed.stages[0]
        assert specification.status is StageStatus.SUCCEEDED
        assert specification.attempt_number == 2
        assert [stage.stage for stage in reconstructed.stages] == [
            Stage.SPECIFICATION,
            Stage.PLANNING,
        ]
        assert reconstructed.stages[1].status is StageStatus.PENDING
