"""PostgreSQL-backed fixtures for the state-store integration tests (T-006).

Integration tests need a real PostgreSQL server (ADR-004) and are therefore
skipped unless ``DARK_FACTORY_TEST_DATABASE_URL`` points at one. The schema is
created through the real Alembic migration so the migration itself is exercised.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from dark_factory.orchestration.state.engine import (
    create_session_factory,
    create_state_engine,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL_ENV = "DARK_FACTORY_TEST_DATABASE_URL"

# Child tables first; RESTART IDENTITY keeps sequences stable between tests.
_TABLES = (
    "audit_log, decision, stage_result, event_delivery, outbox, attempt, stage, usage, "
    "execution_leases, execution, effect_ledger, change, product"
)


def _test_database_url() -> str:
    url = os.environ.get(TEST_DATABASE_URL_ENV)
    if not url:
        pytest.skip(f"{TEST_DATABASE_URL_ENV} is not set; skipping PostgreSQL integration tests")
    return url


def alembic_config(database_url: str) -> Config:
    """Alembic config of the test database; shared by the schema and reversibility tests.

    ``ConfigParser`` interpolates ``%``, so a URL-encoded password (``%2F``) must
    be escaped or the option parser rejects the URL.
    """
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def _upgrade(database_url: str) -> None:
    command.upgrade(alembic_config(database_url), "head")


@pytest.fixture(scope="session")
def state_engine() -> Iterator[Engine]:
    database_url = _test_database_url()
    engine = create_state_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    _upgrade(database_url)
    yield engine
    engine.dispose()


@pytest.fixture
def session_factory(state_engine: Engine) -> sessionmaker[Session]:
    with state_engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))
    return create_session_factory(state_engine)
