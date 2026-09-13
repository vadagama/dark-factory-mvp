"""Declarative base and metadata for the factory operational state (ADR-004, T-006).

The operational state is PostgreSQL-only and authoritative (ADR-004 p.1); it is
not a wire contract, so it may evolve through Alembic migrations. A stable
naming convention keeps generated constraint names deterministic and reviewable
(Alembic ``expand/contract``, ADR-010 p.6).
"""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base class of the operational-state ORM models."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
