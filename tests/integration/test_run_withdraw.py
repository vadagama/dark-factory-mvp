"""Operator withdrawal of a parked run against PostgreSQL (T064, TD-030, ADR-006).

The withdrawal is a terminal decision of the store, not a stage result: these
tests hold its row-level behaviour — a parked run and its non-terminal stages
become ``canceled``, a repeat is inert, a finished run is refused, the committed
history is not rewritten and the decision lands in the append-only audit log.
Skipped without ``DARK_FACTORY_TEST_DATABASE_URL`` like every PostgreSQL-backed
test (ADR-004).
"""

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session, sessionmaker

from dark_factory.changes.enums import Route, RunStatus, Stage, StageStatus, StopOutcome
from dark_factory.changes.next_action import StopAction
from dark_factory.changes.run import Change, ChangeRun, StageResult
from dark_factory.changes.usage import BudgetSnapshot
from dark_factory.orchestration.reconcile.models import ReconcileReport
from dark_factory.orchestration.reconcile.service import GlobalReconciler
from dark_factory.orchestration.runner import (
    RunAdvance,
    RunAdvanceOutcome,
    RunNotAdvanceableError,
    StageExecutor,
    advance_run,
    deterministic_stage_executor,
)
from dark_factory.orchestration.stages.context import StageContext
from dark_factory.orchestration.state.change_store import AuditRepository, ChangeRepository
from dark_factory.orchestration.state.engine import session_scope
from dark_factory.orchestration.state.models import AuditLogEntry, Execution
from dark_factory.orchestration.state.repositories import ExecutionRepository
from dark_factory.orchestration.state.run_store import (
    WITHDRAW_ACTION,
    RunNotWithdrawableError,
    RunStore,
    RunWithdrawal,
    UnknownRunError,
    WithdrawOutcome,
)
from dark_factory.orchestration.state.stage_results import StageResultRepository
from tests.changes_factories import make_change

NOW = datetime(2026, 9, 19, 12, 0, 0, tzinfo=UTC)
REVISION = "rev-1"
RUN_ID = "run_1"


def _blocking_executor(context: StageContext) -> StageResult:
    """Scripted executor that parks the attempt ``blocked`` — a run a human must decide."""
    return StageResult(
        stage=context.stage,
        run_id=context.run_id,
        change_id=context.change.id,
        attempt_number=context.attempt_number,
        input_revision=context.input_revision,
        status=StageStatus.BLOCKED,
        next_action=StopAction(outcome=StopOutcome.BLOCKED, reason="needs a human decision"),
        produced_at=NOW,
    )


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


def _seed_run(
    session_factory: sessionmaker[Session],
    change: Change,
    run_id: str,
    status: RunStatus,
    stages: Sequence[tuple[Stage, StageStatus]],
) -> None:
    """Seed a run and its stage rows directly, without driving the flow.

    The states the flow cannot be parked in on demand — a ``running`` run, an
    already finished one — are seeded as rows: the withdrawal only reads the
    persisted status and never re-runs a transition that produced it.
    """
    with session_scope(session_factory) as session:
        repository = ExecutionRepository(session)
        repository.create(
            execution_id=run_id,
            change_id=change.id,
            route=Route.STANDARD,
            provider=change.product.provider,
        )
        execution = session.get(Execution, run_id)
        assert execution is not None
        execution.status = status.value
        execution.state_revision = 2
        for stage, stage_status in stages:
            row = repository.get_or_create_stage(
                execution_id=run_id, stage=stage, input_revision=REVISION
            )
            row.status = stage_status.value
            row.state_revision = 2


def _withdraw(
    session_factory: sessionmaker[Session],
    run_id: str,
    *,
    actor: str = "alice",
    role: str | None = "operator",
    reason: str | None = None,
    idempotency_key: str | None = None,
) -> RunWithdrawal:
    with session_scope(session_factory) as session:
        return RunStore(session).withdraw_run(
            run_id,
            actor=actor,
            role=role,
            reason=reason,
            idempotency_key=idempotency_key,
            owner_id="test-owner",
            now=NOW,
        )


def _load(session_factory: sessionmaker[Session], run_id: str) -> ChangeRun | None:
    with session_scope(session_factory) as session:
        return RunStore(session).load(run_id)


def _audit_entries(session_factory: sessionmaker[Session], run_id: str) -> list[AuditLogEntry]:
    with session_scope(session_factory) as session:
        return AuditRepository(session).list_for("run", run_id)


