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
    StopOutcome,
)
from dark_factory.changes.findings import GateResult
from dark_factory.changes.keys import operation_key
from dark_factory.changes.next_action import ExecuteStageAction, StopAction
from dark_factory.changes.run import Change, InvalidStatusTransition, StageResult
from dark_factory.changes.usage import BudgetSnapshot
from dark_factory.cli.main import EXIT_OK, EXIT_WAITING, RunAdvanceArgs, main
from dark_factory.cli.runner import run_advance_command
from dark_factory.orchestration.routes import route_profile
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
from dark_factory.orchestration.state.repositories import (
    ContractConflictError,
    ExecutionRepository,
    LeaseLostError,
    StateError,
)
from dark_factory.orchestration.state.run_store import (
    DEFAULT_LEASE_TTL,
    STAGE_COMPLETED_CONSUMERS,
    RunStore,
)
from dark_factory.ports.events import EventType
from tests.changes_factories import make_change, make_contract

REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL_ENV = "DARK_FACTORY_TEST_DATABASE_URL"
# The head is the revision under test only by convention; ``MIGRATION_PARENT`` is
# the revision below the runner-state migration whose columns are asserted, so it
# stays two revisions back once a newer head is added (T065).
MIGRATION_HEAD = "0005_products"
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
        attempt_number=context.attempt_number,
        input_revision=context.input_revision,
        status=StageStatus.SUCCEEDED,
        next_action=ExecuteStageAction(next_stage=next_stage),
        gate_results=[
            GateResult(gate=gate, status=GateStatus.PASSED, sha=context.input_revision or "")
            for gate in sorted(context.required_gates, key=lambda gate: gate.value)
        ],
        produced_at=NOW,
    )


def _blocking_executor(context: StageContext) -> StageResult:
    """Scripted executor that ends the attempt ``blocked`` — a retryable status (ADR-006 p.7)."""
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
    _advance(session_factory, change, run_id, executor=_blocking_executor)

    # The structural guarantee behind "a replay never clears finished_at": an
    # attempt whose result is committed is refused instead of reopened. The
    # fixture ends the attempt ``blocked`` — a final, committed outcome; a
    # ``waiting`` checkpoint, by contrast, is the resumable external-wait state
    # of ADR-006 p.8 and re-opens through the resume path (T-092 S3).
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
        assert attempt_row.status == StageStatus.BLOCKED.value
        assert attempt_row.finished_at is not None


def test_a_blocked_attempt_is_retried_as_the_next_attempt(
    session_factory: sessionmaker[Session],
) -> None:
    change = make_change()
    _seed_change(session_factory, change)
    revision = RunStore.stage_input_revision(change)
    run_id = _create_run(session_factory, change)
    key = operation_key(run_id, Stage.SPECIFICATION, revision)

    blocked = _advance(session_factory, change, run_id, executor=_blocking_executor)
    assert blocked.outcome is RunAdvanceOutcome.BLOCKED

    retried = _advance(session_factory, change, run_id, executor=_advancing_executor)

    # FAILED/BLOCKED -> IN_PROGRESS: the repeat is a new physical attempt of the
    # same logical operation (ADR-006 p.7) — one operation row, two attempts.
    assert retried.outcome is RunAdvanceOutcome.ADVANCED
    assert retried.result.attempt_number == 2
    with session_scope(session_factory) as session:
        stage_row = session.execute(
            select(StageRow).where(StageRow.operation_key == key)
        ).scalar_one()
        assert stage_row.attempt_count == 2
        assert stage_row.status == StageStatus.SUCCEEDED.value
        first_attempt = session.get(Attempt, f"{key}:1")
        assert first_attempt is not None
        assert first_attempt.status == StageStatus.BLOCKED.value
        assert first_attempt.finished_at is not None
        second_attempt = session.get(Attempt, f"{key}:2")
        assert second_attempt is not None
        assert second_attempt.status == StageStatus.SUCCEEDED.value
        assert session.get(StageResultRow, f"{key}:1") is not None
        assert session.get(StageResultRow, f"{key}:2") is not None
        # The successor stage of the retried advance is its own operation.
        assert len(list(session.execute(select(StageRow)).scalars())) == 2
        assert len(list(session.execute(select(OutboxEvent)).scalars())) == 2
        assert session.get(ExecutionLease, ("execution", run_id)) is None


def test_a_live_lease_blocks_a_second_advance(
    session_factory: sessionmaker[Session],
) -> None:
    change = make_change()
    _seed_change(session_factory, change)
    run_id = _create_run(session_factory, change)

    with session_scope(session_factory) as session:
        RunStore(session).acquire_lease(
            run_id=run_id, owner_id="other-owner", ttl=DEFAULT_LEASE_TTL
        )

    # The lease is the mutual exclusion of one run (ADR-006 p.6): while another
    # owner holds it the advance refuses and writes nothing, and the attempt row
    # stays the durable guard against a second execution.
    with pytest.raises(LeaseLostError):
        _advance(session_factory, change, run_id)

    with session_scope(session_factory) as session:
        assert list(session.execute(select(Attempt)).scalars()) == []
        assert list(session.execute(select(StageResultRow)).scalars()) == []


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


