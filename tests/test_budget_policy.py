"""Budget policy: run limits, per-role allowances and tighter-of-two (T-062, FR-018)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from dark_factory.changes.enums import Role
from dark_factory.orchestration.budget import (
    DEFAULT_BUDGET_POLICY,
    BudgetLimits,
    BudgetPolicy,
    RoleAllowance,
)

DEADLINE = datetime(2026, 9, 16, 12, 0, 0, tzinfo=UTC)


def test_default_policy_sets_no_limit_for_any_role() -> None:
    """Regression guard: the default policy keeps today's behaviour — no limits at all."""
    assert DEFAULT_BUDGET_POLICY.run_limits == BudgetLimits()
    assert DEFAULT_BUDGET_POLICY.role_allowances == ()
    for role in Role:
        limits = DEFAULT_BUDGET_POLICY.limits_for(role)
        assert limits == BudgetLimits()
        assert limits.configured is False


def test_role_allowance_can_only_tighten_the_run_budget() -> None:
    policy = BudgetPolicy(
        run_limits=BudgetLimits(token_budget=1000),
        role_allowances=(
            RoleAllowance(role=Role.DEVELOP, limits=BudgetLimits(token_budget=100)),
            # A grant above the run budget must not raise the run limit (FR-018).
            RoleAllowance(role=Role.QUALITY, limits=BudgetLimits(token_budget=5000)),
        ),
    )
    assert policy.limits_for(Role.DEVELOP).token_budget == 100
    assert policy.limits_for(Role.QUALITY).token_budget == 1000
    assert policy.limits_for(Role.ARCHITECT).token_budget == 1000


def test_allowance_dimensions_merge_with_the_run_limits() -> None:
    policy = BudgetPolicy(
        run_limits=BudgetLimits(cost_budget=Decimal("10"), deadline=DEADLINE),
        role_allowances=(RoleAllowance(role=Role.DEVELOP, limits=BudgetLimits(token_budget=50)),),
    )
    limits = policy.limits_for(Role.DEVELOP)
    assert limits.token_budget == 50
    assert limits.cost_budget == Decimal("10")
    assert limits.deadline == DEADLINE


def test_the_earlier_deadline_wins_whichever_side_sets_it() -> None:
    earlier = DEADLINE - timedelta(hours=1)
    run_tighter = BudgetPolicy(
        run_limits=BudgetLimits(deadline=DEADLINE),
        role_allowances=(RoleAllowance(role=Role.DEVELOP, limits=BudgetLimits(deadline=earlier)),),
    )
    allowance_tighter = BudgetPolicy(
        run_limits=BudgetLimits(deadline=earlier),
        role_allowances=(RoleAllowance(role=Role.DEVELOP, limits=BudgetLimits(deadline=DEADLINE)),),
    )
    assert run_tighter.limits_for(Role.DEVELOP).deadline == earlier
    assert allowance_tighter.limits_for(Role.DEVELOP).deadline == earlier


def test_allowances_are_kept_in_role_order() -> None:
    policy = BudgetPolicy(
        role_allowances=(
            RoleAllowance(role=Role.QUALITY, limits=BudgetLimits(token_budget=1)),
            RoleAllowance(role=Role.DEVELOP, limits=BudgetLimits(token_budget=1)),
        )
    )
    assert [item.role for item in policy.role_allowances] == [Role.DEVELOP, Role.QUALITY]


def test_duplicate_role_allowances_are_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate role allowances: develop"):
        BudgetPolicy(
            role_allowances=(
                RoleAllowance(role=Role.DEVELOP, limits=BudgetLimits(token_budget=1)),
                RoleAllowance(role=Role.DEVELOP, limits=BudgetLimits(token_budget=2)),
            )
        )


def test_token_budget_mirrors_the_snapshot_lower_bound() -> None:
    """A zero limit is meaningless: BudgetSnapshot requires ge=1, and so does the policy."""
    with pytest.raises(ValidationError):
        BudgetLimits(token_budget=0)
