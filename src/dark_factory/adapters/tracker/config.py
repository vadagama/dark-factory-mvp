"""Environment configuration of the Plane tracker adapter (T-033, ADR-013).

The adapter talks to a self-hosted Plane instance over its REST API; every
setting comes from the environment, secrets never appear in code, commits or
output (ADR-009). Missing configuration never breaks import or construction —
``from_env`` returns ``None`` and the wiring reports the gap lazily, exactly
like the GitHub and harness adapters.

Environment variables:

- ``DARK_FACTORY_PLANE_BASE_URL`` — Plane web root, e.g. ``http://plane.local``
  (the adapter appends ``/api/v1/...`` itself).
- ``DARK_FACTORY_PLANE_API_KEY`` — Plane API token of the automation user
  (a secret, never logged).
- ``DARK_FACTORY_PLANE_WORKSPACE_SLUG`` — slug of the pilot workspace.
- ``DARK_FACTORY_PLANE_PROJECT_ID`` — id of the project factory issues live in.
- ``DARK_FACTORY_PLANE_REPOSITORY_SLUG`` — ``owner/repo`` of the product
  repository the tracker tasks belong to; it becomes ``Change.product``.
- ``DARK_FACTORY_PLANE_REPOSITORY_PROVIDER`` — ``github`` (default) or
  ``gitlab``; an unknown name is a configuration error, not a silent default.
- ``DARK_FACTORY_PLANE_WEBHOOK_SECRET`` — current webhook signing secret
  (optional; a secret).
- ``DARK_FACTORY_PLANE_WEBHOOK_SECRET_PREVIOUS`` — previous signing secret kept
  during rotation (optional; a secret, ADR-013 p.6).
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final

from dark_factory.adapters._env import missing_env_vars, resolve_env
from dark_factory.ports import Provider, RepositoryRef

PLANE_BASE_URL_ENV_VAR: Final[str] = "DARK_FACTORY_PLANE_BASE_URL"
"""Plane web root the API paths are resolved against."""

PLANE_API_KEY_ENV_VAR: Final[str] = "DARK_FACTORY_PLANE_API_KEY"
"""Plane API token; a secret, never included in output (ADR-009)."""

PLANE_WORKSPACE_SLUG_ENV_VAR: Final[str] = "DARK_FACTORY_PLANE_WORKSPACE_SLUG"
"""Slug of the workspace the factory issues belong to."""

PLANE_PROJECT_ID_ENV_VAR: Final[str] = "DARK_FACTORY_PLANE_PROJECT_ID"
"""Id of the project the factory issues belong to."""

PLANE_REPOSITORY_SLUG_ENV_VAR: Final[str] = "DARK_FACTORY_PLANE_REPOSITORY_SLUG"
"""Product repository of the tracked tasks (``Change.product``)."""

PLANE_REPOSITORY_PROVIDER_ENV_VAR: Final[str] = "DARK_FACTORY_PLANE_REPOSITORY_PROVIDER"
"""Source-control provider of that repository (``github`` or ``gitlab``)."""

PLANE_WEBHOOK_SECRET_ENV_VAR: Final[str] = "DARK_FACTORY_PLANE_WEBHOOK_SECRET"
"""Current webhook signing secret; a secret, never included in output."""

PLANE_WEBHOOK_SECRET_PREVIOUS_ENV_VAR: Final[str] = "DARK_FACTORY_PLANE_WEBHOOK_SECRET_PREVIOUS"
"""Previous signing secret kept active during rotation; a secret."""

_REQUIRED_ENV_VARS: Final[tuple[str, ...]] = (
    PLANE_BASE_URL_ENV_VAR,
    PLANE_API_KEY_ENV_VAR,
    PLANE_WORKSPACE_SLUG_ENV_VAR,
    PLANE_PROJECT_ID_ENV_VAR,
    PLANE_REPOSITORY_SLUG_ENV_VAR,
)

DEFAULT_REPOSITORY_PROVIDER: Final[Provider] = Provider.GITHUB
"""Provider assumed when ``DARK_FACTORY_PLANE_REPOSITORY_PROVIDER`` is unset."""


def _parse_provider(value: str) -> Provider:
    """``Provider`` for a configured name; an unknown name is a configuration error."""
    try:
        return Provider(value.strip().lower())
    except ValueError as error:
        known = ", ".join(sorted(provider.value for provider in Provider))
        raise ValueError(f"{PLANE_REPOSITORY_PROVIDER_ENV_VAR} must be one of: {known}") from error


@dataclass(frozen=True, slots=True)
class PlaneConfig:
    """Endpoint, credentials and repository binding of the Plane tracker adapter.

    ``api_key`` and the webhook secrets are secrets (ADR-009) and stay out of
    ``repr``, so a stray log line or a failing assertion cannot print them.
    """

    base_url: str
    workspace_slug: str
    project_id: str
    repository: RepositoryRef
    api_key: str = field(repr=False)
    webhook_secret: str | None = field(default=None, repr=False)
    previous_webhook_secret: str | None = field(default=None, repr=False)

    def active_webhook_secrets(self) -> tuple[str, ...]:
        """Signing secrets accepted now: the current one first, the previous one during rotation."""
        return tuple(
            secret for secret in (self.webhook_secret, self.previous_webhook_secret) if secret
        )

    @classmethod
    def missing_env_vars(cls, env: Mapping[str, str] | None = None) -> tuple[str, ...]:
        """Names of the required env vars that are unset or blank, in a stable order."""
        return missing_env_vars(_REQUIRED_ENV_VARS, env)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "PlaneConfig | None":
        """Config read from ``env`` (default: the process environment); ``None`` when incomplete."""
        source = resolve_env(env)
        if cls.missing_env_vars(env):
            return None
        provider_name = (source.get(PLANE_REPOSITORY_PROVIDER_ENV_VAR) or "").strip()
        return cls(
            base_url=(source.get(PLANE_BASE_URL_ENV_VAR) or "").strip(),
            workspace_slug=(source.get(PLANE_WORKSPACE_SLUG_ENV_VAR) or "").strip(),
            project_id=(source.get(PLANE_PROJECT_ID_ENV_VAR) or "").strip(),
            repository=RepositoryRef(
                provider=(
                    DEFAULT_REPOSITORY_PROVIDER
                    if not provider_name
                    else _parse_provider(provider_name)
                ),
                slug=(source.get(PLANE_REPOSITORY_SLUG_ENV_VAR) or "").strip(),
            ),
            api_key=source.get(PLANE_API_KEY_ENV_VAR) or "",
            webhook_secret=(source.get(PLANE_WEBHOOK_SECRET_ENV_VAR) or "").strip() or None,
            previous_webhook_secret=(
                (source.get(PLANE_WEBHOOK_SECRET_PREVIOUS_ENV_VAR) or "").strip() or None
            ),
        )