def test_withdraw_cancels_a_waiting_run_and_its_stage(
    session_factory: sessionmaker[Session],
) -> None:
    change = make_change()
    _seed_change(session_factory, change)
    run_id = _create_run(session_factory, change)
    parked = _advance(session_factory, change, run_id)
    assert parked.outcome is RunAdvanceOutcome.WAITING

    withdrawal = _withdraw(session_factory, run_id, reason="mistakenly started")

    assert withdrawal.outcome is WithdrawOutcome.WITHDRAWN
    assert withdrawal.run.status is RunStatus.CANCELED
    assert withdrawal.run.finished_at is not None
    assert [stage.status for stage in withdrawal.run.stages] == [StageStatus.CANCELED]
    reconstructed = _load(session_factory, run_id)
    assert reconstructed is not None
    assert reconstructed.status is RunStatus.CANCELED
    assert reconstructed.finished_at is not None
    # The stage row is terminal now: nothing can move it forward any more.
    assert reconstructed.stages[0].status is StageStatus.CANCELED
    assert reconstructed.stages[0].finished_at is not None


def test_withdraw_cancels_a_blocked_run(session_factory: sessionmaker[Session]) -> None:
    change = make_change()
    _seed_change(session_factory, change)
    run_id = _create_run(session_factory, change)
    blocked = _advance(session_factory, change, run_id, executor=_blocking_executor)
    assert blocked.outcome is RunAdvanceOutcome.BLOCKED

    withdrawal = _withdraw(session_factory, run_id)

    assert withdrawal.outcome is WithdrawOutcome.WITHDRAWN
    assert withdrawal.run.status is RunStatus.CANCELED
    assert [stage.status for stage in withdrawal.run.stages] == [StageStatus.CANCELED]


@pytest.mark.parametrize(
    "stage_status",
    [
        StageStatus.PENDING,
        StageStatus.IN_PROGRESS,
        StageStatus.WAITING,
        StageStatus.BLOCKED,
        StageStatus.FAILED,
    ],
)
def test_withdraw_cancels_every_non_terminal_stage_row(
    session_factory: sessionmaker[Session], stage_status: StageStatus
) -> None:
    change = make_change()
    _seed_change(session_factory, change)
    _seed_run(
        session_factory, change, RUN_ID, RunStatus.RUNNING, [(Stage.SPECIFICATION, stage_status)]
    )

    withdrawal = _withdraw(session_factory, RUN_ID)

    assert withdrawal.outcome is WithdrawOutcome.WITHDRAWN
    assert withdrawal.run.status is RunStatus.CANCELED
    assert [stage.status for stage in withdrawal.run.stages] == [StageStatus.CANCELED]


@pytest.mark.parametrize(
    "stage_status",
    [
        StageStatus.SUCCEEDED,
        StageStatus.SKIPPED,
        StageStatus.SUPERSEDED,
        StageStatus.CANCELED,
    ],
)
def test_withdraw_keeps_terminal_stage_rows(
    session_factory: sessionmaker[Session], stage_status: StageStatus
) -> None:
    """A stage that already reached a terminal status is never rewritten by the run's ending."""
    change = make_change()
    _seed_change(session_factory, change)
    _seed_run(
        session_factory,
        change,
        RUN_ID,
        RunStatus.RUNNING,
        [(Stage.SPECIFICATION, stage_status), (Stage.PLANNING, StageStatus.WAITING)],
    )

    withdrawal = _withdraw(session_factory, RUN_ID)

    assert [stage.status for stage in withdrawal.run.stages] == [
        stage_status,
        StageStatus.CANCELED,
    ]


def test_withdraw_is_idempotent_and_does_not_move_the_revision(
    session_factory: sessionmaker[Session],
) -> None:
    change = make_change()
    _seed_change(session_factory, change)
    _seed_run(
        session_factory,
        change,
        RUN_ID,
        RunStatus.WAITING,
        [(Stage.SPECIFICATION, StageStatus.WAITING)],
    )

    first = _withdraw(session_factory, RUN_ID, reason="first")
    assert first.outcome is WithdrawOutcome.WITHDRAWN
    revision = first.run.state_revision
    finished_at = first.run.finished_at

    second = _withdraw(session_factory, RUN_ID, reason="second")

    assert second.outcome is WithdrawOutcome.REPLAYED
    assert second.run.status is RunStatus.CANCELED
    assert second.run.state_revision == revision
    assert second.run.finished_at == finished_at
    # Only the audit row of the repeat is written; the decision itself is unchanged.
    assert [entry.outcome for entry in _audit_entries(session_factory, RUN_ID)] == [
        "created",
        "replayed",
    ]


