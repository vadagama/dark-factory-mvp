"""Schema invariants and crash semantics of the operational state (T-006)."""

from datetime import timedelta

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from dark_factory.changes import Provider, Route, RunStatus, Stage
from dark_factory.changes.keys import effect_key
from dark_factory.orchestration.state import (
    EffectLedger,
    EffectLedgerEntry,
    EventDelivery,
    Execution,
    ExecutionRepository,
    LeaseLostError,
    LeaseRepository,
    OutboxEvent,
    OutboxRepository,
    StaleFencingTokenError,
    StateConflictError,
    ensure_effect,
    session_scope,
)
from dark_factory.orchestration.state.repositories import OutboxEventDraft

LONG = timedelta(minutes=5)


def _seed_execution(
    session_factory: sessionmaker[Session],
    execution_id: str = "exec-1",
    *,
    route: Route = Route.STANDARD,
    provider: Provider = Provider.GITHUB,
) -> None:
    with session_scope(session_factory) as session:
        ExecutionRepository(session).create(
            execution_id=execution_id,
            change_id="chg-1",
            route=route,
            provider=provider,
        )


def test_operation_key_is_unique_per_input_revision(
    session_factory: sessionmaker[Session],
) -> None:
    _seed_execution(session_factory)
    with session_scope(session_factory) as session:
        repo = ExecutionRepository(session)
        first = repo.get_or_create_stage(
            execution_id="exec-1", stage=Stage.CONSTRUCTION, input_revision="rev-1"
        )
        again = repo.get_or_create_stage(
            execution_id="exec-1", stage=Stage.CONSTRUCTION, input_revision="rev-1"
        )
        assert first.id == again.id
        assert first.operation_key == again.operation_key

        other_revision = repo.get_or_create_stage(
            execution_id="exec-1", stage=Stage.CONSTRUCTION, input_revision="rev-2"
        )
        assert other_revision.operation_key != first.operation_key


def test_attempt_retry_is_idempotent(session_factory: sessionmaker[Session]) -> None:
    _seed_execution(session_factory)
    with session_scope(session_factory) as session:
        repo = ExecutionRepository(session)
        stage = repo.get_or_create_stage(
            execution_id="exec-1", stage=Stage.CONSTRUCTION, input_revision="rev-1"
        )
        first = repo.append_attempt(stage, attempt_number=1)
        again = repo.append_attempt(stage, attempt_number=1)
        assert first.id == again.id
        assert first.id == f"{stage.operation_key}:1"

        second = repo.append_attempt(stage, attempt_number=2)
        assert second.id != first.id
        assert stage.attempt_count == 2


def test_update_status_checks_revision_and_fencing(
    session_factory: sessionmaker[Session],
) -> None:
    _seed_execution(session_factory)
    with session_scope(session_factory) as session:
        token = LeaseRepository(session).acquire(
            resource_type="execution",
            resource_id="exec-1",
            owner_id="pod-a",
            ttl=LONG,
        )
    with session_scope(session_factory) as session:
        repo = ExecutionRepository(session)
        with pytest.raises(StaleFencingTokenError):
            repo.update_status(
                "exec-1", RunStatus.RUNNING, expected_revision=1, fencing_token=token + 1
            )
        with pytest.raises(StateConflictError):
            repo.update_status(
                "exec-1", RunStatus.RUNNING, expected_revision=42, fencing_token=token
            )
        updated = repo.update_status(
            "exec-1", RunStatus.RUNNING, expected_revision=1, fencing_token=token
        )
        assert updated.status == RunStatus.RUNNING.value
        assert updated.state_revision == 2


def test_lease_fencing_token_is_monotonic(
    session_factory: sessionmaker[Session],
) -> None:
    with session_scope(session_factory) as session:
        leases = LeaseRepository(session)
        first = leases.acquire(
            resource_type="execution", resource_id="exec-1", owner_id="pod-a", ttl=LONG
        )
        assert first == 1
        second = leases.acquire(
            resource_type="execution", resource_id="exec-1", owner_id="pod-a", ttl=LONG
        )
        assert second == 2

        # A live lease cannot be stolen by another owner.
        with pytest.raises(LeaseLostError):
            leases.acquire(
                resource_type="execution", resource_id="exec-1", owner_id="pod-b", ttl=LONG
            )
        # A stale token cannot renew the lease.
        with pytest.raises(LeaseLostError):
            leases.renew(
                resource_type="execution",
                resource_id="exec-1",
                owner_id="pod-a",
                fencing_token=first,
                ttl=LONG,
            )


def test_expired_lease_can_be_stolen_with_new_token(
    session_factory: sessionmaker[Session],
) -> None:
    with session_scope(session_factory) as session:
        leases = LeaseRepository(session)
        # A non-positive TTL yields an already-expired lease.
        leases.acquire(resource_type="execution", resource_id="exec-1", owner_id="pod-a", ttl=-LONG)
        stolen = leases.acquire(
            resource_type="execution", resource_id="exec-1", owner_id="pod-b", ttl=LONG
        )
        assert stolen == 2


def test_state_change_and_event_commit_atomically(
    session_factory: sessionmaker[Session],
) -> None:
    with session_scope(session_factory) as session:
        ExecutionRepository(session).create(
            execution_id="exec-1",
            change_id="chg-1",
            route=Route.STANDARD,
            provider=Provider.GITHUB,
        )
        OutboxRepository(session).publish(
            OutboxEventDraft(
                event_id="evt-1",
                event_type="run.started",
                change_id="chg-1",
                run_id="exec-1",
                aggregate_id="exec-1",
                aggregate_version=1,
            ),
            consumers=("tracker", "console"),
        )
    with session_scope(session_factory) as session:
        assert session.get(Execution, "exec-1") is not None
        assert session.get(OutboxEvent, "evt-1") is not None


