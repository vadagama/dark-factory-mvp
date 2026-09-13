"""Errors raised by the agent definition layer (profiles and skills registries)."""


class AgentDefinitionError(RuntimeError):
    """Base class of agent profile/skill definition errors."""


class ProfileNotFoundError(AgentDefinitionError):
    """No profile manifest exists for the role (ADR-007 p.4: the core ships three)."""


class SkillNotFoundError(AgentDefinitionError):
    """No skill manifest is registered under the requested id."""
