"""Phase-bound decisions: ``decision.phase`` (M3, T098, ADR-039)

Revision ID: 0007_phase_rounds
Revises: 0006_conversations
Create Date: 2026-09-19

The ``specification`` stage runs requirements → architecture → interface as
rounds of one stage (ADR-039), and the requirements and the architecture
approvals share the ``specification`` gate. ``decision.phase`` tells them
apart; NULL is a pre-M3 decision and reads as the phase of its gate.
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

from dark_factory.changes.enums import Phase

# revision identifiers, used by Alembic.
revision: str = "0007_phase_rounds"
down_revision: str | Sequence[str] | None = "0006_conversations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DECISION: str = "decision"


def _values(enum_cls: type[Any]) -> str:
    """``IN (...)`` list rendered from the enum, so the CHECK cannot drift from it."""
    return ", ".join(f"'{member.value}'" for member in enum_cls)


_PHASE_VALUES: str = _values(Phase)


def upgrade() -> None:
    """Add the nullable ``phase`` column with its CHECK; existing rows stay NULL."""
    op.add_column(_DECISION, sa.Column("phase", sa.String(length=32), nullable=True))
    op.create_check_constraint(
        "phase_allowed", _DECISION, f"phase IS NULL OR phase IN ({_PHASE_VALUES})"
    )


def downgrade() -> None:
    """Drop the column; a phase-bound approval degrades to its gate (the M2 reading)."""
    op.drop_constraint(op.f("ck_decision_phase_allowed"), _DECISION, type_="check")
    op.drop_column(_DECISION, "phase")
