"""Budget coordinator: allowance precedence, reservations and the run aggregate (T-062).

The tests are deterministic: the reference time is always passed in (never the
wall clock), reservation ids derive from the caller's key, and nothing touches
a database, the network or a sleep.
"""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from dark_factory.api.aggregates import open_blocker_count
from dark_factory.changes.enums import (
    FindingOrigin,
    FindingSeverity,
    FindingStatus,
    Role,
    Route,
    RunStatus,
    Stage,
    StageStatus,
    StopOutcome,
)
from dark_factory.changes.next_action import StopAction
from dark_factory.changes.usage import BudgetSnapshot, Usage
from dark_factory.orchestration.budget import (
    DEFAULT_BUDGET_POLICY,
    BudgetCoordinator,
    BudgetExhaustedError,
    BudgetLimits,
    BudgetPolicy,
    BudgetScope,
    BudgetState,
    RoleAllowance,
    UnknownReservationError,
)
from dark_factory.orchestration.flow import apply_result
from dark_factory.orchestration.stages import StageContext, build_context, run_deterministic_stage
from tests.changes_factories import NOW, make_change, make_contract, make_run

RUN_ID = "run_01H"
REVISION = "a1b2c3d"

TOKEN_POLICY = BudgetPolicy(run_limits=BudgetLimits(token_budget=100))
COST_POLICY = BudgetPolicy(run_limits=BudgetLimits(cost_budget=Decimal("5.00")))
DEADLINE = NOW + timedelta(minutes=1)
DEADLINE_POLICY = BudgetPolicy(run_limits=BudgetLimits(deadline=DEADLINE))


def _account(
    coordinator: BudgetCoordinator,
    role: Role,
    usage: Usage,
    *,
    key: str = "op",
    now: datetime = NOW,
) -> None:
    """Account one call of ``role`` through the reservation protocol."""
    reservation = coordinator.reserve(role, key=key, estimated=usage, now=now)
    coordinator.settle(reservation.reservation_id, usage=usage)


def _context(stage: Stage, *, budget: BudgetSnapshot | None = None) -> StageContext:
    """A run-backed attempt context; the approved contract lets Construction run (T-063)."""
    return build_context(
        change=make_change(),
        stage=stage,
        route=Route.STANDARD,
        run_id=RUN_ID,
        input_revision=REVISION,
        budget=budget if budget is not None else BudgetSnapshot(),
        implementation_contract=make_contract(),
    )


# --- default policy: nothing changes -------------------------------------------------


def test_default_policy_never_triggers() -> None:
    """Regression guard: DEFAULT_BUDGET_POLICY enforces no limit at all (FR-016)."""
    coordinator = BudgetCoordinator()

    assert coordinator.policy is DEFAULT_BUDGET_POLICY
    assert coordinator.check(Role.DEVELOP, now=NOW).state is BudgetState.WITHIN_LIMITS
    assert coordinator.check(Role.DEVELOP, now=NOW).diagnostics == ""
    assert coordinator.aggregate(now=NOW).state is BudgetState.WITHIN_LIMITS

    # Even a huge spend and unknown spend stay inside a policy without limits.
    _account(coordinator, Role.DEVELOP, Usage(total_tokens=10_000_000, cost=Decimal("9999")))
    assert (
        coordinator.reserve(Role.QUALITY, key="blind", estimated=Usage(), now=NOW).settled is False
    )
    assert coordinator.check(Role.QUALITY, now=NOW).state is BudgetState.WITHIN_LIMITS


# --- limits trigger with exact diagnostics -------------------------------------------


def test_token_budget_triggers_with_exact_diagnostics() -> None:
    coordinator = BudgetCoordinator(TOKEN_POLICY)
    _account(coordinator, Role.DEVELOP, Usage(total_tokens=100))

    check = coordinator.check(Role.DEVELOP, now=NOW)

    assert check.state is BudgetState.AWAITING_DECISION
    assert [(item.scope, item.role, item.rule, item.reason) for item in check.violations] == [
        (BudgetScope.RUN, None, "token_budget", "token budget exhausted: 100/100"),
        (
            BudgetScope.ROLE,
            Role.DEVELOP,
            "token_budget",
            "role develop: token budget exhausted: 100/100",
        ),
    ]
    assert check.diagnostics == (
        "token budget exhausted: 100/100; role develop: token budget exhausted: 100/100"
    )


