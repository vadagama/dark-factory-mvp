"""Integration tests of the Reconciler pass against PostgreSQL (T-063, ADR-006).

Requires ``DARK_FACTORY_TEST_DATABASE_URL`` (the shared fixtures build the
schema through the real Alembic migration); skipped without it. Every pass
runs against a truncated schema, so tests seed execution and lease rows
directly to control the observed data deterministically — the repository
invariants themselves are covered by the state-store suite (T-006).
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from dark_factory.adapters.fakes.workflow import FakeWorkflowEngine
from dark_factory.changes.enums import (
    ChangeRequestStatus,
    DecisionOutcome,
    DecisionSource,
    Gate,
    GateStatus,
    Provider,
    RiskClass,
    Route,
    RunStatus,
    Stage,
    StageStatus,
)
from dark_factory.changes.findings import Decision, GateResult
from dark_factory.cli.main import EXIT_OK, ReconcileArgs
from dark_factory.cli.reconcile import run_reconcile_command
from dark_factory.orchestration.policy.merge import (
    DEFAULT_MERGE_POLICY,
    MergePolicy,
    MergeRequestContext,
)
from dark_factory.orchestration.reconcile.models import (
    AnomalyKind,
    ReconcileActionKind,
    ReconcileReport,
)
from dark_factory.orchestration.reconcile.rules import ChangeRequestFacts, ExternalFacts
from dark_factory.orchestration.reconcile.service import GlobalReconciler
from dark_factory.orchestration.state.engine import session_scope
from dark_factory.orchestration.state.models import Execution
from dark_factory.orchestration.state.repositories import (
    ExecutionRepository,
    LeaseLostError,
    LeaseRepository,
)

HEAD = "abc123def"

AUTO_MERGE_POLICY = MergePolicy(auto_merge_risk_classes=frozenset({RiskClass.R0}))


class _StubObserver:
    """Observer seam with canned facts; records the observed run ids."""

    def __init__(self, facts_by_run: dict[str, ExternalFacts]) -> None:
        self._facts_by_run = facts_by_run
        self.observed: list[str] = []

    async def observe_external(self, *, run_id: str, change_id: str) -> ExternalFacts | None:
        self.observed.append(run_id)
        return self._facts_by_run.get(run_id)


def _run_pass(factory: sessionmaker[Session], **kwargs: Any) -> ReconcileReport:
    options: dict[str, Any] = {"owner_id": "reconciler-test"}
    options.update(kwargs)
    return asyncio.run(GlobalReconciler(factory, **options).run_pass())


def _seed_run(
    factory: sessionmaker[Session], execution_id: str, change_id: str, status: RunStatus
) -> None:
    """Create the execution row with the given status and no lease rows.

    The status is set directly so each test controls the lease state
    independently of the status history.
    """
    with session_scope(factory) as session:
        ExecutionRepository(session).create(
            execution_id=execution_id,
            change_id=change_id,
            route=Route.QUICK,
            provider=Provider.GITHUB,
        )
        row = session.get(Execution, execution_id)
        assert row is not None
        row.status = status.value
        row.state_revision = 2


def _seed_lease(
    factory: sessionmaker[Session], execution_id: str, *, owner: str, expired: bool
) -> None:
    ttl = -timedelta(minutes=1) if expired else timedelta(minutes=5)
    with session_scope(factory) as session:
        LeaseRepository(session).acquire(
            resource_type="execution", resource_id=execution_id, owner_id=owner, ttl=ttl
        )


def _seed_repeated_error(factory: sessionmaker[Session], execution_id: str) -> None:
    with session_scope(factory) as session:
        repo = ExecutionRepository(session)
        stage_row = repo.get_or_create_stage(
            execution_id=execution_id, stage=Stage.CONSTRUCTION, input_revision="rev-1"
        )
        for number in (1, 2):
            attempt = repo.append_attempt(stage_row, attempt_number=number)
            attempt.status = StageStatus.FAILED.value


def _execution(factory: sessionmaker[Session], execution_id: str) -> tuple[RunStatus, int, Any]:
    with session_scope(factory) as session:
        row = session.get(Execution, execution_id)
        assert row is not None
        return RunStatus(row.status), row.state_revision, row.finished_at


def _lease(factory: sessionmaker[Session], execution_id: str) -> tuple[str, int]:
    with session_scope(factory) as session:
        lease = LeaseRepository(session).get("execution", execution_id)
        assert lease is not None
        return lease.owner_id, lease.fencing_token


def _merge_facts() -> ExternalFacts:
    """Approved change request with the release gate passed at the head SHA."""
    return ExternalFacts(
        change_request=ChangeRequestFacts(status=ChangeRequestStatus.OPEN),
        merge=MergeRequestContext(
            executor="human",
            risk_class=RiskClass.R0,
            route=Route.QUICK,
            stage=Stage.RELEASE,
            expected_sha=HEAD,
            head_sha=HEAD,
            human_approvals=(
                Decision(
                    id="d-1",
                    gate=Gate.REVIEW,
                    outcome=DecisionOutcome.APPROVED,
                    decided_by=DecisionSource.HUMAN,
                    decided_at=datetime(2026, 9, 14, tzinfo=UTC),
                    commit_sha=HEAD,
                ),
            ),
            gate_results=(GateResult(gate=Gate.RELEASE, status=GateStatus.PASSED, sha=HEAD),),
        ),
    )


def test_expired_lease_is_taken_over_with_a_fresh_fencing_token(
    session_factory: sessionmaker[Session],
) -> None:
    _seed_run(session_factory, "run-0001", "chg-1", RunStatus.RUNNING)
    _seed_lease(session_factory, "run-0001", owner="agent-1", expired=True)

    report = _run_pass(session_factory)

    assert report.lease_acquired is True
    assert report.scanned == 1
    entry = report.entries[0]
    assert entry.action is not None
    assert entry.action.kind is ReconcileActionKind.LEASE_TAKEOVER
    assert entry.action.anomaly is AnomalyKind.EXPIRED_LEASE
    assert entry.applied is True
    assert entry.in_sync is None  # no engine wired: nothing was observed
    assert _lease(session_factory, "run-0001") == ("reconciler-test", 2)
    status, revision, finished_at = _execution(session_factory, "run-0001")
    assert status is RunStatus.RUNNING  # the takeover repairs the lease, not the status
    assert revision == 2
    assert finished_at is None


def test_duplicate_active_runs_are_superseded(session_factory: sessionmaker[Session]) -> None:
    _seed_run(session_factory, "run-0002", "chg-1", RunStatus.RUNNING)
    _seed_run(session_factory, "run-0001", "chg-1", RunStatus.RUNNING)

    report = _run_pass(session_factory)

    by_run = {entry.run_id: entry for entry in report.entries}
    duplicate = by_run["run-0002"]
    canonical = by_run["run-0001"]
    assert duplicate.action is not None
    assert duplicate.action.kind is ReconcileActionKind.SUPERSEDE
    assert duplicate.applied is True
    assert canonical.action is None
    assert _execution(session_factory, "run-0002")[0] is RunStatus.SUPERSEDED
    assert _execution(session_factory, "run-0001")[0] is RunStatus.RUNNING


def test_merged_change_request_is_recorded_without_a_completed_run(
    session_factory: sessionmaker[Session],
) -> None:
    _seed_run(session_factory, "run-0001", "chg-1", RunStatus.RUNNING)
    observer = _StubObserver(
        {
            "run-0001": ExternalFacts(
                change_request=ChangeRequestFacts(
                    status=ChangeRequestStatus.MERGED, merged_sha="deadbeef"
                )
            )
        }
    )

    report = _run_pass(session_factory, observer=observer)

    entry = report.entries[0]
    assert entry.action is not None
    assert entry.action.kind is ReconcileActionKind.RECORD_MERGE
    assert entry.action.merged_sha == "deadbeef"
    assert entry.applied is True
    status, revision, finished_at = _execution(session_factory, "run-0001")
    assert status is RunStatus.SUCCEEDED
    assert revision == 3
    assert finished_at is not None
    assert observer.observed == ["run-0001"]


def test_repeated_error_escalates_to_blocked(session_factory: sessionmaker[Session]) -> None:
    _seed_run(session_factory, "run-0001", "chg-1", RunStatus.RUNNING)
    _seed_repeated_error(session_factory, "run-0001")
    _seed_lease(session_factory, "run-0001", owner="agent-1", expired=True)

    report = _run_pass(session_factory)

    entry = report.entries[0]
    assert entry.action is not None
    assert entry.action.kind is ReconcileActionKind.ESCALATE
    assert entry.action.anomaly is AnomalyKind.REPEATED_ERROR
    assert entry.applied is True
    assert _execution(session_factory, "run-0001")[0] is RunStatus.BLOCKED


def test_a_repeated_pass_on_the_same_data_changes_nothing(
    session_factory: sessionmaker[Session],
) -> None:
    _seed_run(session_factory, "run-0001", "chg-1", RunStatus.RUNNING)
    _seed_lease(session_factory, "run-0001", owner="agent-1", expired=True)
    _seed_run(session_factory, "run-0002", "chg-2", RunStatus.RUNNING)
    _seed_run(session_factory, "run-0002-dup", "chg-2", RunStatus.RUNNING)
    _seed_run(session_factory, "run-0003", "chg-3", RunStatus.RUNNING)
    _seed_repeated_error(session_factory, "run-0003")

    first = _run_pass(session_factory)

    assert [entry.run_id for entry in first.entries] == [
        "run-0001",
        "run-0002",
        "run-0002-dup",
        "run-0003",
    ]
    assert [entry.applied for entry in first.entries] == [True, False, True, True]
    after_first = {
        entry.run_id: _execution(session_factory, entry.run_id) for entry in first.entries
    }

    second = _run_pass(session_factory)

    assert second.lease_acquired is True
    # The superseded duplicate is terminal and leaves the scan set.
    assert [entry.run_id for entry in second.entries] == ["run-0001", "run-0002", "run-0003"]
    assert all(entry.applied is False for entry in second.entries)
    assert all(entry.action is None for entry in second.entries)
    # Every run visible to the first pass — including the superseded one — is unchanged.
    after_second = {
        entry.run_id: _execution(session_factory, entry.run_id) for entry in first.entries
    }
    assert after_second == after_first


def test_a_parallel_reconciler_is_skipped_by_the_global_lease(
    session_factory: sessionmaker[Session],
) -> None:
    _seed_run(session_factory, "run-0001", "chg-1", RunStatus.RUNNING)
    with session_scope(session_factory) as session:
        LeaseRepository(session).acquire(
            resource_type="reconciler",
            resource_id="global",
            owner_id="another-reconciler",
            ttl=timedelta(minutes=5),
        )

    report = _run_pass(session_factory, owner_id="second-reconciler")

    assert report.lease_acquired is False
    assert report.scanned == 0
    assert report.entries == ()
    assert _execution(session_factory, "run-0001")[0] is RunStatus.RUNNING


def test_a_live_execution_lease_is_reported_not_applied(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_run(session_factory, "run-0001", "chg-1", RunStatus.RUNNING)
    _seed_lease(session_factory, "run-0001", owner="agent-1", expired=True)
    real_acquire = LeaseRepository.acquire

    def _unstealable_acquire(self, *, resource_type, resource_id, owner_id, ttl):
        if resource_type == "execution":
            raise LeaseLostError(f"lease {resource_type}/{resource_id} is held by another owner")
        return real_acquire(
            self, resource_type=resource_type, resource_id=resource_id, owner_id=owner_id, ttl=ttl
        )

    monkeypatch.setattr(LeaseRepository, "acquire", _unstealable_acquire)

    report = _run_pass(session_factory)

    entry = report.entries[0]
    assert entry.action is not None
    assert entry.action.kind is ReconcileActionKind.LEASE_TAKEOVER
    assert entry.applied is False
    assert entry.note is not None and "live execution lease" in entry.note
    assert _execution(session_factory, "run-0001")[0] is RunStatus.RUNNING
    assert _lease(session_factory, "run-0001") == ("agent-1", 1)


def test_ci_postgresql_drift_is_resolved_in_favour_of_postgresql(
    session_factory: sessionmaker[Session],
) -> None:
    engine = FakeWorkflowEngine()
    run_id = asyncio.run(engine.start(idempotency_key="chg-1"))
    engine.set_status(run_id, RunStatus.SUCCEEDED)  # the engine claims completion
    _seed_run(session_factory, run_id, "chg-1", RunStatus.RUNNING)

    report = _run_pass(session_factory, engine=engine)

    entry = report.entries[0]
    assert entry.desired_status is RunStatus.RUNNING
    assert entry.observed_status is RunStatus.SUCCEEDED
    assert entry.in_sync is False
    assert entry.action is None  # drift alone plans no recovery mutation
    # PostgreSQL stays authoritative; the pass does not "correct" the engine.
    assert _execution(session_factory, run_id)[0] is RunStatus.RUNNING


def test_approved_and_passed_without_merge_is_reported_not_executed(
    session_factory: sessionmaker[Session],
) -> None:
    _seed_run(session_factory, "run-0001", "chg-1", RunStatus.WAITING)
    _seed_lease(session_factory, "run-0001", owner="agent-1", expired=False)
    observer = _StubObserver({"run-0001": _merge_facts()})

    report = _run_pass(session_factory, observer=observer, merge_policy=AUTO_MERGE_POLICY)

    entry = report.entries[0]
    assert entry.action is not None
    assert entry.action.kind is ReconcileActionKind.PLAN_MERGE
    assert entry.applied is False
    assert entry.note is not None and "planned for its executor" in entry.note
    assert _execution(session_factory, "run-0001")[0] is RunStatus.WAITING


def test_manual_mode_reports_wait_for_human(session_factory: sessionmaker[Session]) -> None:
    _seed_run(session_factory, "run-0001", "chg-1", RunStatus.WAITING)
    _seed_lease(session_factory, "run-0001", owner="agent-1", expired=False)
    observer = _StubObserver({"run-0001": _merge_facts()})

    report = _run_pass(session_factory, observer=observer, merge_policy=DEFAULT_MERGE_POLICY)

    entry = report.entries[0]
    assert entry.action is not None
    assert entry.action.kind is ReconcileActionKind.WAIT_FOR_HUMAN
    assert entry.applied is False
    assert _execution(session_factory, "run-0001")[0] is RunStatus.WAITING


def test_record_merge_on_a_failed_run_is_left_to_the_next_pass(
    session_factory: sessionmaker[Session],
) -> None:
    _seed_run(session_factory, "run-0001", "chg-1", RunStatus.FAILED)
    observer = _StubObserver(
        {
            "run-0001": ExternalFacts(
                change_request=ChangeRequestFacts(
                    status=ChangeRequestStatus.MERGED, merged_sha="deadbeef"
                )
            )
        }
    )

    report = _run_pass(session_factory, observer=observer)

    entry = report.entries[0]
    assert entry.action is not None
    assert entry.action.kind is ReconcileActionKind.RECORD_MERGE
    assert entry.applied is False
    assert entry.note is not None and "not allowed" in entry.note
    assert _execution(session_factory, "run-0001")[0] is RunStatus.FAILED


def test_cli_reconcile_command_runs_one_idempotent_pass(
    session_factory: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    code = run_reconcile_command(
        ReconcileArgs(json_output=True), session_factory=session_factory, owner_id="cli-owner"
    )

    assert code == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["lease_acquired"] is True
    assert payload["owner_id"] == "cli-owner"
    assert payload["scanned"] == 0
    assert payload["entries"] == []


def test_cli_reconcile_text_report_lists_planned_actions(
    session_factory: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_run(session_factory, "run-0001", "chg-1", RunStatus.RUNNING)
    _seed_lease(session_factory, "run-0001", owner="agent-1", expired=True)

    code = run_reconcile_command(
        ReconcileArgs(json_output=False), session_factory=session_factory, owner_id="cli-owner"
    )

    assert code == EXIT_OK
    out = capsys.readouterr().out
    assert "run-0001 [running]: lease_takeover (expired_lease) applied=True" in out
