"""Runner state: budget and implementation contract of an execution (T-092)

Revision ID: 0004_runner_state
Revises: 0003_route_risk_classes
Create Date: 2026-09-16

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0004_runner_state"
down_revision: str | Sequence[str] | None = "0003_route_risk_classes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE: str = "execution"
_BUDGET: str = "budget"
_IMPLEMENTATION_CONTRACT: str = "implementation_contract"


def upgrade() -> None:
    """Add the run-state columns the durable driver reconstructs a ``ChangeRun`` from.

    Both are nullable: an execution created before this revision carries no
    budget snapshot and no approved contract, and ``NULL`` reads back as
    "not set" (the default snapshot and an unapproved contract).
    """
    op.add_column(
        _TABLE, sa.Column(_BUDGET, postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )
    op.add_column(
        _TABLE,
        sa.Column(_IMPLEMENTATION_CONTRACT, postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    """Drop both columns; the run state they carry is not reconstructible from elsewhere."""
    op.drop_column(_TABLE, _IMPLEMENTATION_CONTRACT)
    op.drop_column(_TABLE, _BUDGET)
