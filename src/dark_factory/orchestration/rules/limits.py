"""Deterministic limit rules of the Factory Flow (T-004, FR-008/FR-016/FR-018).

Limits are pure functions of the budget snapshot (plus an explicit reference
time for the deadline): identical inputs always produce identical verdicts.
Exhaustion stops autonomous execution with ``Blocked`` (ADR-018 p.5).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from dark_factory.changes.usage import BudgetSnapshot

type LimitRule = Literal["rework_limit", "token_budget", "cost_budget", "deadline"]


@dataclass(frozen=True)
class LimitViolation:
    """One triggered limit; ``reason`` is human-readable diagnostics."""

    rule: LimitRule
    reason: str


def rework_violation(budget: BudgetSnapshot, *, requested_round: int) -> LimitViolation | None:
    """Check a requested rework round against the run-level rework limit (FR-008).

    The ``BudgetSnapshot`` counters are the authority on how many rounds the
    run already spent (FR-016); the action's own counters are informational.
    """
    if budget.rework_exhausted:
        return LimitViolation(
            rule="rework_limit",
            reason=(
                "rework limit exhausted: "
                f"{budget.used_rework_rounds}/{budget.max_rework_rounds} rounds used"
            ),
        )
    if requested_round > budget.max_rework_rounds:
        return LimitViolation(
            rule="rework_limit",
            reason=(
                f"rework round {requested_round} exceeds the limit of {budget.max_rework_rounds}"
            ),
        )
    return None


def continuation_violations(budget: BudgetSnapshot, *, now: datetime) -> list[LimitViolation]:
    """Limits that block further autonomous work (next stage or rework round)."""
    violations: list[LimitViolation] = []
    if budget.token_budget_exhausted:
        violations.append(
            LimitViolation(
                rule="token_budget",
                reason=f"token budget exhausted: {budget.tokens_used}/{budget.token_budget}",
            )
        )
    if budget.cost_budget_exhausted:
        violations.append(
            LimitViolation(
                rule="cost_budget",
                reason=f"cost budget exhausted: {budget.cost_used}/{budget.cost_budget}",
            )
        )
    if budget.deadline is not None and now > budget.deadline:
        violations.append(
            LimitViolation(
                rule="deadline",
                reason=f"deadline {budget.deadline.isoformat()} passed",
            )
        )
    return violations
