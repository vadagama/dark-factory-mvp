"""Application factory of the example product API."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import health
from app.config import Settings
from app.db import create_engine, create_sessionmaker


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the app with its own engine and sessionmaker (isolated per app)."""
    settings = settings or Settings()
    engine = create_engine(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        await engine.dispose()

    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.state.sessionmaker = create_sessionmaker(engine)
    app.include_router(health.router)
    return app


app = create_app()
