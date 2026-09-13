"""Versioned manifests of the agent skills (T-011)."""

from dark_factory.agents.skills.manifest import (
    AGENT_SKILL_SCHEMA_VERSION,
    SkillManifest,
    SkillSchemaVersion,
)
from dark_factory.agents.skills.registry import get_skill

__all__ = [
    "AGENT_SKILL_SCHEMA_VERSION",
    "SkillManifest",
    "SkillSchemaVersion",
    "get_skill",
]
