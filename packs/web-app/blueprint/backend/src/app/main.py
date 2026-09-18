"""Application factory of the example product API.

The module imports cleanly without ``DATABASE_URL``: the settings are strict
and validated only when ``create_app`` is called, so unit tests and tooling
can import the module hermetically. Uvicorn runs the factory directly:
``uvicorn app.main:create_app --factory``.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import health
from app.config import Settings
from app.db import create_engine, create_sessionmaker


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the app with its own engine and sessionmaker (isolated per app)."""
    # The URL arrives through the environment (Helm existingSecret), never as a
    # literal: mypy sees only the dataclass_transform field signature, so the
    # env-sourced construction reads as a missing argument to it.
    settings = settings or Settings()  # type: ignore[call-arg]  # fail fast: no safe default
    engine = create_engine(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        await engine.dispose()

    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.state.sessionmaker = create_sessionmaker(engine)
    app.include_router(health.router)
    return app
