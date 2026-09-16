"""Budget coordinator and allowance policy (T-062, FR-016/FR-018, ADR-018 p.3/p.5).

Single public surface of the budget package: the policy (limits and per-role
allowances) and the coordinator that enforces them against one ledger.

``RoleUsage`` is re-exported here although it lives in ``changes/usage.py``,
next to ``Usage`` and ``BudgetSnapshot``: it is a serialized data contract
(``RunUsageSummary.roles``), but it is also part of the budget surface T-062
specifies.
"""

from dark_factory.changes.usage import RoleUsage
from dark_factory.orchestration.budget.coordinator import (
    BudgetAggregate,
    BudgetCheck,
    BudgetCoordinator,
    BudgetExhaustedError,
    BudgetScope,
    BudgetState,
    BudgetViolation,
    Reservation,
    UnknownReservationError,
)
from dark_factory.orchestration.budget.policy import (
    DEFAULT_BUDGET_POLICY,
    BudgetLimits,
    BudgetPolicy,
    RoleAllowance,
)

__all__ = [
    "DEFAULT_BUDGET_POLICY",
    "BudgetAggregate",
    "BudgetCheck",
    "BudgetCoordinator",
    "BudgetExhaustedError",
    "BudgetLimits",
    "BudgetPolicy",
    "BudgetScope",
    "BudgetState",
    "BudgetViolation",
    "Reservation",
    "RoleAllowance",
    "RoleUsage",
    "UnknownReservationError",
]
