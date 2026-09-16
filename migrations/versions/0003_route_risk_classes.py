"""Route risk classes: allow the architecture and foundation routes (T-080, ADR-023 p.6)

Revision ID: 0003_route_risk_classes
Revises: 0002_api_state
Create Date: 2026-09-16

"""

from collections.abc import Sequence

from alembic import op

from dark_factory.changes.enums import Route

# revision identifiers, used by Alembic.
revision: str = "0003_route_risk_classes"
down_revision: str | Sequence[str] | None = "0002_api_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT: str = "ck_execution_route_allowed"
_TABLE: str = "execution"

# Routes the constraint allowed before this revision: a frozen snapshot of the
# pre-T-080 world, so a downgrade does not silently follow the enum if it grows.
_LEGACY_ROUTES: tuple[Route, ...] = (Route.QUICK, Route.STANDARD)


def _route_condition(routes: Sequence[Route]) -> str:
    """``route IN (...)`` rendered from ``Route``, so values cannot drift from the enum."""
    values = ", ".join(f"'{route.value}'" for route in routes)
    return f"route IN ({values})"


def _replace_route_check(routes: Sequence[Route]) -> None:
    """Swap the route CHECK constraint for ``routes`` (the original name is kept)."""
    op.drop_constraint(op.f(_CONSTRAINT), _TABLE, type_="check")
    op.create_check_constraint(op.f(_CONSTRAINT), _TABLE, _route_condition(routes))


def upgrade() -> None:
    """Widen ``execution.route`` to all four routes of ADR-023 p.6."""
    _replace_route_check(list(Route))


def downgrade() -> None:
    """Back to the two MVP routes; rows carrying a new route must be gone first."""
    _replace_route_check(list(_LEGACY_ROUTES))