def test_cost_budget_triggers_with_exact_diagnostics() -> None:
    coordinator = BudgetCoordinator(COST_POLICY)
    _account(coordinator, Role.QUALITY, Usage(total_tokens=1, cost=Decimal("5.00")))

    check = coordinator.check(Role.QUALITY, now=NOW)

    assert [item.reason for item in check.violations] == [
        "cost budget exhausted: 5.00/5.00",
        "role quality: cost budget exhausted: 5.00/5.00",
    ]
    assert check.state is BudgetState.AWAITING_DECISION


def test_deadline_triggers_with_exact_diagnostics() -> None:
    coordinator = BudgetCoordinator(DEADLINE_POLICY)

    assert coordinator.check(Role.DEVELOP, now=NOW).state is BudgetState.WITHIN_LIMITS
    late = coordinator.check(Role.DEVELOP, now=NOW + timedelta(minutes=2))

    assert [item.reason for item in late.violations] == [
        f"deadline {DEADLINE.isoformat()} passed",
        f"role develop: deadline {DEADLINE.isoformat()} passed",
    ]


def test_rules_keep_the_documented_order_within_each_scope() -> None:
    """Run scope first, then role scope; token, cost, deadline inside a scope (rules/limits)."""
    policy = BudgetPolicy(
        run_limits=BudgetLimits(
            token_budget=1,
            cost_budget=Decimal("1"),
            deadline=DEADLINE,
        )
    )
    coordinator = BudgetCoordinator(policy)
    _account(coordinator, Role.DEVELOP, Usage(total_tokens=1, cost=Decimal("1")))

    violations = coordinator.check(Role.DEVELOP, now=NOW + timedelta(minutes=2)).violations

    assert [item.scope for item in violations] == [
        BudgetScope.RUN,
        BudgetScope.RUN,
        BudgetScope.RUN,
        BudgetScope.ROLE,
        BudgetScope.ROLE,
        BudgetScope.ROLE,
    ]
    assert [item.rule for item in violations] == [
        "token_budget",
        "cost_budget",
        "deadline",
        "token_budget",
        "cost_budget",
        "deadline",
    ]


def test_role_allowance_tightens_only_the_role_scope() -> None:
    """The tighter of two wins for the role; the run budget keeps its own threshold."""
    policy = BudgetPolicy(
        run_limits=BudgetLimits(token_budget=100),
        role_allowances=(RoleAllowance(role=Role.DEVELOP, limits=BudgetLimits(token_budget=10)),),
    )
    coordinator = BudgetCoordinator(policy)
    _account(coordinator, Role.DEVELOP, Usage(total_tokens=10))

    check = coordinator.check(Role.DEVELOP, now=NOW)

    assert [(item.scope, item.role, item.rule) for item in check.violations] == [
        (BudgetScope.ROLE, Role.DEVELOP, "token_budget")
    ]
    assert check.diagnostics == "role develop: token budget exhausted: 10/10"
    # Another role is untouched by the allowance and stays within the run budget.
    assert coordinator.check(Role.QUALITY, now=NOW).state is BudgetState.WITHIN_LIMITS


# --- reservations: reserve and account the spend of all calls (FR-018) ---------------


def test_an_open_reservation_counts_as_spent() -> None:
    coordinator = BudgetCoordinator(TOKEN_POLICY)

    coordinator.reserve(Role.DEVELOP, key="op-1", estimated=Usage(total_tokens=100), now=NOW)

    assert coordinator.check(Role.DEVELOP, now=NOW).state is BudgetState.AWAITING_DECISION
    assert coordinator.usage_for(Role.DEVELOP).total_tokens == 0


def test_repeating_a_key_returns_the_same_reservation_without_double_counting() -> None:
    coordinator = BudgetCoordinator(TOKEN_POLICY)

    first = coordinator.reserve(Role.DEVELOP, key="op-1", estimated=Usage(total_tokens=10), now=NOW)
    second = coordinator.reserve(
        Role.DEVELOP, key="op-1", estimated=Usage(total_tokens=10), now=NOW
    )

    assert second == first
    assert second.settled is False
    assert coordinator.aggregate(now=NOW).roles[0].reserved.total_tokens == 10


