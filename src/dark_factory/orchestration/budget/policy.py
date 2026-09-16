"""Budget policy: run-level limits and per-role allowances (T-062, FR-016/FR-018, ADR-018 p.3).

The policy is pure data: it fixes *which* limits exist, while
:mod:`dark_factory.orchestration.budget.coordinator` decides whether they are
exhausted. Field names and semantics mirror ``BudgetSnapshot``
(:mod:`dark_factory.changes.usage`) so the two cannot drift, and no threshold is
restated here — the comparison itself stays in ``rules/limits.py``.

A role allowance may only **tighten** the run budget, never loosen it (FR-018):
the effective limit of a role is the tighter of the run and the role value per
dimension, with ``None`` meaning "not set". ``DEFAULT_BUDGET_POLICY`` sets no
limit at all, so a run that does not opt into a policy keeps today's behaviour
(FR-016: restarts never reset spend, and no new limit appears out of nowhere).
"""

from datetime import datetime
from decimal import Decimal
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

from dark_factory.changes.enums import Role


class BudgetLimits(BaseModel):
    """Limits of one budget scope; ``None`` means the dimension is not constrained."""

    model_config = ConfigDict(frozen=True)

    token_budget: int | None = Field(default=None, ge=1)
    cost_budget: Decimal | None = None
    deadline: datetime | None = None

    @property
    def configured(self) -> bool:
        """True when at least one dimension is limited."""
        return (
            self.token_budget is not None
            or self.cost_budget is not None
            or self.deadline is not None
        )


class RoleAllowance(BaseModel):
    """Limits granted to one role on top of the run budget (ADR-018 p.3)."""

    model_config = ConfigDict(frozen=True)

    role: Role
    limits: BudgetLimits


class BudgetPolicy(BaseModel):
    """Budget policy of a run: run-level limits plus optional per-role allowances.

    At most one allowance per role is allowed: a duplicate is a configuration
    error and fails loudly instead of silently picking one of the two.
    """

    model_config = ConfigDict(frozen=True)

    run_limits: BudgetLimits = Field(default_factory=BudgetLimits)
    role_allowances: tuple[RoleAllowance, ...] = ()

    @field_validator("role_allowances")
    @classmethod
    def _normalize_allowances(cls, value: tuple[RoleAllowance, ...]) -> tuple[RoleAllowance, ...]:
        """Reject duplicate roles and keep the allowances in ``Role.value`` order."""
        counts: dict[Role, int] = {}
        for allowance in value:
            counts[allowance.role] = counts.get(allowance.role, 0) + 1
        duplicates = sorted(role.value for role, count in counts.items() if count > 1)
        if duplicates:
            raise ValueError(f"duplicate role allowances: {', '.join(duplicates)}")
        return tuple(sorted(value, key=lambda item: item.role.value))

    def limits_for(self, role: Role) -> BudgetLimits:
        """Effective limits of ``role``: the tighter of the run budget and its allowance.

        A role allowance can only tighten: a dimension limited only at run level
        keeps the run value, a dimension limited at both keeps the smaller one,
        and ``None`` keeps meaning "not set" (never "unlimited").
        """
        allowance = next(
            (item.limits for item in self.role_allowances if item.role is role),
            None,
        )
        if allowance is None:
            return self.run_limits
        return BudgetLimits(
            token_budget=_tighter(self.run_limits.token_budget, allowance.token_budget),
            cost_budget=_tighter(self.run_limits.cost_budget, allowance.cost_budget),
            deadline=_tighter(self.run_limits.deadline, allowance.deadline),
        )


DEFAULT_BUDGET_POLICY: Final = BudgetPolicy()
"""Policy without any limit: the stage path behaves exactly as before T-062."""


def _tighter[T: (int, Decimal, datetime)](left: T | None, right: T | None) -> T | None:
    """Tighter of two optional limits of one dimension; ``None`` never tightens."""
    if left is None:
        return right
    if right is None:
        return left
    return left if left < right else right
