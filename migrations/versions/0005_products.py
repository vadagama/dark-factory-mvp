"""Products: product registry and the change -> product link (T065, ADR-030)

Revision ID: 0005_products
Revises: 0004_runner_state
Create Date: 2026-09-19

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from dark_factory.changes.enums import ProductStatus

# revision identifiers, used by Alembic.
revision: str = "0005_products"
down_revision: str | Sequence[str] | None = "0004_runner_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PRODUCT: str = "product"
_CHANGE: str = "change"
_CHANGE_PRODUCT_ID: str = "product_id"

# ``status IN (...)`` rendered from the enum, so the CHECK cannot drift from it.
_STATUS_VALUES: str = ", ".join(f"'{status.value}'" for status in ProductStatus)


def upgrade() -> None:
    """Create the product registry and link changes to it (ADR-030 p.1/p.2)."""
    op.create_table(
        _PRODUCT,
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=512), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("repository", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("repository_url", sa.String(length=512), nullable=True),
        sa.Column("baseline_ref", sa.String(length=512), nullable=True),
        sa.Column("dev_env_ref", sa.String(length=512), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("status_reason", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("state_revision", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(f"status IN ({_STATUS_VALUES})", name=op.f("ck_product_status_allowed")),
        sa.CheckConstraint("state_revision >= 1", name=op.f("ck_product_state_revision_positive")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_product")),
    )
    op.add_column(_CHANGE, sa.Column(_CHANGE_PRODUCT_ID, sa.String(length=128), nullable=True))
    op.create_index(
        op.f(f"ix_{_CHANGE}_{_CHANGE_PRODUCT_ID}"), _CHANGE, [_CHANGE_PRODUCT_ID], unique=False
    )


def downgrade() -> None:
    """Drop the change -> product link and the product registry."""
    op.drop_index(op.f(f"ix_{_CHANGE}_{_CHANGE_PRODUCT_ID}"), table_name=_CHANGE)
    op.drop_column(_CHANGE, _CHANGE_PRODUCT_ID)
    op.drop_table(_PRODUCT)
