"""Versioned manifest of an agent role profile (T-011, ADR-007).

A profile is the typed constant of one role: what it consumes and produces
(:class:`~dark_factory.agents.artifacts.ArtifactKind`), the tool allowlist
(names only — the harness wiring resolves them to callables and T-011 fills
the per-role sets from these profiles), behavioural constraints and textual
stop-conditions (free-form; machine interpretation arrives with the escalation
policy, T-016). The role is the profile identity: exactly one profile per role.
"""

from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from dark_factory.agents.artifacts import ArtifactKind
from dark_factory.changes.enums import Role

AGENT_PROFILE_SCHEMA_VERSION: Final = 1
"""Schema version of the profile manifest contract; breaking changes bump it (ADR-015 p.3)."""

# Literal contract type of AGENT_PROFILE_SCHEMA_VERSION; keep the two in sync.
type ProfileSchemaVersion = Literal[1]


class AgentProfile(BaseModel):
    """Versioned manifest of one agent role (ADR-007 p.3)."""

    model_config = ConfigDict(frozen=True)

    schema_version: ProfileSchemaVersion = AGENT_PROFILE_SCHEMA_VERSION
    role: Role
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    description: str = Field(min_length=1)
    inputs: tuple[ArtifactKind, ...]
    outputs: tuple[ArtifactKind, ...]
    tools: tuple[str, ...]
    constraints: tuple[str, ...]
    stop_conditions: tuple[str, ...]
    skills: tuple[str, ...]
