"""Database wiring: declarative base, async engine and session factories.

The engine is created per application instance (``app.main.create_app``), so
tests can spin isolated apps; module-level globals would share one engine
across the whole test session.
"""

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import Settings


class Base(DeclarativeBase):
    """Declarative base for the product models (``app.models`` registers tables)."""


def create_engine(settings: Settings) -> AsyncEngine:
    """Async engine for the configured PostgreSQL (psycopg driver)."""
    return create_async_engine(settings.database_url, pool_pre_ping=True)


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