def test_reservation_ids_are_deterministic_across_coordinators() -> None:
    first = BudgetCoordinator().reserve(
        Role.DEVELOP, key="op-1", estimated=Usage(total_tokens=1), now=NOW
    )
    second = BudgetCoordinator().reserve(
        Role.DEVELOP, key="op-1", estimated=Usage(total_tokens=1), now=NOW
    )

    assert first.reservation_id == second.reservation_id == "rsv_develop_op-1"


def test_reserve_is_refused_when_the_check_is_awaiting_decision() -> None:
    coordinator = BudgetCoordinator(TOKEN_POLICY)
    _account(coordinator, Role.DEVELOP, Usage(total_tokens=100))

    with pytest.raises(BudgetExhaustedError) as excinfo:
        coordinator.reserve(Role.DEVELOP, key="op-2", estimated=Usage(total_tokens=1), now=NOW)

    assert excinfo.value.check.state is BudgetState.AWAITING_DECISION
    assert str(excinfo.value) == excinfo.value.check.diagnostics
    assert "token budget exhausted: 100/100" in str(excinfo.value)


def test_reserve_is_refused_while_an_unsettled_reservation_exists() -> None:
    coordinator = BudgetCoordinator(TOKEN_POLICY)
    first = coordinator.reserve(Role.DEVELOP, key="op-1", estimated=Usage(total_tokens=10), now=NOW)

    with pytest.raises(BudgetExhaustedError, match="already has an unsettled reservation") as info:
        coordinator.reserve(Role.DEVELOP, key="op-2", estimated=Usage(total_tokens=10), now=NOW)

    assert info.value.check.state is BudgetState.WITHIN_LIMITS
    assert first.reservation_id in str(info.value)


def test_unknown_spend_cannot_be_reserved_while_a_limit_is_configured() -> None:
    coordinator = BudgetCoordinator(TOKEN_POLICY)

    with pytest.raises(BudgetExhaustedError, match="unknown spend") as info:
        coordinator.reserve(Role.DEVELOP, key="op-1", estimated=Usage(), now=NOW)

    assert info.value.check.state is BudgetState.WITHIN_LIMITS


def test_a_role_allowance_alone_makes_unknown_spend_unreservable() -> None:
    """The limit that matters is the effective one: a role allowance counts as configured."""
    policy = BudgetPolicy(
        role_allowances=(RoleAllowance(role=Role.DEVELOP, limits=BudgetLimits(token_budget=10)),)
    )
    coordinator = BudgetCoordinator(policy)

    with pytest.raises(BudgetExhaustedError, match="unknown spend"):
        coordinator.reserve(Role.DEVELOP, key="op-1", estimated=Usage(), now=NOW)

    # A role without an allowance has no limit, so an unknown estimate is still accepted.
    assert (
        coordinator.reserve(Role.QUALITY, key="op-2", estimated=Usage(), now=NOW).settled is False
    )


def test_unknown_spend_is_reserved_without_any_limit() -> None:
    reservation = BudgetCoordinator().reserve(Role.DEVELOP, key="op-1", estimated=Usage(), now=NOW)

    assert reservation.settled is False


# --- settlement ----------------------------------------------------------------------


def test_settle_records_the_usage_and_counts_the_call() -> None:
    coordinator = BudgetCoordinator()
    reservation = coordinator.reserve(
        Role.DEVELOP, key="op-1", estimated=Usage(total_tokens=50), now=NOW
    )
    accounted = Usage(total_tokens=40, prompt_tokens=30, completion_tokens=10, cost=Decimal("0.25"))

    settled = coordinator.settle(reservation.reservation_id, usage=accounted)

    assert settled.settled is True
    assert coordinator.usage_for(Role.DEVELOP) == accounted
    assert coordinator.total_usage() == accounted
    role_usage = coordinator.aggregate(now=NOW).roles[0]
    assert (role_usage.role, role_usage.calls, role_usage.usage) == (Role.DEVELOP, 1, accounted)
    assert role_usage.reserved.total_tokens == 0


def test_unknown_settlement_keeps_the_reservation_open() -> None:
    """FR-018: unreconciled spend stays reserved, so the run stays conservative."""
    coordinator = BudgetCoordinator(TOKEN_POLICY)
    reservation = coordinator.reserve(
        Role.DEVELOP, key="op-1", estimated=Usage(total_tokens=100), now=NOW
    )

    assert coordinator.settle(reservation.reservation_id, usage=None).settled is False
    assert coordinator.settle(reservation.reservation_id, usage=Usage()).settled is False
    assert coordinator.usage_for(Role.DEVELOP).total_tokens == 0
    assert coordinator.check(Role.DEVELOP, now=NOW).state is BudgetState.AWAITING_DECISION


