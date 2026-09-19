"""SQLAlchemy ORM models of the factory operational state (ADR-004/006/016, T-006).

Authoritative operational state lives in PostgreSQL (ADR-004 p.1). Two schema
invariants are enforced here and covered by integration tests:

- ``stage.operation_key`` is unique: exactly one logical operation per
  ``(execution_id, stage, input_revision)``. Retries do not create a new logical
  operation, they append rows to ``attempt`` (ADR-006 p.3).
- ``effect_ledger.effect_key`` is unique: exactly one external effect per key
  (ADR-006 p.3), the basis of effectively-once side effects.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any, Final

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from dark_factory.changes.clock import utc_now
from dark_factory.changes.enums import (
    ChangeSource,
    DecisionOutcome,
    DecisionSource,
    Gate,
    ProductStatus,
    Provider,
    RiskClass,
    Route,
    RunStatus,
    StageStatus,
)
from dark_factory.changes.enums import Stage as StageEnum
from dark_factory.changes.run import _RESULT_STATUSES
from dark_factory.orchestration.state.base import Base
from dark_factory.orchestration.state.enums import DeliveryStatus, EffectStatus


def _values(enum_cls: type[Any]) -> str:
    """Render an enum's wire values as a SQL ``IN`` list (single source of truth)."""
    return ", ".join(f"'{member.value}'" for member in enum_cls)


ROUTE_VALUES: Final = _values(Route)
PROVIDER_VALUES: Final = _values(Provider)
RUN_STATUS_VALUES: Final = _values(RunStatus)
STAGE_VALUES: Final = _values(StageEnum)
STAGE_STATUS_VALUES: Final = _values(StageStatus)
EFFECT_STATUS_VALUES: Final = _values(EffectStatus)
DELIVERY_STATUS_VALUES: Final = _values(DeliveryStatus)
CHANGE_SOURCE_VALUES: Final = _values(ChangeSource)
PRODUCT_STATUS_VALUES: Final = _values(ProductStatus)
RISK_CLASS_VALUES: Final = _values(RiskClass)
GATE_VALUES: Final = _values(Gate)
DECISION_OUTCOME_VALUES: Final = _values(DecisionOutcome)
DECISION_SOURCE_VALUES: Final = _values(DecisionSource)
RESULT_STATUS_VALUES: Final = ", ".join(
    f"'{member.value}'" for member in StageStatus if member in _RESULT_STATUSES
)


class Execution(Base):
    """Logical execution of one change through the factory.

    ``budget`` and ``implementation_contract`` hold the JSON dumps of the run's
    :class:`~dark_factory.changes.usage.BudgetSnapshot` and approved
    :class:`~dark_factory.changes.implementation_contract.ImplementationContract`
    (T-092): without them a replayed run would lose its rework counters, its
    accumulated spend and its approved implementation boundary. ``NULL`` means
    "not set" — the default snapshot and an unapproved contract.
    """

    __tablename__ = "execution"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    change_id: Mapped[str] = mapped_column(String(128), index=True)
    route: Mapped[str] = mapped_column(String(16))
    provider: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default=RunStatus.PENDING.value)
    state_revision: Mapped[int] = mapped_column(Integer, default=1)
    budget: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    implementation_contract: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(f"route IN ({ROUTE_VALUES})", name="route_allowed"),
        CheckConstraint(f"provider IN ({PROVIDER_VALUES})", name="provider_allowed"),
        CheckConstraint(f"status IN ({RUN_STATUS_VALUES})", name="status_allowed"),
        CheckConstraint("state_revision >= 1", name="state_revision_positive"),
    )


class Stage(Base):
    """Logical operation of one stage of an execution.

    ``operation_key`` encodes ``(execution_id, stage, input_revision)`` and is
    unique: a retry of the same input revision reuses this row (ADR-006 p.3).
    """

    __tablename__ = "stage"

    id: Mapped[str] = mapped_column(String(256), primary_key=True)
    execution_id: Mapped[str] = mapped_column(
        ForeignKey("execution.id", ondelete="CASCADE"), index=True
    )
    stage: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default=StageStatus.PENDING.value)
    input_revision: Mapped[str] = mapped_column(String(128))
    operation_key: Mapped[str] = mapped_column(String(512), unique=True)
    state_revision: Mapped[int] = mapped_column(Integer, default=1)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(f"stage IN ({STAGE_VALUES})", name="stage_allowed"),
        CheckConstraint(f"status IN ({STAGE_STATUS_VALUES})", name="status_allowed"),
        CheckConstraint("state_revision >= 1", name="state_revision_positive"),
        CheckConstraint("attempt_count >= 0", name="attempt_count_non_negative"),
    )


