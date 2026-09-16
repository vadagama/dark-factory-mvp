"""Unit tests of the Settings contract (hermetic: environment only)."""

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_settings_reads_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_NAME", "custom-name")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/db")
    settings = Settings(_env_file=None)
    assert settings.app_name == "custom-name"
    assert settings.database_url == "postgresql+psycopg://u:p@localhost:5432/db"


def test_settings_defaults_app_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/db")
    monkeypatch.delenv("APP_NAME", raising=False)
    settings = Settings(_env_file=None)
    assert settings.app_name == "example-product"


def test_settings_require_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    # _env_file=None keeps the test hermetic against a developer's local .env.
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
