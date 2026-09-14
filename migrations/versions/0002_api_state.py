"""API state: change intake, decisions, stage results and audit log (T035, ADR-009 p.7)

Revision ID: 0002_api_state
Revises: 0001_state_schema
Create Date: 2026-09-14

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002_api_state"
down_revision: str | Sequence[str] | None = "0001_state_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "change",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("external_ref", sa.String(length=512), nullable=True),
        sa.Column("risk_class", sa.String(length=8), nullable=False),
        sa.Column("product", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("state_revision", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "risk_class IN ('R0', 'R1', 'R2', 'R3', 'R4')",
            name=op.f("ck_change_risk_class_allowed"),
        ),
        sa.CheckConstraint(
            "source IN ('tracker', 'console', 'cli', 'api')", name=op.f("ck_change_source_allowed")
        ),
        sa.CheckConstraint("state_revision >= 1", name=op.f("ck_change_state_revision_positive")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_change")),
    )
    op.create_index(
        "uq_change_external_ref",
        "change",
        ["external_ref"],
        unique=True,
        postgresql_where=sa.text("external_ref IS NOT NULL"),
    )
    op.create_table(
        "decision",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("change_id", sa.String(length=128), nullable=False),
        sa.Column("gate", sa.String(length=32), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("decided_by", sa.String(length=16), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=True),
        sa.Column("commit_sha", sa.String(length=128), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "evidence_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.CheckConstraint(
            "decided_by IN ('human', 'policy', 'agent')",
            name=op.f("ck_decision_decided_by_allowed"),
        ),
        sa.CheckConstraint(
            "gate IN ('specification', 'planning', 'code', 'ui', 'review', 'verification', 'release')",
            name=op.f("ck_decision_gate_allowed"),
        ),
        sa.CheckConstraint(
            "outcome IN ('approved', 'rejected', 'waived')",
            name=op.f("ck_decision_outcome_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["change_id"],
            ["change.id"],
            name=op.f("fk_decision_change_id_change"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_decision")),
    )
    op.create_index(op.f("ix_decision_change_id"), "decision", ["change_id"], unique=False)
    op.create_index(
        "uq_decision_idempotency_key",
        "decision",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_table(
        "stage_result",
        sa.Column("id", sa.String(length=768), nullable=False),
        sa.Column("operation_key", sa.String(length=512), nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("change_id", sa.String(length=128), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("input_revision", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("produced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint(
            "attempt_number >= 1", name=op.f("ck_stage_result_attempt_number_positive")
        ),
        sa.CheckConstraint(
            "stage IN ('specification', 'planning', 'construction', 'review_verification', 'release')",
            name=op.f("ck_stage_result_stage_allowed"),
        ),
        sa.CheckConstraint(
            "status IN ('waiting', 'succeeded', 'failed', 'blocked')",
            name=op.f("ck_stage_result_status_allowed"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stage_result")),
    )
    op.create_index(op.f("ix_stage_result_change_id"), "stage_result", ["change_id"], unique=False)
    op.create_index(
        op.f("ix_stage_result_operation_key"), "stage_result", ["operation_key"], unique=False
    )
    op.create_index(op.f("ix_stage_result_run_id"), "stage_result", ["run_id"], unique=False)
    op.create_table(
        "audit_log",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("resource_type", sa.String(length=32), nullable=False),
        sa.Column("resource_id", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_index(op.f("ix_stage_result_run_id"), table_name="stage_result")
    op.drop_index(op.f("ix_stage_result_operation_key"), table_name="stage_result")
    op.drop_index(op.f("ix_stage_result_change_id"), table_name="stage_result")
    op.drop_table("stage_result")
    op.drop_index("uq_decision_idempotency_key", table_name="decision")
    op.drop_index(op.f("ix_decision_change_id"), table_name="decision")
    op.drop_table("decision")
    op.drop_index("uq_change_external_ref", table_name="change")
    op.drop_table("change")
