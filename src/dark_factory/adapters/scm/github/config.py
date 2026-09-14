"""Environment configuration of the GitHub adapter (T-024, ADR-019 p.3).

Authentication is GitHub App based: short-lived installation tokens with
minimal permissions (ADR-019 p.3); long-lived PATs never reach the core or the
agents. Secrets come from configuration/environment only and are never
reported back (ADR-009). Missing configuration never breaks import or
construction — ``from_env`` returns ``None`` and the wiring reports the gap
lazily (the harness adapter follows the same pattern).

Environment variables:

- ``DARK_FACTORY_GITHUB_APP_ID`` — numeric GitHub App id.
- ``DARK_FACTORY_GITHUB_APP_PRIVATE_KEY`` — PEM text of the App private key;
  a secret, never logged (ADR-009).
- ``DARK_FACTORY_GITHUB_INSTALLATION_ID`` — installation id of the App on the
  target repositories.
- ``DARK_FACTORY_GITHUB_API_URL`` — REST API base URL (optional; the default
  is ``https://api.github.com``, GHES sets its own).
- ``DARK_FACTORY_GITHUB_WORKFLOW_ID`` — workflow the CI port dispatches
  (optional; the default is ``factory.yml``, T-031 templates).
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

GITHUB_API_URL_ENV_VAR: Final[str] = "DARK_FACTORY_GITHUB_API_URL"
"""REST API base URL (GHES overrides the default ``https://api.github.com``)."""

GITHUB_APP_ID_ENV_VAR: Final[str] = "DARK_FACTORY_GITHUB_APP_ID"
"""Numeric id of the GitHub App (ADR-019 p.3)."""

GITHUB_APP_PRIVATE_KEY_ENV_VAR: Final[str] = "DARK_FACTORY_GITHUB_APP_PRIVATE_KEY"
"""PEM text of the App private key; a secret, never included in output (ADR-009)."""

GITHUB_INSTALLATION_ID_ENV_VAR: Final[str] = "DARK_FACTORY_GITHUB_INSTALLATION_ID"
"""Installation id of the App on the target repositories."""

GITHUB_WORKFLOW_ID_ENV_VAR: Final[str] = "DARK_FACTORY_GITHUB_WORKFLOW_ID"
"""Workflow the CI port dispatches (default ``factory.yml``, T-031 templates)."""

_REQUIRED_ENV_VARS: Final[tuple[str, ...]] = (
    GITHUB_APP_ID_ENV_VAR,
    GITHUB_APP_PRIVATE_KEY_ENV_VAR,
    GITHUB_INSTALLATION_ID_ENV_VAR,
)

DEFAULT_API_BASE_URL: Final[str] = "https://api.github.com"
DEFAULT_WORKFLOW_ID: Final[str] = "factory.yml"


def _env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    """The given mapping or, by default, the process environment."""
    return os.environ if env is None else env


@dataclass(frozen=True, slots=True)
class GitHubConfig:
    """Endpoint and App configuration of the GitHub adapter (ADR-019 p.3)."""

    api_base_url: str = DEFAULT_API_BASE_URL
    app_id: str | None = None
    installation_id: str | None = None
    private_key: str | None = None
    workflow_id: str = DEFAULT_WORKFLOW_ID

    @classmethod
    def missing_env_vars(cls, env: Mapping[str, str] | None = None) -> tuple[str, ...]:
        """Names of the required env vars that are unset or blank, in a stable order."""
        source = _env(env)
        return tuple(name for name in _REQUIRED_ENV_VARS if not (source.get(name) or "").strip())

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "GitHubConfig | None":
        """Config read from ``env`` (default: the process environment); ``None`` when incomplete."""
        source = _env(env)
        if cls.missing_env_vars(env):
            return None
        return cls(
            api_base_url=(source.get(GITHUB_API_URL_ENV_VAR) or DEFAULT_API_BASE_URL).strip(),
            app_id=(source.get(GITHUB_APP_ID_ENV_VAR) or "").strip(),
            installation_id=(source.get(GITHUB_INSTALLATION_ID_ENV_VAR) or "").strip(),
            private_key=source.get(GITHUB_APP_PRIVATE_KEY_ENV_VAR) or "",
            workflow_id=(source.get(GITHUB_WORKFLOW_ID_ENV_VAR) or DEFAULT_WORKFLOW_ID).strip(),
        )
