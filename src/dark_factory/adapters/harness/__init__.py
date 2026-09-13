"""PydanticAI adapter of ``HarnessPort`` (T019, docs T-010).

Single public surface of the harness adapter. The LLM endpoint is the existing
LiteLLM proxy (OpenAI-compatible, ADR-002 §6); concrete models are never
hardcoded, configuration comes from environment variables (``HarnessConfig``).
Tools per role and structured outputs are the minimal extension points that
T-011 (role profiles, extended envelope) fills in.
"""

from dark_factory.adapters.harness.adapter import PydanticAIHarness, RoleTools, ToolFunction
from dark_factory.adapters.harness.config import (
    LLM_API_KEY_ENV_VAR,
    LLM_BASE_URL_ENV_VAR,
    LLM_MODEL_ENV_VAR,
    HarnessConfig,
)

__all__ = [
    "LLM_API_KEY_ENV_VAR",
    "LLM_BASE_URL_ENV_VAR",
    "LLM_MODEL_ENV_VAR",
    "HarnessConfig",
    "PydanticAIHarness",
    "RoleTools",
    "ToolFunction",
]
