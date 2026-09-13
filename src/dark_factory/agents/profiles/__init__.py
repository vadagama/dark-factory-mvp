"""Versioned manifests of the agent role profiles (T-011, ADR-007)."""

from dark_factory.agents.profiles.manifest import (
    AGENT_PROFILE_SCHEMA_VERSION,
    AgentProfile,
    ProfileSchemaVersion,
)
from dark_factory.agents.profiles.registry import CORE_ROLES, get_profile

__all__ = [
    "AGENT_PROFILE_SCHEMA_VERSION",
    "CORE_ROLES",
    "AgentProfile",
    "ProfileSchemaVersion",
    "get_profile",
]
