"""Versioned manifest of an agent skill (T-011).

A skill is one executable unit of work bound to exactly one role: its
``instruction`` is the text the agent runs on (the harness sends it as the
prompt, T-019), and ``stop_conditions`` are free-form texts — the agent must
stop/escalate when one holds, machine interpretation arrives later (T-016).
The ``id`` is the stable kebab-case wire id profiles reference.
"""

from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from dark_factory.agents.artifacts import ArtifactKind
from dark_factory.changes.enums import Role

AGENT_SKILL_SCHEMA_VERSION: Final = 1
"""Schema version of the skill manifest contract; breaking changes bump it (ADR-015 p.3)."""

# Literal contract type of AGENT_SKILL_SCHEMA_VERSION; keep the two in sync.
type SkillSchemaVersion = Literal[1]

_SKILL_ID_PATTERN = r"^[a-z0-9]+(-[a-z0-9]+)*$"
"""Stable wire-id form: kebab-case (ADR-007; skill ids cross process boundaries)."""


class SkillManifest(BaseModel):
    """Versioned manifest of one agent skill (T-011)."""

    model_config = ConfigDict(frozen=True)

    schema_version: SkillSchemaVersion = AGENT_SKILL_SCHEMA_VERSION
    id: str = Field(min_length=1, pattern=_SKILL_ID_PATTERN)
    version: str = Field(min_length=1)
    role: Role
    purpose: str = Field(min_length=1)
    inputs: tuple[ArtifactKind, ...]
    outputs: tuple[ArtifactKind, ...]
    instruction: str = Field(min_length=1)
    stop_conditions: tuple[str, ...]
