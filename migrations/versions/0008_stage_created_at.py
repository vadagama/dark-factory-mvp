"""``stage.created_at``: order the occurrences of one stage by creation (M3, ADR-039)

Revision ID: 0008_stage_created_at
Revises: 0007_phase_rounds
Create Date: 2026-09-19

A rework round or a phase round of the same stage is a new operation row of
that stage. The driver reads the *last* occurrence as the one in flight, so the
rows of one stage must sort by creation, not by operation key. Existing rows
are backfilled from ``started_at`` (or the migration time), which keeps their
former relative order.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008_stage_created_at"
down_revision: str | Sequence[str] | None = "0007_phase_rounds"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STAGE: str = "stage"


def upgrade() -> None:
    """Add ``created_at`` (server default ``now()``) and backfill it from ``started_at``."""
    op.add_column(
        _STAGE,
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.execute(sa.text(f"UPDATE {_STAGE} SET created_at = started_at WHERE started_at IS NOT NULL"))


def downgrade() -> None:
    """Drop the column; the rows of one stage fall back to the operation-key order."""
    op.drop_column(_STAGE, "created_at")
