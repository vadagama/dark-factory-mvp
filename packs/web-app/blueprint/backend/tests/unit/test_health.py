"""Unit tests of the health endpoints (hermetic: no database, fake session)."""

import asyncio
from collections.abc import AsyncIterator
from typing import Any, cast

import httpx
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

import app
from app.config import Settings
from app.health import get_session
from app.main import create_app


class _StubSession:
    """Minimal stand-in for AsyncSession: the endpoint only calls execute()."""

    def __init__(self, fail: bool) -> None:
        self._fail = fail

    async def execute(self, statement: Any) -> None:
        if self._fail:
            raise SQLAlchemyError("database is down")
        return None


def _settings() -> Settings:
    # The engine is created lazily and never connects in the unit tests.
    return Settings(database_url="postgresql+psycopg://stub:stub@localhost:5432/stub")


def _override_session(app: Any, fail: bool) -> None:
    async def fake_session() -> AsyncIterator[AsyncSession]:
        yield cast(AsyncSession, _StubSession(fail))

    app.dependency_overrides[get_session] = fake_session


def test_healthz_reports_ok_without_a_real_database() -> None:
    application = create_app(_settings())
    _override_session(application, fail=False)

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/healthz")
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "database": "ok"}

    asyncio.run(scenario())


def test_healthz_returns_503_when_the_database_fails() -> None:
    application = create_app(_settings())
    _override_session(application, fail=True)

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/healthz")
        assert response.status_code == 503

    asyncio.run(scenario())


def test_root_reports_app_metadata_without_the_database() -> None:
    application = create_app(
        Settings(app_name="example-product", database_url=_settings().database_url)
    )

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/")
        assert response.status_code == 200
        assert response.json() == {"app": "example-product", "version": app.__version__}

    asyncio.run(scenario())
