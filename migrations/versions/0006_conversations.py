"""Conversations and documents: questions, comments, rework orders, drafts, view marks (T078-T085, ADR-034/035)

Revision ID: 0006_conversations
Revises: 0005_products
Create Date: 2026-09-19

"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from dark_factory.changes.enums import (
    AnswerKind,
    CommentStatus,
    Phase,
    QuestionStatus,
    ReworkOrderStatus,
)

# revision identifiers, used by Alembic.
revision: str = "0006_conversations"
down_revision: str | Sequence[str] | None = "0005_products"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_QUESTION: str = "question"
_COMMENT: str = "comment"
_REWORK_ORDER: str = "rework_order"
_ARTIFACT_DRAFT: str = "artifact_draft"
_ARTIFACT_VIEW: str = "artifact_view"


def _values(enum_cls: type[Any]) -> str:
    """``IN (...)`` list rendered from the enum, so the CHECK cannot drift from it."""
    return ", ".join(f"'{member.value}'" for member in enum_cls)


_PHASE_VALUES: str = _values(Phase)
_KIND_VALUES: str = _values(AnswerKind)
_QUESTION_STATUS_VALUES: str = _values(QuestionStatus)
_COMMENT_STATUS_VALUES: str = _values(CommentStatus)
_REWORK_ORDER_STATUS_VALUES: str = _values(ReworkOrderStatus)


def _timestamp(name: str) -> "sa.Column[Any]":
    return sa.Column(
        name, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def _change_fk(name: str = "change_id") -> "sa.Column[Any]":
    return sa.Column(
        name,
        sa.String(length=128),
        sa.ForeignKey("change.id", ondelete="CASCADE"),
        nullable=False,
    )


def upgrade() -> None:
    """Create the discussion tables (ADR-034) and the document-side records (ADR-035)."""
    op.create_table(
        _QUESTION,
        sa.Column("id", sa.String(length=128), nullable=False),
        _change_fk(),
        sa.Column("run_id", sa.String(length=128), nullable=True),
        sa.Column("phase", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("blocking", sa.Boolean(), nullable=False),
        sa.Column("artifact", sa.String(length=512), nullable=True),
        sa.Column("anchor_id", sa.String(length=256), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        _timestamp("asked_at"),
        _timestamp("updated_at"),
        sa.CheckConstraint(f"phase IN ({_PHASE_VALUES})", name=op.f("ck_question_phase_allowed")),
        sa.CheckConstraint(f"kind IN ({_KIND_VALUES})", name=op.f("ck_question_kind_allowed")),
        sa.CheckConstraint(
            f"status IN ({_QUESTION_STATUS_VALUES})", name=op.f("ck_question_status_allowed")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_question")),
    )
    op.create_index(op.f("ix_question_change_id"), _QUESTION, ["change_id"], unique=False)

    op.create_table(
        _COMMENT,
        sa.Column("id", sa.String(length=128), nullable=False),
        _change_fk(),
        sa.Column("phase", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("artifact", sa.String(length=512), nullable=False),
        sa.Column("anchor_id", sa.String(length=256), nullable=True),
        sa.Column("anchor_revision", sa.String(length=128), nullable=True),
        sa.Column("rework_order_id", sa.String(length=128), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.CheckConstraint(f"phase IN ({_PHASE_VALUES})", name=op.f("ck_comment_phase_allowed")),
        sa.CheckConstraint(
            f"status IN ({_COMMENT_STATUS_VALUES})", name=op.f("ck_comment_status_allowed")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_comment")),
    )
    op.create_index(op.f("ix_comment_change_id"), _COMMENT, ["change_id"], unique=False)

    op.create_table(
        _REWORK_ORDER,
        sa.Column("id", sa.String(length=128), nullable=False),
        _change_fk(),
        sa.Column("run_id", sa.String(length=128), nullable=True),
        sa.Column("phase", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("round", sa.Integer(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.CheckConstraint(
            f"phase IN ({_PHASE_VALUES})", name=op.f("ck_rework_order_phase_allowed")
        ),
        sa.CheckConstraint(
            f"status IN ({_REWORK_ORDER_STATUS_VALUES})",
            name=op.f("ck_rework_order_status_allowed"),
        ),
        sa.CheckConstraint(
            "round IS NULL OR round >= 1", name=op.f("ck_rework_order_round_positive")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rework_order")),
    )
    op.create_index(op.f("ix_rework_order_change_id"), _REWORK_ORDER, ["change_id"], unique=False)

    op.create_table(
        _ARTIFACT_DRAFT,
        _change_fk(),
        sa.Column("artifact", sa.String(length=512), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("base_revision", sa.String(length=128), nullable=True),
        sa.Column("saved_by", sa.String(length=128), nullable=False),
        _timestamp("updated_at"),
        sa.PrimaryKeyConstraint("change_id", "artifact", name=op.f("pk_artifact_draft")),
    )

    op.create_table(
        _ARTIFACT_VIEW,
        _change_fk(),
        sa.Column("artifact", sa.String(length=512), nullable=False),
        sa.Column("revision", sa.String(length=128), nullable=False),
        sa.Column("viewed_by", sa.String(length=128), nullable=False),
        _timestamp("viewed_at"),
        sa.PrimaryKeyConstraint("change_id", "artifact", "revision", name=op.f("pk_artifact_view")),
    )


def downgrade() -> None:
    """Drop the five tables; the discussion and the drafts are not reconstructible."""
    op.drop_table(_ARTIFACT_VIEW)
    op.drop_table(_ARTIFACT_DRAFT)
    op.drop_index(op.f("ix_rework_order_change_id"), table_name=_REWORK_ORDER)
    op.drop_table(_REWORK_ORDER)
    op.drop_index(op.f("ix_comment_change_id"), table_name=_COMMENT)
    op.drop_table(_COMMENT)
    op.drop_index(op.f("ix_question_change_id"), table_name=_QUESTION)
    op.drop_table(_QUESTION)