def test_settling_twice_counts_the_usage_once() -> None:
    coordinator = BudgetCoordinator()
    reservation = coordinator.reserve(
        Role.DEVELOP, key="op-1", estimated=Usage(total_tokens=50), now=NOW
    )
    first = coordinator.settle(reservation.reservation_id, usage=Usage(total_tokens=40))

    second = coordinator.settle(reservation.reservation_id, usage=Usage(total_tokens=40))

    assert second == first
    assert coordinator.usage_for(Role.DEVELOP).total_tokens == 40
    assert coordinator.aggregate(now=NOW).roles[0].calls == 1


def test_settling_an_unknown_reservation_raises() -> None:
    with pytest.raises(UnknownReservationError, match="rsv_develop_op-1"):
        BudgetCoordinator().settle("rsv_develop_op-1", usage=Usage(total_tokens=1))


# --- the combined budget -------------------------------------------------------------


def test_aggregate_reports_totals_and_roles_in_role_order() -> None:
    policy = BudgetPolicy(run_limits=BudgetLimits(token_budget=1000))
    coordinator = BudgetCoordinator(policy)
    _account(coordinator, Role.QUALITY, Usage(total_tokens=10, cost=Decimal("0.10")), key="q-1")
    _account(coordinator, Role.DEVELOP, Usage(total_tokens=20, cost=Decimal("0.20")), key="d-1")
    coordinator.reserve(
        Role.ARCHITECT, key="a-1", estimated=Usage(total_tokens=5, cost=Decimal("0.05")), now=NOW
    )

    aggregate = coordinator.aggregate(now=NOW)

    assert [item.role for item in aggregate.roles] == [Role.ARCHITECT, Role.DEVELOP, Role.QUALITY]
    assert aggregate.limits == policy.run_limits
    assert aggregate.state is BudgetState.WITHIN_LIMITS
    # ``run`` is what was settled; an open reservation shows up in ``reserved`` only.
    assert aggregate.run.total_tokens == 30
    assert aggregate.run.cost == Decimal("0.30")
    architect, develop, quality = aggregate.roles
    assert (architect.calls, architect.usage.total_tokens, architect.reserved.total_tokens) == (
        0,
        0,
        5,
    )
    assert (develop.calls, develop.usage.total_tokens, develop.reserved.total_tokens) == (1, 20, 0)
    assert (quality.calls, quality.usage.total_tokens) == (1, 10)


def test_aggregate_is_awaiting_decision_when_a_role_allowance_is_exhausted() -> None:
    policy = BudgetPolicy(
        run_limits=BudgetLimits(token_budget=1000),
        role_allowances=(RoleAllowance(role=Role.DEVELOP, limits=BudgetLimits(token_budget=10)),),
    )
    coordinator = BudgetCoordinator(policy)
    _account(coordinator, Role.DEVELOP, Usage(total_tokens=10))

    aggregate = coordinator.aggregate(now=NOW)

    assert aggregate.state is BudgetState.AWAITING_DECISION
    assert aggregate.limits.token_budget == 1000


def test_aggregate_is_awaiting_decision_when_the_run_budget_is_exhausted() -> None:
    coordinator = BudgetCoordinator(TOKEN_POLICY)
    _account(coordinator, Role.QUALITY, Usage(total_tokens=100))

    assert coordinator.aggregate(now=NOW).state is BudgetState.AWAITING_DECISION


def test_aggregate_is_immutable_and_serializable() -> None:
    """The aggregate is the combined budget a run record carries: frozen and round-trippable."""
    aggregate = BudgetCoordinator(TOKEN_POLICY).aggregate(now=NOW)

    restored = type(aggregate).model_validate_json(aggregate.model_dump_json())

    assert restored == aggregate
    with pytest.raises(ValidationError):
        type(aggregate).model_validate({**aggregate.model_dump(), "run": "not-a-usage"})


# --- stage path integration ----------------------------------------------------------


