"""Unit tests of the Reconciler anomaly→action table and drift resolution (T-063, ADR-006).

Pure planning only: no database, no clock — ``now`` is injected and identical
facts plan identical actions (determinism, ADR-006 p.5). The PostgreSQL-backed
behaviour of a full pass lives in ``tests/integration/test_reconcile.py``.
"""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from dark_factory.adapters.fakes.workflow import FakeWorkflowEngine
from dark_factory.changes.enums import (
    ChangeRequestStatus,
    DecisionOutcome,
    DecisionSource,
    Gate,
    GateStatus,
    RiskClass,
    Route,
    RunStatus,
    Stage,
)
from dark_factory.changes.findings import Decision, GateResult
from dark_factory.orchestration.policy.merge import (
    DEFAULT_MERGE_POLICY,
    MergePolicy,
    MergeRequestContext,
)
from dark_factory.orchestration.reconcile.models import AnomalyKind, ReconcileActionKind
from dark_factory.orchestration.reconcile.rules import (
    ANOMALY_RULES,
    BranchFacts,
    ChangeRequestFacts,
    LeaseFacts,
    RunFacts,
    plan_action,
    resolve_drift,
)
from dark_factory.orchestration.reconcile.service import (
    PostgresReconciliationService,
    observe_statuses,
)
from dark_factory.ports import (
    ReconcileDesired,
    ReconcileObserved,
    ReconcileResult,
    ReconciliationService,
)

NOW = datetime(2026, 9, 14, 12, 0, 0, tzinfo=UTC)
HEAD = "abc123def"

AUTO_MERGE_POLICY = MergePolicy(auto_merge_risk_classes=frozenset({RiskClass.R0}))
"""Policy that authorizes the trusted finalizer for R0 changes (T-026)."""

_BASE_FACTS = RunFacts(
    run_id="run-0002",
    change_id="chg-1",
    desired_status=RunStatus.RUNNING,
    desired_revision=3,
)


def facts(**overrides: Any) -> RunFacts:
    """A healthy running run; keyword overrides replace single fields."""
    return replace(_BASE_FACTS, **overrides)


def lease_facts(*, expires_at: datetime | None = None, **overrides: Any) -> LeaseFacts:
    """A live lease of some agent, expiring five minutes after ``NOW`` by default."""
    values: dict[str, Any] = {
        "owner_id": "agent-1",
        "fencing_token": 1,
        "expires_at": NOW + timedelta(minutes=5) if expires_at is None else expires_at,
    }
    values.update(overrides)
    return LeaseFacts(**values)


def merge_context() -> MergeRequestContext:
    """An approved change request with the release gate passed at the head SHA."""
    return MergeRequestContext(
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
                decided_at=NOW,
                commit_sha=HEAD,
            ),
        ),
        gate_results=(GateResult(gate=Gate.RELEASE, status=GateStatus.PASSED, sha=HEAD),),
    )


def expired_lease(**overrides: Any) -> LeaseFacts:
    return lease_facts(expires_at=NOW - timedelta(seconds=1), **overrides)


def test_anomaly_rules_table_lists_all_six_rules_in_fixed_order() -> None:
    assert [rule.anomaly for rule in ANOMALY_RULES] == [
        AnomalyKind.REPEATED_ERROR,
        AnomalyKind.EXPIRED_LEASE,
        AnomalyKind.DUPLICATE_RUNS,
        AnomalyKind.MERGED_CR_WITHOUT_RUN,
        AnomalyKind.APPROVED_PASSED_WITHOUT_MERGE,
        AnomalyKind.BRANCH_BEHIND,
    ]


@pytest.mark.parametrize(
    ("anomaly", "overrides", "expected_kind"),
    [
        pytest.param(
            AnomalyKind.REPEATED_ERROR,
            {"repeated_error": True, "repeated_error_stage": "construction"},
            ReconcileActionKind.ESCALATE,
            id="repeated_error-escalate",
        ),
        pytest.param(
            AnomalyKind.EXPIRED_LEASE,
            {"lease": expired_lease()},
            ReconcileActionKind.LEASE_TAKEOVER,
            id="expired_lease-lease_takeover",
        ),
        pytest.param(
            AnomalyKind.DUPLICATE_RUNS,
            {"active_sibling_ids": ("run-0001",)},
            ReconcileActionKind.SUPERSEDE,
            id="duplicate_runs-supersede",
        ),
        pytest.param(
            AnomalyKind.MERGED_CR_WITHOUT_RUN,
            {
                "change_request": ChangeRequestFacts(
                    status=ChangeRequestStatus.MERGED, merged_sha="deadbeef"
                )
            },
            ReconcileActionKind.RECORD_MERGE,
            id="merged_cr_without_run-record_merge",
        ),
        pytest.param(
            AnomalyKind.APPROVED_PASSED_WITHOUT_MERGE,
            {
                "change_request": ChangeRequestFacts(status=ChangeRequestStatus.OPEN),
                "merge": merge_context(),
            },
            ReconcileActionKind.PLAN_MERGE,
            id="approved_passed_without_merge-plan_merge",
        ),
        pytest.param(
            AnomalyKind.BRANCH_BEHIND,
            {"branch": BranchFacts(head_sha="head-1", base_sha="base-0", behind_count=2)},
            ReconcileActionKind.UPDATE_BRANCH,
            id="branch_behind-update_branch",
        ),
    ],
)
def test_anomaly_rule_table_plans_one_action_per_row(
    anomaly: AnomalyKind, overrides: dict[str, Any], expected_kind: ReconcileActionKind
) -> None:
    action = plan_action(facts(**overrides), now=NOW, policy=AUTO_MERGE_POLICY)

    assert action is not None
    assert action.anomaly is anomaly
    assert action.kind is expected_kind
    assert action.run_id == "run-0002"
    assert action.reason