class Attempt(Base):
    """Physical attempt of a logical stage operation (``attempt_id`` primary key)."""

    __tablename__ = "attempt"

    id: Mapped[str] = mapped_column(String(640), primary_key=True)
    operation_key: Mapped[str] = mapped_column(String(512), index=True)
    stage_id: Mapped[str] = mapped_column(ForeignKey("stage.id", ondelete="CASCADE"), index=True)
    attempt_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default=StageStatus.IN_PROGRESS.value)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("stage_id", "attempt_number", name="stage_number"),
        CheckConstraint(f"status IN ({STAGE_STATUS_VALUES})", name="status_allowed"),
        CheckConstraint("attempt_number >= 1", name="attempt_number_positive"),
    )


class ExecutionLease(Base):
    """Lease with a monotonic fencing token (ADR-006 p.6).

    Kubernetes ``concurrencyPolicy: Forbid`` is only an optimisation; correctness
    comes from this row: a writer whose ``fencing_token`` is stale is rejected.
    """

    __tablename__ = "execution_leases"

    resource_type: Mapped[str] = mapped_column(String(64), primary_key=True)
    resource_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(128))
    fencing_token: Mapped[int] = mapped_column(Integer)
    acquired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )

    __table_args__ = (CheckConstraint("fencing_token >= 1", name="fencing_token_positive"),)


class OutboxEvent(Base):
    """Versioned event envelope of the transactional outbox (ADR-016 p.3)."""

    __tablename__ = "outbox"

    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    event_version: Mapped[int] = mapped_column(Integer, default=1)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    change_id: Mapped[str] = mapped_column(String(128), index=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    stage: Mapped[str | None] = mapped_column(String(32))
    aggregate_id: Mapped[str] = mapped_column(String(128))
    aggregate_version: Mapped[int] = mapped_column(Integer)
    correlation_id: Mapped[str | None] = mapped_column(String(128))
    causation_id: Mapped[str | None] = mapped_column(String(128))
    artifact_refs: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    sequence: Mapped[int] = mapped_column(Integer)

    __table_args__ = (
        UniqueConstraint("aggregate_id", "sequence", name="aggregate_sequence"),
        CheckConstraint("event_version >= 1", name="event_version_positive"),
        CheckConstraint("aggregate_version >= 1", name="aggregate_version_positive"),
        CheckConstraint("sequence >= 1", name="sequence_positive"),
        Index("ix_outbox_run_id_sequence", "run_id", "sequence"),
        Index("ix_outbox_change_id_sequence", "change_id", "sequence"),
    )


class EventDelivery(Base):
    """Per-consumer delivery state of one outbox event (ADR-016 p.5)."""

    __tablename__ = "event_delivery"

    event_id: Mapped[str] = mapped_column(
        ForeignKey("outbox.event_id", ondelete="CASCADE"), primary_key=True
    )
    consumer_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default=DeliveryStatus.PENDING.value)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(1024))

    __table_args__ = (
        CheckConstraint(f"status IN ({DELIVERY_STATUS_VALUES})", name="status_allowed"),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
    )


class EffectLedgerEntry(Base):
    """Durable ledger of external side effects (ADR-006 p.3)."""

    __tablename__ = "effect_ledger"

    effect_key: Mapped[str] = mapped_column(String(1024), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default=EffectStatus.PLANNED.value)
    external_ref: Mapped[str | None] = mapped_column(String(1024))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(f"status IN ({EFFECT_STATUS_VALUES})", name="status_allowed"),
    )


class UsageRecord(Base):
    """Usage aggregate per execution/attempt (FR-018/FR-024, SC-003)."""

    __tablename__ = "usage"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    execution_id: Mapped[str] = mapped_column(
        ForeignKey("execution.id", ondelete="CASCADE"), index=True
    )
    attempt_id: Mapped[str | None] = mapped_column(String(640))
    stage: Mapped[str | None] = mapped_column(String(32))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    rework_rounds: Mapped[int] = mapped_column(Integer, default=0)
    manual_interventions: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("prompt_tokens >= 0", name="prompt_tokens_non_negative"),
        CheckConstraint("completion_tokens >= 0", name="completion_tokens_non_negative"),
        CheckConstraint("rework_rounds >= 0", name="rework_rounds_non_negative"),
        CheckConstraint("manual_interventions >= 0", name="manual_interventions_non_negative"),
    )


