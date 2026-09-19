"""Intake of a change: the structured brief, the scenario and the spend limit (T071).

A change starts as an operator's intent (plan §6, phase Ф0 «Инициатива»): what
hurts (``problem``), what should be true afterwards (``goal``), what must not
change (``constraints``) and what is explicitly left out (``out_of_scope``).
The brief keeps the operator's original wording (``source_text``) next to the
structured fields, so an agent's formulation (T072) never erases the intent it
was derived from; when the agent could not help, the brief stays a ``draft``
and the cause is observable in ``error`` instead of a silent empty form.

The scenario (``specs_only`` / ``full``) and the spend limit are operator
decisions taken at intake (plan §2, decisions 2 and 10). The limit is money,
in USD: it is copied into the run's ``BudgetSnapshot`` when the run is created,
so the budget the Flow and the budget coordinator see is the operator's limit
(the hard stop of the ChangeSet on that limit is T104).
"""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dark_factory.changes.enums import BriefAuthor, BriefStatus
from dark_factory.changes.usage import BudgetSnapshot

__all__ = ["IntakeBrief", "SpendLimit", "clean_lines"]


def clean_lines(items: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """Strip and drop blank entries; order is preserved (the operator's order matters)."""
    return tuple(item.strip() for item in items if item and item.strip())


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


class IntakeBrief(BaseModel):
    """Structured brief of a change (T071; formulated by the agent in T072).

    ``status`` is derived, never submitted as a claim: a brief is ``complete``
    only when ``problem`` and ``goal`` are stated; anything else is a
    ``draft``. ``formulated_by`` records who wrote the current structured
    fields, ``source_text`` the free text they came from, ``error`` the last
    reason the agent could not formulate (observable per T072 DoD).
    """

    model_config = ConfigDict(frozen=True)

    problem: str | None = None
    goal: str | None = None
    constraints: tuple[str, ...] = ()
    out_of_scope: tuple[str, ...] = ()
    source_text: str | None = None
    status: BriefStatus = BriefStatus.DRAFT
    formulated_by: BriefAuthor | None = None
    error: str | None = None

    @field_validator("problem", "goal", "source_text", "error", mode="before")
    @classmethod
    def _strip_text(cls, value: object) -> object:
        return _clean(value) if isinstance(value, str) else value

    @field_validator("constraints", "out_of_scope", mode="before")
    @classmethod
    def _strip_lines(cls, value: object) -> object:
        if isinstance(value, list | tuple):
            return clean_lines([str(item) for item in value])
        return value

    @model_validator(mode="after")
    def _derive_status(self) -> "IntakeBrief":
        complete = self.problem is not None and self.goal is not None
        derived = BriefStatus.COMPLETE if complete else BriefStatus.DRAFT
        if self.status is not derived:
            # A frozen model: derive through the private setter pydantic allows here.
            object.__setattr__(self, "status", derived)
        return self

    @property
    def is_complete(self) -> bool:
        """True when requirements can start from this brief."""
        return self.status is BriefStatus.COMPLETE

    @property
    def missing(self) -> tuple[str, ...]:
        """Names of the required fields that are still empty (for guidance and CLI)."""
        required = (("problem", self.problem), ("goal", self.goal))
        return tuple(name for name, value in required if value is None)


class SpendLimit(BaseModel):
    """Hard spend limit of a change chosen at intake (T071, plan §2 decision 10).

    ``cost_budget_usd`` is the money limit; ``token_budget`` is an optional
    second dimension. Both feed the run budget through :meth:`to_budget`.
    """

    model_config = ConfigDict(frozen=True)

    cost_budget_usd: Decimal = Field(gt=0, max_digits=12, decimal_places=4)
    token_budget: int | None = Field(default=None, ge=1)

    def to_budget(self, base: BudgetSnapshot | None = None) -> BudgetSnapshot:
        """The run budget carrying this limit on top of ``base`` (default snapshot)."""
        snapshot = base if base is not None else BudgetSnapshot()
        return snapshot.model_copy(
            update={"cost_budget": self.cost_budget_usd, "token_budget": self.token_budget}
        )