def test_repeated_error_wins_over_expired_lease() -> None:
    action = plan_action(
        facts(repeated_error=True, repeated_error_stage="construction", lease=expired_lease()),
        now=NOW,
    )

    assert action is not None
    assert action.kind is ReconcileActionKind.ESCALATE


def test_expired_lease_wins_over_duplicate_runs() -> None:
    action = plan_action(
        facts(lease=expired_lease(), active_sibling_ids=("run-0001",)),
        now=NOW,
    )

    assert action is not None
    assert action.kind is ReconcileActionKind.LEASE_TAKEOVER


def test_merged_change_request_wins_over_approved_passed_and_branch_behind() -> None:
    action = plan_action(
        facts(
            change_request=ChangeRequestFacts(
                status=ChangeRequestStatus.MERGED, merged_sha="deadbeef"
            ),
            merge=merge_context(),
            branch=BranchFacts(head_sha="head-1", base_sha="base-0", behind_count=1),
        ),
        now=NOW,
        policy=AUTO_MERGE_POLICY,
    )

    assert action is not None
    assert action.kind is ReconcileActionKind.RECORD_MERGE
    assert action.merged_sha == "deadbeef"


def test_repeated_error_reason_names_the_stage() -> None:
    action = plan_action(facts(repeated_error=True, repeated_error_stage="construction"), now=NOW)

    assert action is not None
    assert "construction" in action.reason


def test_expired_lease_reason_carries_owner_and_token() -> None:
    action = plan_action(facts(lease=expired_lease()), now=NOW)

    assert action is not None
    assert "agent-1" in action.reason
    assert "fencing token 1" in action.reason


def test_approved_passed_plans_the_merge_for_the_trusted_finalizer() -> None:
    action = plan_action(
        facts(
            change_request=ChangeRequestFacts(status=ChangeRequestStatus.OPEN),
            merge=merge_context(),
        ),
        now=NOW,
        policy=AUTO_MERGE_POLICY,
    )

    assert action is not None
    assert action.kind is ReconcileActionKind.PLAN_MERGE
    assert action.merge_method == "squash"
    assert "trusted finalizer" in action.reason


def test_manual_mode_waits_for_the_human_instead_of_merging() -> None:
    action = plan_action(
        facts(
            change_request=ChangeRequestFacts(status=ChangeRequestStatus.OPEN),
            merge=merge_context(),
        ),
        now=NOW,
        policy=DEFAULT_MERGE_POLICY,
    )

    assert action is not None
    assert action.kind is ReconcileActionKind.WAIT_FOR_HUMAN


def test_sha_drift_blocks_the_merge_and_escalates() -> None:
    drifted = replace(merge_context(), head_sha="new-head")

    action = plan_action(
        facts(
            change_request=ChangeRequestFacts(status=ChangeRequestStatus.OPEN),
            merge=drifted,
        ),
        now=NOW,
        policy=AUTO_MERGE_POLICY,
    )

    assert action is not None
    assert action.kind is ReconcileActionKind.ESCALATE


def test_live_lease_is_not_taken_over() -> None:
    assert plan_action(facts(lease=lease_facts()), now=NOW) is None


def test_canonical_duplicate_run_is_kept() -> None:
    # run-0001 is the lexicographic minimum among the active runs of the change:
    # it survives, its sibling is superseded instead.
    action = plan_action(facts(run_id="run-0001", active_sibling_ids=("run-0002",)), now=NOW)

    assert action is None


def test_merged_change_request_of_a_succeeded_run_is_not_recorded_twice() -> None:
    merged = ChangeRequestFacts(status=ChangeRequestStatus.MERGED, merged_sha="deadbeef")

    assert (
        plan_action(facts(desired_status=RunStatus.SUCCEEDED, change_request=merged), now=NOW)
        is None
    )


