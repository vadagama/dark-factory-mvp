"""Minimal agent task/result contracts behind ``HarnessPort`` (T-005).

Versioned minimum: the full ``TaskEnvelope``/``AgentResult`` contract arrives
with T-011 in ``dark_factory.agents``; these types carry only what the P0 fake
harness needs. ``schema_version`` follows the versioned-contract rule
(ADR-015 p.3): adding fields is backward-compatible, breaking changes bump it.
"""

from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from dark_factory.changes.enums import Role, Stage
from dark_factory.changes.usage import Usage

AGENTS_SCHEMA_VERSION: Final = 1
"""Schema version of the minimal agent contract; T-011 owns the next version."""

# Literal contract type of AGENTS_SCHEMA_VERSION; keep the two in sync.
type AgentSchemaVersion = Literal[1]


class TaskEnvelope(BaseModel):
    """Task handed to one agent through ``HarnessPort.run_stage`` (T-011 extends)."""

    model_config = ConfigDict(frozen=True)

    schema_version: AgentSchemaVersion = AGENTS_SCHEMA_VERSION
    change_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    stage: Stage
    role: Role
    instruction: str = Field(min_length=1)


class AgentResult(BaseModel):
    """Result of one agent call returned by ``HarnessPort.run_stage`` (T-011 extends)."""

    model_config = ConfigDict(frozen=True)

    schema_version: AgentSchemaVersion = AGENTS_SCHEMA_VERSION
    ok: bool
    output: str = ""
    usage: Usage | None = None
