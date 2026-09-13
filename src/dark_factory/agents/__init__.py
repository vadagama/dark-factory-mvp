"""Agent roles as pluggable change workers (ADR-007).

A profile is the versioned manifest of one role — declared inputs/outputs, a
tool allowlist, constraints and stop-conditions; a skill is one unit of work
bound to a role. The execution contract is
``AgentProfile -> TaskEnvelope -> AgentResult`` (``dark_factory.agents.contract``).

Core MVP ships profiles for product, develop and quality only (ADR-007 p.4);
the other six roles get profiles in T-046/T-047.
"""

from dark_factory.agents.artifacts import ArtifactKind
from dark_factory.agents.contract import build_envelope, validate_agent_result
from dark_factory.agents.errors import (
    AgentDefinitionError,
    ProfileNotFoundError,
    SkillNotFoundError,
)
from dark_factory.agents.profiles import (
    AGENT_PROFILE_SCHEMA_VERSION,
    CORE_ROLES,
    AgentProfile,
    ProfileSchemaVersion,
    get_profile,
)
from dark_factory.agents.skills import (
    AGENT_SKILL_SCHEMA_VERSION,
    SkillManifest,
    SkillSchemaVersion,
    get_skill,
)

__all__ = [
    "AGENT_PROFILE_SCHEMA_VERSION",
    "AGENT_SKILL_SCHEMA_VERSION",
    "CORE_ROLES",
    "AgentDefinitionError",
    "AgentProfile",
    "ArtifactKind",
    "ProfileNotFoundError",
    "ProfileSchemaVersion",
    "SkillManifest",
    "SkillNotFoundError",
    "SkillSchemaVersion",
    "build_envelope",
    "get_profile",
    "get_skill",
    "validate_agent_result",
]