def test_update_status_refuses_a_transition_outside_the_domain_table(
    session_factory: sessionmaker[Session],
) -> None:
    change = make_change()
    _seed_change(session_factory, change)
    run_id = _create_run(session_factory, change)

    with session_scope(session_factory) as session:
        store = RunStore(session)
        token = store.acquire_lease(run_id=run_id, owner_id="test-owner", ttl=DEFAULT_LEASE_TTL)
        repository = ExecutionRepository(session)
        # pending -> canceled is a legal edge of the domain table.
        repository.update_status(
            run_id, RunStatus.CANCELED, expected_revision=1, fencing_token=token
        )
        with pytest.raises(InvalidStatusTransition):
            # canceled is terminal: the table has no outgoing edge for it at all,
            # so the persisted status can never leave the domain table
            # (ADR-024, условие 3).
            repository.update_status(
                run_id, RunStatus.RUNNING, expected_revision=2, fencing_token=token
            )

    # The refused transition wrote nothing: the run stays where the last legal
    # edge left it.
    with session_scope(session_factory) as session:
        execution = session.get(Execution, run_id)
        assert execution is not None
        assert execution.status == RunStatus.CANCELED.value
        assert execution.state_revision == 2


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


def test_the_cli_created_run_keys_its_first_stage_by_the_resolver_revision(
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # T-043: with a composition-root resolver the run created from --change-id
    # keys its first stage by the SCM revision (ADR-006 p.4), not by the change
    # snapshot's digest — a digest is not a git object and blocked the agent
    # executor at the workspace boundary on every attempt.
    monkeypatch.setenv("DATABASE_URL", os.environ[TEST_DATABASE_URL_ENV])
    change = make_change()
    _seed_change(session_factory, change)

    def revision_of(change: Change, stage: Stage) -> str:
        return "scm-rev-1"

    assert (
        run_advance_command(
            RunAdvanceArgs(change_id=change.id, run_id=None, json_output=True),
            session_factory=session_factory,
            owner_id="test-owner",
            revision_of=revision_of,
        )
        == EXIT_WAITING
    )
    run_id = json.loads(capsys.readouterr().out)["run_id"]

    # The stage operation — its row, its attempt and its committed result — is
    # keyed by the SCM revision; the run id itself stays snapshot-derived.
    key = operation_key(run_id, Stage.SPECIFICATION, "scm-rev-1")
    with session_scope(session_factory) as session:
        stage_row = session.get(StageRow, key)
        assert stage_row is not None
        assert stage_row.input_revision == "scm-rev-1"
        assert session.get(Attempt, f"{key}:1") is not None
        result = session.get(StageResultRow, f"{key}:1")
        assert result is not None
        assert result.input_revision == "scm-rev-1"


def _execution_columns(engine: Engine) -> set[str]:
    return {column["name"] for column in inspect(engine).get_columns("execution")}


# --- implementation contract (T-016, ADR-018 p.3) ---------------------------


def test_attach_contract_is_idempotent_and_swap_free(
    session_factory: sessionmaker[Session],
) -> None:
    change = make_change()
    _seed_change(session_factory, change)
    contract = make_contract()
    other = make_contract().model_copy(update={"id": "ict-other"})
    run_id = ""

    with session_scope(session_factory) as session:
        store = RunStore(session)
        run = store.create_run(
            change_id=change.id,
            route=Route.STANDARD,
            provider=change.product.provider,
            budget=BudgetSnapshot(),
            input_revision="rev-1",
        )
        assert run.implementation_contract is None
        run_id = run.id

        # A run without a contract records the given one; the identical contract
        # is a no-op; a different one is refused and the stored boundary survives.
        assert store.attach_contract(run.id, contract).implementation_contract == contract
        assert store.attach_contract(run.id, contract).implementation_contract == contract
        with pytest.raises(ContractConflictError):
            store.attach_contract(run.id, other)
        reloaded = store.load(run.id)
        assert reloaded is not None and reloaded.implementation_contract == contract

    # The attachment is durable: a fresh session reads the contract back.
    with session_scope(session_factory) as session:
        persisted = RunStore(session).load(run_id)
        assert persisted is not None and persisted.implementation_contract == contract


def test_the_cli_attaches_an_approved_contract_to_an_existing_run(
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    # The pilot situation: the runs were created without a contract (the CLI had
    # no way to pass one), so the advance that carries --contract-json attaches
    # it inside the same transaction and the run reads it back.
    monkeypatch.setenv("DATABASE_URL", os.environ[TEST_DATABASE_URL_ENV])
    change = make_change()
    _seed_change(session_factory, change)
    with session_scope(session_factory) as session:
        run_id = (
            RunStore(session)
            .create_run(
                change_id=change.id,
                route=Route.STANDARD,
                provider=change.product.provider,
                budget=BudgetSnapshot(),
                input_revision="rev-1",
            )
            .id
        )
    contract = make_contract()
    path = tmp_path / "contract.json"
    path.write_text(contract.model_dump_json(), encoding="utf-8")

    code = run_advance_command(
        RunAdvanceArgs(change_id=None, run_id=run_id, json_output=True, contract_json=str(path)),
        session_factory=session_factory,
        owner_id="test-owner",
    )
    assert code == EXIT_WAITING
    capsys.readouterr()

    # ``run status`` reads the attached contract back from the store.
    assert main(["run", "status", "--run-id", run_id, "--json"]) == EXIT_OK
    record = json.loads(capsys.readouterr().out)
    assert record["implementation_contract"] == json.loads(contract.model_dump_json())
