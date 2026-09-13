"""Environment configuration of the PydanticAI harness adapter (T019, docs T-010).

The LLM endpoint is the existing LiteLLM proxy (OpenAI-compatible, ADR-002 §6).
Concrete models are never hardcoded: the endpoint is fully configured through
environment variables. Missing configuration never breaks import or
construction — ``from_env`` returns ``None``, and the adapter reports the gap
lazily through ``health()``/``run_stage()`` so the deterministic steps of a
stage survive a disabled LLM (US1 scenario 3, R-17).

Environment variables:

- ``DARK_FACTORY_LLM_BASE_URL`` — OpenAI-compatible base URL of the LiteLLM proxy.
- ``DARK_FACTORY_LLM_API_KEY`` — API key of the proxy; a secret, never reported back (ADR-009).
- ``DARK_FACTORY_LLM_MODEL`` — model name served by the proxy (e.g. ``openai/gpt-4o-mini``).
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

LLM_BASE_URL_ENV_VAR: Final[str] = "DARK_FACTORY_LLM_BASE_URL"
"""OpenAI-compatible base URL of the LiteLLM proxy (ADR-002 §6)."""

LLM_API_KEY_ENV_VAR: Final[str] = "DARK_FACTORY_LLM_API_KEY"
"""API key of the proxy; a secret, never included in adapter output (ADR-009)."""

LLM_MODEL_ENV_VAR: Final[str] = "DARK_FACTORY_LLM_MODEL"
"""Model name served by the proxy; concrete models are never hardcoded (ADR-002 §6)."""

_REQUIRED_ENV_VARS: Final[tuple[str, ...]] = (
    LLM_BASE_URL_ENV_VAR,
    LLM_API_KEY_ENV_VAR,
    LLM_MODEL_ENV_VAR,
)


def _env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    """The given mapping or, by default, the process environment."""
    return os.environ if env is None else env


@dataclass(frozen=True, slots=True)
class HarnessConfig:
    """Endpoint configuration of the harness adapter (LiteLLM proxy, ADR-002 §6)."""

    base_url: str
    api_key: str
    model: str

    @classmethod
    def missing_env_vars(cls, env: Mapping[str, str] | None = None) -> tuple[str, ...]:
        """Names of the required env vars that are unset or blank, in a stable order."""
        source = _env(env)
        return tuple(name for name in _REQUIRED_ENV_VARS if not (source.get(name) or "").strip())

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "HarnessConfig | None":
        """Config read from ``env`` (default: the process environment); ``None`` when incomplete."""
        source = _env(env)
        if cls.missing_env_vars(env):
            return None
        return cls(
            base_url=(source.get(LLM_BASE_URL_ENV_VAR) or "").strip(),
            api_key=(source.get(LLM_API_KEY_ENV_VAR) or "").strip(),
            model=(source.get(LLM_MODEL_ENV_VAR) or "").strip(),
        )
