"""Integration: the app against a real PostgreSQL (skipped without a DSN).

Enabled by ``APP_TEST_DATABASE_URL`` — the CI service container sets it
(see .github/workflows/ci.yml); without it the tests skip so the rest of the
suite stays green on machines without a database.
"""

import asyncio
import os

import httpx
import pytest

from app.config import Settings
from app.main import create_app

DATABASE_URL_ENV = "APP_TEST_DATABASE_URL"

pytestmark = pytest.mark.skipif(
    not os.environ.get(DATABASE_URL_ENV),
    reason=f"{DATABASE_URL_ENV} is not set (no PostgreSQL for integration tests)",
)


def test_healthz_answers_with_a_real_database() -> None:
    settings = Settings(database_url=os.environ[DATABASE_URL_ENV])
    application = create_app(settings)

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/healthz")
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "database": "ok"}

    asyncio.run(scenario())


def test_root_answers_with_a_real_database_configured() -> None:
    settings = Settings(database_url=os.environ[DATABASE_URL_ENV])
    application = create_app(settings)

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/")
        assert response.status_code == 200

    asyncio.run(scenario())
