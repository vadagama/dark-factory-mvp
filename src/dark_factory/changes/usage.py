"""Usage accounting, per-role usage and budget snapshots (hld-mvp 6-7)."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from dark_factory.changes.enums import Role


class Usage(BaseModel):
    """Token/cost usage of one agent call or aggregated over a stage."""

    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    cost: Decimal | None = None


class RoleUsage(BaseModel):
    """Settled usage of one role plus what it committed but has not accounted yet.

    A role the ledger knows nothing about reports zero spend, known exactly:
    ``total_tokens`` is ``0``, never ``None``, so an empty section is not
    misread as *unknown* spend (FR-018); ``cost`` stays ``None`` until a call
    reports one.
    """

    model_config = ConfigDict(frozen=True)

    role: Role
    usage: Usage = Field(default_factory=lambda: Usage(total_tokens=0))
    calls: int = Field(default=0, ge=0)
    reserved: Usage = Field(default_factory=lambda: Usage(total_tokens=0))


class BudgetSnapshot(BaseModel):
    """Budget and limits of a run, preserved between CI jobs (hld-mvp 8).

    Unknown usage is treated conservatively, so used values are not validated
    against budgets here; exhaustion flags are advisory input for the Flow
    (T-004) and the budget coordinator (T-062).
    """

    max_rework_rounds: int = Field(default=3, ge=0)
    used_rework_rounds: int = Field(default=0, ge=0)
    token_budget: int | None = Field(default=None, ge=1)
    tokens_used: int = Field(default=0, ge=0)
    cost_budget: Decimal | None = None
    cost_used: Decimal = Decimal("0")
    deadline: datetime | None = None

    @property
    def rework_rounds_remaining(self) -> int:
        """Rework rounds left before the Flow must stop the change."""
        return max(self.max_rework_rounds - self.used_rework_rounds, 0)

    @property
    def rework_exhausted(self) -> bool:
        """True when the rework limit is reached."""
        return self.used_rework_rounds >= self.max_rework_rounds

    @property
    def token_budget_exhausted(self) -> bool:
        """True when the token budget is set and fully used."""
        return self.token_budget is not None and self.tokens_used >= self.token_budget

    @property
    def cost_budget_exhausted(self) -> bool:
        """True when the cost budget is set and fully used."""
        return self.cost_budget is not None and self.cost_used >= self.cost_budget