def test_state_change_and_event_roll_back_together(
    session_factory: sessionmaker[Session],
) -> None:
    with pytest.raises(RuntimeError), session_scope(session_factory) as session:
        ExecutionRepository(session).create(
            execution_id="exec-x",
            change_id="chg-x",
            route=Route.QUICK,
            provider=Provider.GITHUB,
        )
        OutboxRepository(session).publish(
            OutboxEventDraft(
                event_id="evt-x",
                event_type="run.started",
                change_id="chg-x",
                run_id="exec-x",
                aggregate_id="exec-x",
                aggregate_version=1,
            ),
            consumers=("tracker",),
        )
        raise RuntimeError("simulated crash before commit")
    with session_scope(session_factory) as session:
        assert session.get(Execution, "exec-x") is None
        assert session.get(OutboxEvent, "evt-x") is None


def test_outbox_sequence_is_monotonic_per_aggregate(
    session_factory: sessionmaker[Session],
) -> None:
    with session_scope(session_factory) as session:
        outbox = OutboxRepository(session)
        first = outbox.publish(
            OutboxEventDraft(
                event_id="evt-1",
                event_type="run.started",
                change_id="chg-1",
                run_id="exec-1",
                aggregate_id="exec-1",
                aggregate_version=1,
            ),
            consumers=("tracker",),
        )
        second = outbox.publish(
            OutboxEventDraft(
                event_id="evt-2",
                event_type="run.stage_completed",
                change_id="chg-1",
                run_id="exec-1",
                aggregate_id="exec-1",
                aggregate_version=2,
            ),
            consumers=("tracker",),
        )
        assert (first.sequence, second.sequence) == (1, 2)


def test_effect_key_is_unique(session_factory: sessionmaker[Session]) -> None:
    with pytest.raises(IntegrityError), session_scope(session_factory) as session:
        EffectLedger(session).plan("eff-1")
        session.add(EffectLedgerEntry(effect_key="eff-1", status="planned"))


def test_event_delivery_pair_is_unique(session_factory: sessionmaker[Session]) -> None:
    with pytest.raises(IntegrityError), session_scope(session_factory) as session:
        OutboxRepository(session).publish(
            OutboxEventDraft(
                event_id="evt-1",
                event_type="run.started",
                change_id="chg-1",
                run_id="exec-1",
                aggregate_id="exec-1",
                aggregate_version=1,
            ),
            consumers=("tracker",),
        )
        session.add(EventDelivery(event_id="evt-1", consumer_id="tracker", status="pending"))


def test_ensure_effect_crash_before_call_retries_once(
    session_factory: sessionmaker[Session],
) -> None:
    external: dict[str, str] = {}
    key = effect_key("exec-1:construction:rev-1", "branch", "feature/x")

    # Crash after planning but before the external call: transaction rolls back.
    with pytest.raises(RuntimeError), session_scope(session_factory) as session:
        EffectLedger(session).plan(key)
        raise RuntimeError("simulated crash before call")
    assert external == {}

    calls: list[str] = []

    def call() -> str:
        calls.append(key)
        external[key] = "ref-created"
        return "ref-created"

    with session_scope(session_factory) as session:
        ref = ensure_effect(session, key, lookup_external=external.get, call=call)

    assert ref == "ref-created"
    assert calls == [key]
    with session_scope(session_factory) as session:
        entry = EffectLedger(session).lookup(key)
        assert entry is not None
        assert entry.status == "succeeded"
        assert entry.external_ref == "ref-created"


def test_ensure_effect_crash_after_call_does_not_duplicate(
    session_factory: sessionmaker[Session],
) -> None:
    external: dict[str, str] = {}
    key = effect_key("exec-1:construction:rev-1", "change_request", "feature/x")

    def make_call() -> None:
        external[key] = "cr-ref-1"

    # The external effect is created, then the process crashes before commit, so the
    # ledger row is lost.
    with pytest.raises(RuntimeError), session_scope(session_factory) as session:
        EffectLedger(session).plan(key)
        make_call()
        raise RuntimeError("simulated crash after call, before commit")
    assert external[key] == "cr-ref-1"

    calls: list[str] = []

    def call() -> str:  # pragma: no cover - must not run
        calls.append(key)
        return "duplicate"

    with session_scope(session_factory) as session:
        ref = ensure_effect(session, key, lookup_external=external.get, call=call)

    # The deterministic marker lookup found the existing effect; no second call.
    assert ref == "cr-ref-1"
    assert calls == []
    with session_scope(session_factory) as session:
        entry = EffectLedger(session).lookup(key)
        assert entry is not None
        assert entry.status == "succeeded"
        assert entry.external_ref == "cr-ref-1"


def test_ensure_effect_replay_returns_recorded_ref(
    session_factory: sessionmaker[Session],
) -> None:
    external: dict[str, str] = {}
    key = effect_key("exec-1:release:rev-1", "deployment", "apps-dev")
    calls: list[str] = []

    def call() -> str:
        calls.append(key)
        external[key] = "deploy-1"
        return "deploy-1"

    with session_scope(session_factory) as session:
        first = ensure_effect(session, key, lookup_external=external.get, call=call)
    with session_scope(session_factory) as session:
        second = ensure_effect(session, key, lookup_external=external.get, call=call)

    assert first == second == "deploy-1"
    assert calls == [key]
