"""Product configuration loaded from the environment (12-factor).

Secrets and environment-specific values never live in the repository
(ADR-009/ADR-015): ``.env.example`` documents every variable the app reads,
and real values arrive through the environment (``DATABASE_URL`` comes from
the existingSecret in the Helm chart).
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "example-product"
    # Required: no safe default exists; fail fast on a misconfigured deploy.
    database_url: str
