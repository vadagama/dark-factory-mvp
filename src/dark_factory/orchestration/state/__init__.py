"""Operational state store: PostgreSQL schema, migrations and repositories (T-006)."""

from dark_factory.orchestration.state.base import Base
from dark_factory.orchestration.state.engine import (
    DEFAULT_DATABASE_URL,
    create_session_factory,
    create_state_engine,
    session_scope,
)
from dark_factory.orchestration.state.enums import DeliveryStatus, EffectStatus
from dark_factory.orchestration.state.models import (
    Attempt,
    EffectLedgerEntry,
    EventDelivery,
    Execution,
    ExecutionLease,
    OutboxEvent,
    UsageRecord,
)
from dark_factory.orchestration.state.models import Stage as StageRow
from dark_factory.orchestration.state.repositories import (
    EffectLedger,
    ExecutionRepository,
    LeaseLostError,
    LeaseRepository,
    OutboxRepository,
    StaleFencingTokenError,
    StateConflictError,
    StateError,
    ensure_effect,
)

__all__ = [
    "DEFAULT_DATABASE_URL",
    "Attempt",
    "Base",
    "DeliveryStatus",
    "EffectLedger",
    "EffectLedgerEntry",
    "EffectStatus",
    "EventDelivery",
    "Execution",
    "ExecutionLease",
    "ExecutionRepository",
    "LeaseLostError",
    "LeaseRepository",
    "OutboxEvent",
    "OutboxRepository",
    "StageRow",
    "StaleFencingTokenError",
    "StateConflictError",
    "StateError",
    "UsageRecord",
    "create_session_factory",
    "create_state_engine",
    "ensure_effect",
    "session_scope",
]
