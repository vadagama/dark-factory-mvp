"""Implementation Contract: approved boundary of autonomous implementation (T-016, ADR-018 p.3-4).

Discovery/design produce the contract together with the human
(Human-in-the-loop); after approval the implementation runs
Human-off-the-loop inside its boundaries (ADR-018 p.4). The machine side —
the construction entry gate, the autonomy budget and the escalation checks —
lives in ``dark_factory.orchestration.policy``. In the MVP the contract is a
field model carried by the run (storage: task/MR); after T-020 it becomes a
ChangeSet artifact (ADR-020). No file I/O here.
"""

from datetime import datetime
from decimal import Decimal
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from dark_factory.changes.enums import BoundaryArea, EscalationRule, RiskClass, Role
from dark_factory.changes.refs import ArtifactRef

IMPLEMENTATION_CONTRACT_SCHEMA_VERSION: Final = 1
"""Schema version; adding optional fields keeps it, breaking changes bump it (ADR-015 p.3)."""

# Literal contract type of IMPLEMENTATION_CONTRACT_SCHEMA_VERSION; keep the two in sync.
type ContractSchemaVersion = Literal[1]


def _all_escalation_rules() -> tuple[EscalationRule, ...]:
    return tuple(EscalationRule)


class ChangeScope(BaseModel):
    """Approved change boundary: items the implementation may touch, plus explicit exclusions."""

    model_config = ConfigDict(frozen=True)

    in_scope: tuple[str, ...] = Field(min_length=1)
    out_of_scope: tuple[str, ...] = ()


class AcceptanceCriterion(BaseModel):
    """One testable acceptance criterion the implementation must satisfy (ADR-018 p.3)."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    description: str = Field(min_length=1)


class ContractBudget(BaseModel):
    """Autonomy budget granted by the contract: iterations, time and cost (ADR-018 p.3).

    ``max_autonomous_iterations`` bounds the stage attempts of the run (the
    escalation check counts the ``StageRun`` occurrences); rework rounds stay
    on the run-level ``BudgetSnapshot`` (FR-008); T-062 refines the iteration
    accounting.
    """

    model_config = ConfigDict(frozen=True)

    max_autonomous_iterations: int = Field(ge=1)
    deadline: datetime | None = None
    max_cost: Decimal | None = None


class ContractApproval(BaseModel):
    """Human approval that turns the contract into the implementation boundary (ADR-018 p.3)."""

    model_config = ConfigDict(frozen=True)

    approved_by: Role
    decided_at: datetime
    comment: str | None = None


class ImplementationContract(BaseModel):
    """Approved boundary of one autonomous implementation (ADR-018 p.3-4).

    ``approval is None`` means the contract is not approved yet: the flow
    blocks entering implementation (T-016 DoD). ``allowed_boundaries`` names
    the protected boundaries the contract explicitly permits to touch
    (ADR-018 p.5). ``escalation_rules`` lists the agreed escalation
    conditions and defaults to the full machine-checkable set — the checks of
    ``orchestration.policy`` apply it; per-contract subsets take effect with
    the specification-gate policy (T-021).
    """

    model_config = ConfigDict(frozen=True)

    schema_version: ContractSchemaVersion = IMPLEMENTATION_CONTRACT_SCHEMA_VERSION
    id: str = Field(min_length=1)
    scope: ChangeScope
    acceptance_criteria: tuple[AcceptanceCriterion, ...] = Field(min_length=1)
    architectural_constraints: tuple[str, ...] = ()
    ui_evidence: tuple[ArtifactRef, ...] = ()
    risk_class: RiskClass
    budget: ContractBudget
    allowed_boundaries: tuple[BoundaryArea, ...] = ()
    escalation_rules: tuple[EscalationRule, ...] = Field(default_factory=_all_escalation_rules)
    approval: ContractApproval | None = None