def test_exhausted_budget_blocks_the_stage_with_an_open_blocker() -> None:
    """DoD T-062: Awaiting Decision becomes a blocked attempt the Console can count."""
    policy = BudgetPolicy(
        role_allowances=(RoleAllowance(role=Role.DEVELOP, limits=BudgetLimits(token_budget=10)),)
    )
    coordinator = BudgetCoordinator(policy)
    _account(coordinator, Role.DEVELOP, Usage(total_tokens=10))
    check = coordinator.check(Role.DEVELOP, now=NOW)
    assert check.diagnostics == "role develop: token budget exhausted: 10/10"

    result = run_deterministic_stage(_context(Stage.PLANNING), now=NOW, budget_check=check)

    assert result.status is StageStatus.BLOCKED
    assert isinstance(result.next_action, StopAction)
    assert result.next_action.outcome is StopOutcome.BLOCKED
    assert result.next_action.reason == check.diagnostics
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.severity is FindingSeverity.BLOCKER
    assert finding.status is FindingStatus.OPEN
    assert finding.origin is FindingOrigin.CI
    assert finding.role is Role.DEVELOP
    assert finding.required_action == check.diagnostics
    assert open_blocker_count([result]) == 1


def test_blocked_budget_result_is_accepted_by_the_flow_engine() -> None:
    """Awaiting Decision uses the existing vocabulary: the run ends Blocked (ADR-018 p.5)."""
    coordinator = BudgetCoordinator(TOKEN_POLICY)
    _account(coordinator, Role.DEVELOP, Usage(total_tokens=100))
    result = run_deterministic_stage(
        _context(Stage.SPECIFICATION),
        now=NOW,
        budget_check=coordinator.check(Role.DEVELOP, now=NOW),
    )
    run = make_run(route=Route.STANDARD)

    decision = apply_result(run, result)

    assert decision.stage_status is StageStatus.BLOCKED
    assert run.status is RunStatus.BLOCKED


def test_a_run_scope_blocker_is_reported_once_per_rule() -> None:
    """Deterministic finding ids keep the Console count stable across role checks."""
    coordinator = BudgetCoordinator(TOKEN_POLICY)
    _account(coordinator, Role.QUALITY, Usage(total_tokens=100), key="q-1")
    quality = run_deterministic_stage(
        _context(Stage.PLANNING), now=NOW, budget_check=coordinator.check(Role.QUALITY, now=NOW)
    )
    develop = run_deterministic_stage(
        _context(Stage.PLANNING), now=NOW, budget_check=coordinator.check(Role.DEVELOP, now=NOW)
    )

    assert [finding.id for finding in quality.findings] == [
        "budget:run:token_budget",
        "budget:role:quality:token_budget",
    ]
    assert [finding.id for finding in develop.findings] == ["budget:run:token_budget"]
    assert develop.findings[0].role is None
    assert open_blocker_count([quality, develop]) == 2


def test_a_within_limits_check_leaves_the_stage_result_unchanged() -> None:
    """Regression guard: opting into a policy adds nothing while the budget holds."""
    context = _context(Stage.CONSTRUCTION)

    baseline = run_deterministic_stage(context, now=NOW)
    checked = run_deterministic_stage(
        context, now=NOW, budget_check=BudgetCoordinator(TOKEN_POLICY).check(Role.DEVELOP, now=NOW)
    )
    default = run_deterministic_stage(
        context, now=NOW, budget_check=BudgetCoordinator().check(Role.DEVELOP, now=NOW)
    )

    assert default.model_dump(mode="json") == baseline.model_dump(mode="json")
    assert checked.model_dump(mode="json") == baseline.model_dump(mode="json")
    assert default.status is StageStatus.WAITING
    assert default.findings == []


def test_carried_snapshot_exhaustion_still_wins_over_the_coordinator() -> None:
    """The persisted run snapshot stays the first stop condition; the new seam is additive."""
    coordinator = BudgetCoordinator(TOKEN_POLICY)
    _account(coordinator, Role.DEVELOP, Usage(total_tokens=100))
    context = _context(Stage.PLANNING, budget=BudgetSnapshot(token_budget=1, tokens_used=1))

    result = run_deterministic_stage(
        context, now=NOW, budget_check=coordinator.check(Role.DEVELOP, now=NOW)
    )

    assert isinstance(result.next_action, StopAction)
    assert result.next_action.reason == "token budget exhausted: 1/1"
    assert result.findings == []
