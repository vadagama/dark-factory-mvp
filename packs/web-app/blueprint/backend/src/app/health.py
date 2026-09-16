"""Health endpoints: liveness (no database) and readiness (database ping).

The split follows the factory chart contract (charts/dark-factory probes):
liveness must not restart the pod on a database outage, readiness returns 503
while PostgreSQL does not answer.
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app

router = APIRouter(tags=["health"])


class AppInfo(BaseModel):
    app: str
    version: str


class HealthResponse(BaseModel):
    status: str
    database: str


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a session bound to the app's engine."""
    sessionmaker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    async with sessionmaker() as session:
        yield session


@router.get("/", response_model=AppInfo)
async def index(request: Request) -> AppInfo:
    """Liveness: process metadata without touching the database."""
    return AppInfo(app=request.app.title, version=app.__version__)


@router.get("/api/healthz", response_model=HealthResponse)
async def healthz(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HealthResponse:
    """Readiness: 200 only while PostgreSQL answers ``SELECT 1``."""
    try:
        await session.execute(text("SELECT 1"))
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="database unavailable") from error
    return HealthResponse(status="ok", database="ok")
