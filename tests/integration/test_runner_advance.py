"""Durable run driver against PostgreSQL (T-092, ADR-006 p.3/p.4/p.8, ADR-016 p.5).

The unit suite drives the same driver over an in-memory store; here the real
store is exercised: one advance must leave a consistent set of rows
(``execution``, ``stage``, ``attempt``, ``stage_result``, ``outbox``), a repeat
of the same operation must write nothing a second time, a transition outside the
domain table must be refused, the migration must be reversible and the CLI must
read back what it persisted.
"""

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect, select
from sqlalchemy.orm import Session, sessionmaker

from dark_factory.changes.enums import (
    GateStatus,
    Provider,
    Route,
    RunStatus,
    Stage,
    StageStatus,
)
from dark_factory.changes.findings import GateResult
from dark_factory.changes.keys import operation_key
from dark_factory.changes.next_action import ExecuteStageAction
from dark_factory.changes.run import Change, InvalidStatusTransition, StageResult
from dark_factory.changes.usage import BudgetSnapshot
from dark_factory.cli.main import EXIT_OK, EXIT_WAITING, main
from dark_factory.flows.routes import route_profile
from dark_factory.orchestration.runner import (
    RunAdvance,
    RunAdvanceOutcome,
    StageExecutor,
    advance_run,
    deterministic_stage_executor,
)
from dark_factory.orchestration.stages.context import StageContext
from dark_factory.orchestration.state.change_store import ChangeRepository
from dark_factory.orchestration.state.engine import session_scope
from dark_factory.orchestration.state.models import (
    Attempt,
    EventDelivery,
    Execution,
    ExecutionLease,
    OutboxEvent,
)
from dark_factory.orchestration.state.models import Stage as StageRow
from dark_factory.orchestration.state.models import StageResult as StageResultRow
from dark_factory.orchestration.state.repositories import StateError
from dark_factory.orchestration.state.run_store import (
    STAGE_COMPLETED_CONSUMERS,
    RunStore,
)
from dark_factory.ports.events import EventType
from tests.changes_factories import make_change

REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL_ENV = "DARK_FACTORY_TEST_DATABASE_URL"
MIGRATION_HEAD = "0004_runner_state"
MIGRATION_PARENT = "0003_route_risk_classes"
NOW = datetime(2026, 9, 16, 12, 0, 0, tzinfo=UTC)


def _seed_change(session_factory: sessionmaker[Session], change: Change) -> None:
    with session_scope(session_factory) as session:
        ChangeRepository(session).create(change)


def _create_run(
    session_factory: sessionmaker[Session], change: Change, *, budget: BudgetSnapshot | None = None
) -> str:
    with session_scope(session_factory) as session:
        run = RunStore(session).create_run(
            change_id=change.id,
            route=Route.STANDARD,
            provider=change.product.provider,
            budget=budget if budget is not None else BudgetSnapshot(),
            input_revision=RunStore.stage_input_revision(change),
        )
        return run.id


