"""Unit tests of the Settings contract (hermetic: environment only)."""

from collections.abc import Iterator

import pytest
from pydantic import ValidationError

from app.config import Settings


@pytest.fixture(autouse=True)
def no_dotenv(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep the tests hermetic against a developer's local backend/.env.

    ``Settings.model_config`` points at ``.env``; a developer's file would leak
    values into every assertion below. mypy sees the model's synthesized
    ``__init__`` (fields only), so per-call ``_env_file=None`` is not an option:
    isolation switches the class-level config instead. ``database_url`` is a
    required field, so mypy reads a no-arg ``Settings()`` as a missing
    argument, while pydantic-settings sources it from the environment at
    runtime — hence the ``call-arg`` suppressions below.
    """
    monkeypatch.setattr(Settings, "model_config", {**Settings.model_config, "env_file": None})
    yield


def test_settings_reads_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_NAME", "custom-name")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/db")
    settings = Settings()  # type: ignore[call-arg]
    assert settings.app_name == "custom-name"
    assert settings.database_url == "postgresql+psycopg://u:p@localhost:5432/db"


def test_settings_defaults_app_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/db")
    monkeypatch.delenv("APP_NAME", raising=False)
    settings = Settings()  # type: ignore[call-arg]
    assert settings.app_name == "example-product"


def test_settings_require_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]
