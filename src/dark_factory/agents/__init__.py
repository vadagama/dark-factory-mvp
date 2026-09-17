"""Agent roles as pluggable change workers (ADR-007).

A profile is the versioned manifest of one role — declared inputs/outputs, a
tool allowlist, constraints and stop-conditions; a skill is one unit of work
bound to a role. The execution contract is
``AgentProfile -> TaskEnvelope -> AgentResult`` (``dark_factory.agents.contract``).

The registry ships profiles for all nine roles of the ADR-007 catalog:
product, design, architect, develop and quality (ADR-007 p.4), plus
infrastructure, security, ci_cd and operation (T-047, the R3/R4 obligations
of ADR-023).
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