def _advance(
    session_factory: sessionmaker[Session],
    change: Change,
    run_id: str,
    *,
    executor: StageExecutor = deterministic_stage_executor,
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


def _advancing_executor(context: StageContext) -> StageResult:
    """Scripted executor that lets the stage advance — the deterministic path never can (FR-009).

    An honest S1 executor: the required gates of the stage are reported as passed
    on the pinned revision, as CI would report them on the final SHA. Only the
    stages whose route allows ``execute_stage`` are covered; the merge and release
    transitions need their own facts (T-026).
    """
    next_stage = route_profile(context.route).next_stage(context.stage)
    assert next_stage is not None
    return StageResult(
        stage=context.stage,
        run_id=context.run_id,
        change_id=context.change.id,
        input_revision=context.input_revision,
        status=StageStatus.SUCCEEDED,
        next_action=ExecuteStageAction(next_stage=next_stage),
        gate_results=[
            GateResult(gate=gate, status=GateStatus.PASSED, sha=context.input_revision or "")
            for gate in sorted(context.required_gates, key=lambda gate: gate.value)
        ],
        produced_at=NOW,
    )


def test_advance_run_persists_the_whole_decision(session_factory: sessionmaker[Session]) -> None:
    change = make_change()
    _seed_change(session_factory, change)
    revision = RunStore.stage_input_revision(change)
    budget = BudgetSnapshot(max_rework_rounds=4)
    run_id = _create_run(session_factory, change, budget=budget)

    advance = _advance(session_factory, change, run_id)

    # The deterministic S1 executor evaluates no gate on a product SHA (FR-009),
    # so the honest outcome of the first stage is a durable wait.
    assert advance.outcome is RunAdvanceOutcome.WAITING
    assert advance.stage is Stage.SPECIFICATION

    key = operation_key(run_id, Stage.SPECIFICATION, revision)
    with session_scope(session_factory) as session:
        execution = session.get(Execution, run_id)
        assert execution is not None
        assert execution.change_id == change.id
        assert execution.route == Route.STANDARD.value
        assert execution.provider == Provider.GITHUB.value
        assert execution.status == RunStatus.WAITING.value
        # pending -> running -> waiting, and the persisted revision mirrors the
        # domain one instead of lagging one edge behind it.
        assert execution.state_revision == 3
        assert execution.budget == budget.model_dump(mode="json")
        assert execution.implementation_contract is None
        assert execution.finished_at is None

        stage_row = session.execute(
            select(StageRow).where(StageRow.execution_id == run_id)
        ).scalar_one()
        assert stage_row.id == key
        assert stage_row.operation_key == key
        assert stage_row.stage == Stage.SPECIFICATION.value
        assert stage_row.status == StageStatus.WAITING.value
        assert stage_row.input_revision == revision
        assert stage_row.state_revision == 3
        assert stage_row.attempt_count == 1
        assert stage_row.started_at is not None

        attempt = session.get(Attempt, f"{key}:1")
        assert attempt is not None
        assert attempt.stage_id == key
        assert attempt.attempt_number == 1
        assert attempt.status == StageStatus.WAITING.value
        assert attempt.finished_at is not None

        result_row = session.get(StageResultRow, f"{key}:1")
        assert result_row is not None
        assert result_row.run_id == run_id
        assert result_row.change_id == change.id
        assert result_row.operation_key == key
        assert result_row.status == StageStatus.WAITING.value
        stored = StageResult.model_validate(result_row.payload)
        assert stored.stage is Stage.SPECIFICATION
        assert stored.input_revision == revision
        assert stored == advance.result

        events = list(
            session.execute(select(OutboxEvent).where(OutboxEvent.aggregate_id == run_id)).scalars()
        )
        assert len(events) == 1
        event = events[0]
        assert event.event_type == EventType.RUN_STAGE_COMPLETED.value
        assert event.run_id == run_id
        assert event.stage == Stage.SPECIFICATION.value
        assert event.aggregate_version == 3
        assert event.sequence == 1
        assert event.payload["stage_status"] == StageStatus.WAITING.value
        assert event.payload["run_status"] == RunStatus.WAITING.value
        deliveries = set(
            session.execute(
                select(EventDelivery.consumer_id).where(EventDelivery.event_id == event.event_id)
            ).scalars()
        )
        assert deliveries == set(STAGE_COMPLETED_CONSUMERS)


def test_advance_run_round_trips_through_the_store(
    session_factory: sessionmaker[Session],
) -> None:
    change = make_change()
    _seed_change(session_factory, change)
    budget = BudgetSnapshot(max_rework_rounds=2, used_rework_rounds=1)
    run_id = _create_run(session_factory, change, budget=budget)

    _advance(session_factory, change, run_id)

    with session_scope(session_factory) as session:
        loaded = RunStore(session).load(run_id)

    assert loaded is not None
    assert loaded.id == run_id
    assert loaded.change_id == change.id
    assert loaded.status is RunStatus.WAITING
    assert loaded.state_revision == 3
    assert loaded.budget == budget
    assert loaded.implementation_contract is None
    assert len(loaded.stages) == 1
    stage_run = loaded.stages[0]
    assert stage_run.stage is Stage.SPECIFICATION
    assert stage_run.status is StageStatus.WAITING
    assert stage_run.state_revision == 3
    assert stage_run.attempt_number == 1
    assert stage_run.input_revision == RunStore.stage_input_revision(change)
    assert stage_run.started_at is not None
    assert stage_run.finished_at is None


def test_repeating_the_advance_writes_nothing_twice(
    session_factory: sessionmaker[Session],
) -> None:
    change = make_change()
    _seed_change(session_factory, change)
    revision = RunStore.stage_input_revision(change)
    run_id = _create_run(session_factory, change)

    first = _advance(session_factory, change, run_id)
    key = operation_key(run_id, Stage.SPECIFICATION, revision)
    with session_scope(session_factory) as session:
        attempt_row = session.get(Attempt, f"{key}:1")
        assert attempt_row is not None
        committed = (attempt_row.status, attempt_row.finished_at)

    second = _advance(session_factory, change, run_id)

    assert first.outcome is RunAdvanceOutcome.WAITING
    assert second.outcome is RunAdvanceOutcome.REPLAYED
    assert second.decision is None
    assert second.result == first.result
    # The attempt is the idempotency key of the decision (ADR-006 p.3) and the
    # replay is inert: no second result, no second event, no lease left behind,
    # and no mutation of the finalized attempt — its status and finished_at are
    # exactly what the first advance committed (D2).
    with session_scope(session_factory) as session:
        assert len(list(session.execute(select(StageRow)).scalars())) == 1
        assert len(list(session.execute(select(Attempt)).scalars())) == 1
        assert session.get(StageResultRow, f"{key}:1") is not None
        assert len(list(session.execute(select(StageResultRow)).scalars())) == 1
        assert len(list(session.execute(select(OutboxEvent)).scalars())) == 1
        replayed_row = session.get(Attempt, f"{key}:1")
        assert replayed_row is not None
        assert (replayed_row.status, replayed_row.finished_at) == committed
        execution = session.get(Execution, run_id)
        assert execution is not None
        assert execution.status == RunStatus.WAITING.value
        assert execution.state_revision == 3
        assert session.get(ExecutionLease, ("execution", run_id)) is None


def test_open_attempt_refuses_to_reopen_a_committed_attempt(
    session_factory: sessionmaker[Session],
) -> None:
    change = make_change()
    _seed_change(session_factory, change)
    revision = RunStore.stage_input_revision(change)
    run_id = _create_run(session_factory, change)
    _advance(session_factory, change, run_id)

    # The structural guarantee behind "a replay never clears finished_at": an
    # attempt whose result is committed is refused instead of reopened.
    with pytest.raises(StateError), session_scope(session_factory) as session:
        store = RunStore(session)
        run = store.load(run_id)
        assert run is not None
        store.open_attempt(
            run=run,
            stage=Stage.SPECIFICATION,
            input_revision=revision,
            attempt_number=1,
        )

    with session_scope(session_factory) as session:
        attempt_row = session.get(
            Attempt, f"{operation_key(run_id, Stage.SPECIFICATION, revision)}:1"
        )
        assert attempt_row is not None
        assert attempt_row.status == StageStatus.WAITING.value
        assert attempt_row.finished_at is not None


def test_advance_run_chains_into_the_stage_the_decision_created(
    session_factory: sessionmaker[Session],
) -> None:
    change = make_change()
    _seed_change(session_factory, change)
    revision = RunStore.stage_input_revision(change)
    run_id = _create_run(session_factory, change)

    first = _advance(session_factory, change, run_id, executor=_advancing_executor)

    assert first.outcome is RunAdvanceOutcome.ADVANCED
    assert first.decision is not None
    assert first.decision.next_stage is Stage.PLANNING
    # The successor stage exists in memory only until the decision is persisted:
    # without its row the durable run could not chain (D1).
    with session_scope(session_factory) as session:
        rows = list(
            session.execute(select(StageRow).where(StageRow.execution_id == run_id)).scalars()
        )
        planning = session.get(StageRow, operation_key(run_id, Stage.PLANNING, revision))
        assert planning is not None
        assert planning.status == StageStatus.PENDING.value
        assert planning.input_revision == revision
        assert planning.attempt_count == 0
    assert {row.stage: row.status for row in rows} == {
        Stage.SPECIFICATION.value: StageStatus.SUCCEEDED.value,
        Stage.PLANNING.value: StageStatus.PENDING.value,
    }

    # The chain continues: the next advance executes the stage the decision created.
    second = _advance(session_factory, change, run_id)

    assert second.outcome is RunAdvanceOutcome.WAITING
    assert second.stage is Stage.PLANNING
    with session_scope(session_factory) as session:
        planning = session.get(StageRow, operation_key(run_id, Stage.PLANNING, revision))
        assert planning is not None
        assert planning.status == StageStatus.WAITING.value


def test_advance_stage_refuses_a_transition_outside_the_domain_table(
    session_factory: sessionmaker[Session],
) -> None:
    change = make_change()
    _seed_change(session_factory, change)
    revision = RunStore.stage_input_revision(change)
    run_id = _create_run(session_factory, change)
    key = operation_key(run_id, Stage.SPECIFICATION, revision)

    with session_scope(session_factory) as session:
        RunStore(session).advance_stage(key, StageStatus.SUCCEEDED, now=NOW)

    with pytest.raises(InvalidStatusTransition), session_scope(session_factory) as session:
        RunStore(session).advance_stage(key, StageStatus.IN_PROGRESS, now=NOW)

    # The refused transition wrote nothing: a terminal stage stays terminal.
    with session_scope(session_factory) as session:
        stage_row = session.get(StageRow, key)
        assert stage_row is not None
        assert stage_row.status == StageStatus.SUCCEEDED.value
        assert stage_row.state_revision == 3


def test_runner_state_migration_is_reversible(
    state_engine: Engine, session_factory: sessionmaker[Session]
) -> None:
    url = os.environ[TEST_DATABASE_URL_ENV]
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    columns = {"budget", "implementation_contract"}

    try:
        command.downgrade(config, MIGRATION_PARENT)
        assert columns.isdisjoint(_execution_columns(state_engine))
    finally:
        command.upgrade(config, MIGRATION_HEAD)

    assert columns <= _execution_columns(state_engine)


def test_factory_run_advance_and_status_read_the_persisted_run(
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DATABASE_URL", os.environ[TEST_DATABASE_URL_ENV])
    change = make_change()
    _seed_change(session_factory, change)

    assert main(["run", "advance", "--change-id", change.id, "--json"]) == EXIT_WAITING
    payload = json.loads(capsys.readouterr().out)
    run_id = payload["run_id"]
    assert payload["outcome"] == RunAdvanceOutcome.WAITING.value
    assert payload["stage"] == Stage.SPECIFICATION.value
    assert payload["run_status"] == RunStatus.WAITING.value
    assert payload["attempt_number"] == 1
    assert payload["reason"]

    assert main(["run", "status", "--run-id", run_id, "--json"]) == EXIT_OK
    status = json.loads(capsys.readouterr().out)
    assert status["id"] == run_id
    assert status["status"] == RunStatus.WAITING.value
    assert [stage["stage"] for stage in status["stages"]] == [Stage.SPECIFICATION.value]
    assert status["stages"][0]["status"] == StageStatus.WAITING.value
    assert status["stages"][0]["attempt_number"] == 1

    # The same snapshot resolves the same run: a second advance resumes it
    # instead of starting a duplicate one, and it is not advanced past the wait —
    # the committed result is replayed and nothing is written.
    assert main(["run", "advance", "--change-id", change.id, "--json"]) == EXIT_WAITING
    replay = json.loads(capsys.readouterr().out)
    assert replay["run_id"] == run_id
    assert replay["outcome"] == RunAdvanceOutcome.REPLAYED.value
    assert replay["persisted"] is False
    assert replay["result_status"] == StageStatus.WAITING.value
    assert replay["run_status"] is None
    with session_scope(session_factory) as session:
        runs = list(
            session.execute(select(Execution).where(Execution.change_id == change.id)).scalars()
        )
        assert [run.id for run in runs] == [run_id]
        assert runs[0].state_revision == 3


def _execution_columns(engine: Engine) -> set[str]:
    return {column["name"] for column in inspect(engine).get_columns("execution")}