def test_approved_passed_without_merge_context_plans_nothing() -> None:
    open_request = ChangeRequestFacts(status=ChangeRequestStatus.OPEN)

    assert (
        plan_action(facts(change_request=open_request), now=NOW, policy=AUTO_MERGE_POLICY) is None
    )


def test_approved_passed_only_fires_for_active_runs() -> None:
    pending = facts(
        desired_status=RunStatus.PENDING,
        change_request=ChangeRequestFacts(status=ChangeRequestStatus.OPEN),
        merge=merge_context(),
    )

    assert plan_action(pending, now=NOW, policy=AUTO_MERGE_POLICY) is None


def test_branch_up_to_date_needs_no_update() -> None:
    current = BranchFacts(head_sha="head-1", base_sha="base-0", behind_count=0)

    assert plan_action(facts(branch=current), now=NOW) is None


def test_repeated_error_of_a_blocked_run_is_already_escalated() -> None:
    blocked = facts(
        desired_status=RunStatus.BLOCKED,
        repeated_error=True,
        repeated_error_stage="construction",
    )

    assert plan_action(blocked, now=NOW) is None


def test_terminal_runs_never_receive_recovery_actions() -> None:
    succeeded = facts(desired_status=RunStatus.SUCCEEDED, lease=expired_lease())
    superseded = facts(desired_status=RunStatus.SUPERSEDED, lease=expired_lease())

    assert plan_action(succeeded, now=NOW) is None
    assert plan_action(superseded, now=NOW) is None


def test_healthy_run_plans_nothing() -> None:
    assert plan_action(facts(), now=NOW) is None


def test_planning_is_deterministic() -> None:
    values = facts(lease=expired_lease(), active_sibling_ids=("run-0001",))

    assert plan_action(values, now=NOW) == plan_action(values, now=NOW)


def test_takeover_converges_the_next_pass_plans_nothing_again() -> None:
    first = plan_action(facts(lease=expired_lease()), now=NOW)

    assert first is not None
    assert first.kind is ReconcileActionKind.LEASE_TAKEOVER
    # After the takeover the lease is live again with a fresh fencing token.
    taken_over = facts(lease=lease_facts(owner_id="reconciler-1", fencing_token=2))

    assert plan_action(taken_over, now=NOW) is None


def test_supersede_converges_the_next_pass_plans_nothing_again() -> None:
    first = plan_action(facts(active_sibling_ids=("run-0001",)), now=NOW)

    assert first is not None
    assert first.kind is ReconcileActionKind.SUPERSEDE
    # The sibling was closed: no active sibling remains.
    assert plan_action(facts(), now=NOW) is None


def test_resolve_drift_reports_in_sync_when_states_match() -> None:
    result = resolve_drift(
        ReconcileDesired(run_id="run-001", status=RunStatus.RUNNING, state_revision=3),
        ReconcileObserved(run_id="run-001", status=RunStatus.RUNNING),
    )

    assert result == ReconcileResult(
        run_id="run-001", in_sync=True, status=RunStatus.RUNNING, state_revision=3
    )


def test_resolve_drift_decides_in_favour_of_postgresql() -> None:
    result = resolve_drift(
        ReconcileDesired(run_id="run-001", status=RunStatus.RUNNING, state_revision=5),
        ReconcileObserved(run_id="run-001", status=RunStatus.CANCELED, state_revision=7),
    )

    assert result.in_sync is False
    assert result.status is RunStatus.RUNNING
    assert result.state_revision == 5


def test_resolve_drift_rejects_a_scope_mismatch() -> None:
    with pytest.raises(ValueError, match="scope mismatch"):
        resolve_drift(
            ReconcileDesired(run_id="run-001", status=RunStatus.RUNNING, state_revision=1),
            ReconcileObserved(run_id="run-002", status=RunStatus.RUNNING),
        )


def test_observe_statuses_maps_known_runs_and_skips_unknown_ones() -> None:
    engine = FakeWorkflowEngine()
    run_id = asyncio.run(engine.start(idempotency_key="k-1"))
    engine.set_status(run_id, RunStatus.WAITING)

    observed = asyncio.run(observe_statuses(engine, [run_id, "run-unknown"]))

    assert observed == {run_id: RunStatus.WAITING}


def test_observe_statuses_without_an_engine_observes_nothing() -> None:
    assert asyncio.run(observe_statuses(None, ["run-0001"])) == {}


def test_postgres_reconciliation_service_satisfies_the_port_and_prefers_postgresql() -> None:
    service = PostgresReconciliationService()

    assert isinstance(service, ReconciliationService)
    result = asyncio.run(
        service.reconcile(
            desired=ReconcileDesired(run_id="run-001", status=RunStatus.WAITING, state_revision=4),
            observed=ReconcileObserved(run_id="run-001", status=RunStatus.RUNNING),
        )
    )

    assert result == ReconcileResult(
        run_id="run-001", in_sync=False, status=RunStatus.WAITING, state_revision=4
    )