class Product(Base):
    """Product registry entry: a product and its repository readiness (ADR-030, T065).

    ``payload`` carries the full Product document; ``repository``, ``status``
    and ``status_reason`` are denormalized for querying. ``state_revision``
    guards concurrent status writers (ADR-006 p.4).
    """

    __tablename__ = "product"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(512))
    description: Mapped[str | None] = mapped_column(Text)
    repository: Mapped[dict[str, Any]] = mapped_column(JSONB)
    repository_url: Mapped[str | None] = mapped_column(String(512))
    baseline_ref: Mapped[str | None] = mapped_column(String(512))
    dev_env_ref: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(16), default=ProductStatus.CREATED.value)
    status_reason: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    state_revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(f"status IN ({PRODUCT_STATUS_VALUES})", name="status_allowed"),
        CheckConstraint("state_revision >= 1", name="state_revision_positive"),
    )


class Change(Base):
    """Intake record of a change (FR-001, FR-017, ADR-009 p.7).

    ``payload`` carries the full Change document; ``product`` is denormalized
    for querying. ``external_ref`` is the tracker dedup key: at most one change
    per external reference (partial unique index). ``product_id`` is the
    optional owning product (ADR-030 p.2): ``NULL`` for pre-T065 changes.
    """

    __tablename__ = "change"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    title: Mapped[str] = mapped_column(String(512))
    description: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(32))
    external_ref: Mapped[str | None] = mapped_column(String(512))
    risk_class: Mapped[str] = mapped_column(String(8))
    product: Mapped[dict[str, Any]] = mapped_column(JSONB)
    product_id: Mapped[str | None] = mapped_column(String(128), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    state_revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(f"source IN ({CHANGE_SOURCE_VALUES})", name="source_allowed"),
        CheckConstraint(f"risk_class IN ({RISK_CLASS_VALUES})", name="risk_class_allowed"),
        CheckConstraint("state_revision >= 1", name="state_revision_positive"),
        Index(
            "uq_change_external_ref",
            "external_ref",
            unique=True,
            postgresql_where=text("external_ref IS NOT NULL"),
        ),
    )


class Decision(Base):
    """Approval decision over a change, version-bound to ``commit_sha`` (ADR-009 p.7).

    ``role`` holds the API role of the caller (``operator``/``service``); it is
    not an agent role (ADR-007), so no CHECK constraint narrows it.
    """

    __tablename__ = "decision"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    change_id: Mapped[str] = mapped_column(ForeignKey("change.id", ondelete="CASCADE"), index=True)
    gate: Mapped[str] = mapped_column(String(32))
    outcome: Mapped[str] = mapped_column(String(16))
    decided_by: Mapped[str] = mapped_column(String(16))
    role: Mapped[str | None] = mapped_column(String(32))
    commit_sha: Mapped[str | None] = mapped_column(String(128))
    comment: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    evidence_ids: Mapped[list[Any]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(128))

    __table_args__ = (
        CheckConstraint(f"gate IN ({GATE_VALUES})", name="gate_allowed"),
        CheckConstraint(f"outcome IN ({DECISION_OUTCOME_VALUES})", name="outcome_allowed"),
        CheckConstraint(f"decided_by IN ({DECISION_SOURCE_VALUES})", name="decided_by_allowed"),
        Index(
            "uq_decision_idempotency_key",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
    )


class StageResult(Base):
    """Durable store of one immutable StageResult document (ADR-006 p.4).

    The primary key is the ``attempt_id`` (ADR-006 p.3), so recording the same
    attempt twice is a no-op and earlier attempts are never overwritten (FR-014).
    """

    __tablename__ = "stage_result"

    id: Mapped[str] = mapped_column(String(768), primary_key=True)
    operation_key: Mapped[str] = mapped_column(String(512), index=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    change_id: Mapped[str] = mapped_column(String(128), index=True)
    stage: Mapped[str] = mapped_column(String(32))
    attempt_number: Mapped[int] = mapped_column(Integer)
    input_revision: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16))
    produced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)

    __table_args__ = (
        CheckConstraint(f"stage IN ({STAGE_VALUES})", name="stage_allowed"),
        CheckConstraint(f"status IN ({RESULT_STATUS_VALUES})", name="status_allowed"),
        CheckConstraint("attempt_number >= 1", name="attempt_number_positive"),
    )


class AuditLogEntry(Base):
    """Audit record of one mutating API operation (ADR-009 p.7).

    Never carries secrets: ``details`` holds non-sensitive context only.
    """

    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    actor: Mapped[str] = mapped_column(String(128))
    role: Mapped[str | None] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(64))
    resource_type: Mapped[str] = mapped_column(String(32))
    resource_id: Mapped[str] = mapped_column(String(128))
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    outcome: Mapped[str] = mapped_column(String(16))
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )


ALL_MODELS: Final[tuple[type[Base], ...]] = (
    Execution,
    Stage,
    Attempt,
    ExecutionLease,
    OutboxEvent,
    EventDelivery,
    EffectLedgerEntry,
    UsageRecord,
    Product,
    Change,
    Decision,
    StageResult,
    AuditLogEntry,
)
