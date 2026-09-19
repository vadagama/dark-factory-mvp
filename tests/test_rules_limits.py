"""Deterministic limit rules: rework, token/cost budgets, deadline (T-004)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from dark_factory.changes.usage import BudgetSnapshot
from dark_factory.orchestration.rules.limits import continuation_violations, rework_violation

NOW = datetime(2026, 9, 13, 12, 0, 0, tzinfo=UTC)


def test_fresh_budget_allows_rework() -> None:
    assert rework_violation(BudgetSnapshot(), requested_round=1) is None


def test_exhausted_rework_limit_triggers() -> None:
    budget = BudgetSnapshot(max_rework_rounds=3, used_rework_rounds=3)
    violation = rework_violation(budget, requested_round=4)
    assert violation is not None
    assert violation.rule == "rework_limit"
    assert "exhausted" in violation.reason


def test_round_beyond_limit_triggers_even_with_capacity() -> None:
    budget = BudgetSnapshot(max_rework_rounds=3, used_rework_rounds=0)
    violation = rework_violation(budget, requested_round=4)
    assert violation is not None
    assert violation.rule == "rework_limit"


def test_last_allowed_round_passes() -> None:
    budget = BudgetSnapshot(max_rework_rounds=3, used_rework_rounds=2)
    assert rework_violation(budget, requested_round=3) is None


def test_continuation_is_clean_with_default_budget() -> None:
    assert continuation_violations(BudgetSnapshot(), now=NOW) == []


def test_token_budget_triggers_only_when_exhausted() -> None:
    under = BudgetSnapshot(token_budget=100, tokens_used=99)
    assert continuation_violations(under, now=NOW) == []
    at = BudgetSnapshot(token_budget=100, tokens_used=100)
    violations = continuation_violations(at, now=NOW)
    assert [v.rule for v in violations] == ["token_budget"]


def test_cost_budget_triggers_only_when_exhausted() -> None:
    under = BudgetSnapshot(cost_budget=Decimal("10"), cost_used=Decimal("9.99"))
    assert continuation_violations(under, now=NOW) == []
    at = BudgetSnapshot(cost_budget=Decimal("10"), cost_used=Decimal("10"))
    assert [v.rule for v in continuation_violations(at, now=NOW)] == ["cost_budget"]


def test_deadline_triggers_only_after_reference_time() -> None:
    budget = BudgetSnapshot(deadline=NOW + timedelta(minutes=1))
    assert continuation_violations(budget, now=NOW) == []
    assert continuation_violations(budget, now=NOW + timedelta(minutes=2)) == [
        continuation_violations(budget, now=NOW + timedelta(minutes=2))[0]
    ]


def test_all_violations_report_together() -> None:
    budget = BudgetSnapshot(
        token_budget=1,
        tokens_used=1,
        cost_budget=Decimal("1"),
        cost_used=Decimal("1"),
        deadline=NOW - timedelta(days=1),
    )
    rules = [v.rule for v in continuation_violations(budget, now=NOW)]
    assert rules == ["token_budget", "cost_budget", "deadline"]
