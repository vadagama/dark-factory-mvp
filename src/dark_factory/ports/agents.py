"""Agent task/result contracts behind ``HarnessPort`` (T-005, extended in T-011).

``schema_version`` follows the versioned-contract rule (ADR-015 p.3): adding
optional fields keeps the version, breaking changes bump it. T-011 added
``TaskEnvelope.skill_id`` and ``TaskEnvelope.bundle_hash`` additively: the
envelope pins the role, the skill and the ``ContextBundle`` snapshot the agent
must work from (FR-001); the profile pipeline lives in ``dark_factory.agents``.
"""

from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from dark_factory.changes.enums import Role, Stage
from dark_factory.changes.usage import Usage

AGENTS_SCHEMA_VERSION: Final = 1
"""Schema version of the agent contract; adding fields keeps it, breaking changes bump it."""

# Literal contract type of AGENTS_SCHEMA_VERSION; keep the two in sync.
type AgentSchemaVersion = Literal[1]


class TaskEnvelope(BaseModel):
    """Task handed to one agent through ``HarnessPort.run_stage``.

    The minimal fields carry what a harness needs; ``skill_id`` and
    ``bundle_hash`` extend the contract additively (T-011, ADR-007) and stay
    optional so minimal envelopes remain valid (ADR-015 p.3).
    """

    model_config = ConfigDict(frozen=True)

    schema_version: AgentSchemaVersion = AGENTS_SCHEMA_VERSION
    change_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    stage: Stage
    role: Role
    instruction: str = Field(min_length=1)
    skill_id: str | None = Field(default=None, min_length=1)
    bundle_hash: str | None = Field(default=None, min_length=1)


class AgentResult(BaseModel):
    """Result of one agent call returned by ``HarnessPort.run_stage``."""

    model_config = ConfigDict(frozen=True)

    schema_version: AgentSchemaVersion = AGENTS_SCHEMA_VERSION
    ok: bool
    output: str = ""
    usage: Usage | None = None