def test_withdraw_refuses_an_unknown_run(session_factory: sessionmaker[Session]) -> None:
    with pytest.raises(UnknownRunError):
        _withdraw(session_factory, "run-missing")

    assert _audit_entries(session_factory, "run-missing") == []


@pytest.mark.parametrize("status", [RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.SUPERSEDED])
def test_withdraw_refuses_a_finished_run_without_rewriting_it(
    session_factory: sessionmaker[Session], status: RunStatus
) -> None:
    """A finished run is neither resurrected nor reclassified: the withdrawal refuses."""
    change = make_change()
    _seed_change(session_factory, change)
    _seed_run(
        session_factory, change, RUN_ID, status, [(Stage.SPECIFICATION, StageStatus.SUCCEEDED)]
    )

    with pytest.raises(RunNotWithdrawableError):
        _withdraw(session_factory, RUN_ID)

    reconstructed = _load(session_factory, RUN_ID)
    assert reconstructed is not None
    assert reconstructed.status is status
    assert [stage.status for stage in reconstructed.stages] == [StageStatus.SUCCEEDED]
    # The refusal happens before any write — the audit log stays empty too.
    assert _audit_entries(session_factory, RUN_ID) == []


def test_withdraw_leaves_the_committed_history_untouched(
    session_factory: sessionmaker[Session],
) -> None:
    change = make_change()
    _seed_change(session_factory, change)
    run_id = _create_run(session_factory, change)
    parked = _advance(session_factory, change, run_id)

    _withdraw(session_factory, run_id)

    with session_scope(session_factory) as session:
        stored = StageResultRepository(session).list_for_run(run_id)
    # The committed result of the parked attempt is the history: it is not rewritten.
    assert stored == [parked.result]
    assert stored[0].status is StageStatus.WAITING


def test_withdraw_records_the_decision_in_the_append_only_audit_log(
    session_factory: sessionmaker[Session],
) -> None:
    change = make_change()
    _seed_change(session_factory, change)
    _seed_run(
        session_factory,
        change,
        RUN_ID,
        RunStatus.BLOCKED,
        [(Stage.SPECIFICATION, StageStatus.BLOCKED)],
    )

    _withdraw(
        session_factory,
        RUN_ID,
        actor="alice",
        role="operator",
        reason="invalid pilot run",
        idempotency_key="idem-1",
    )

    entries = _audit_entries(session_factory, RUN_ID)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.action == WITHDRAW_ACTION
    assert entry.resource_type == "run"
    assert entry.resource_id == RUN_ID
    assert entry.actor == "alice"
    assert entry.role == "operator"
    assert entry.outcome == "created"
    assert entry.idempotency_key == "idem-1"
    assert entry.details == {"reason": "invalid pilot run"}


def test_withdraw_without_a_reason_records_no_details(
    session_factory: sessionmaker[Session],
) -> None:
    change = make_change()
    _seed_change(session_factory, change)
    _seed_run(
        session_factory,
        change,
        RUN_ID,
        RunStatus.WAITING,
        [(Stage.SPECIFICATION, StageStatus.WAITING)],
    )

    _withdraw(session_factory, RUN_ID)

    assert _audit_entries(session_factory, RUN_ID)[0].details == {}


def test_a_withdrawn_run_is_no_longer_advanceable(
    session_factory: sessionmaker[Session],
) -> None:
    change = make_change()
    _seed_change(session_factory, change)
    run_id = _create_run(session_factory, change)
    _advance(session_factory, change, run_id)
    _withdraw(session_factory, run_id)

    with pytest.raises(RunNotAdvanceableError), session_scope(session_factory) as session:
        advance_run(
            store=RunStore(session),
            change=change,
            run_id=run_id,
            owner_id="test-owner",
            now=NOW,
        )


def test_a_withdrawn_run_is_not_resurrected_by_a_reconcile_pass(
    session_factory: sessionmaker[Session],
) -> None:
    """The Reconciler scans only non-terminal runs, so a withdrawal is final (T064, TD-030)."""
    change = make_change()
    _seed_change(session_factory, change)
    _seed_run(
        session_factory,
        change,
        RUN_ID,
        RunStatus.WAITING,
        [(Stage.SPECIFICATION, StageStatus.WAITING)],
    )
    _withdraw(session_factory, RUN_ID)

    report: ReconcileReport = asyncio.run(
        GlobalReconciler(session_factory, owner_id="reconciler-test").run_pass()
    )

    # A terminal run is not part of the scan set: no anomaly, no recovery action.
    assert report.scanned == 0
    assert not report.entries
    reconstructed = _load(session_factory, RUN_ID)
    assert reconstructed is not None
    assert reconstructed.status is RunStatus.CANCELED
