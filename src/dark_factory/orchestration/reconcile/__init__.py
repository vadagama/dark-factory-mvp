"""Reconciler: the anomaly→action table, models and the pass executor (T-063, ADR-006).

Public surface:

- ``models`` — frozen journal models of one pass (``AnomalyKind``,
  ``ReconcileAction``, ``RunReconciliation``, ``ReconcileReport``);
- ``rules`` — the pure anomaly→action table (fixed rule order, first match
  wins) and the CI↔PostgreSQL drift resolution in favour of PostgreSQL;
- ``service`` — :class:`GlobalReconciler`, the idempotent pass executor over
  the PostgreSQL state store, and :class:`PostgresReconciliationService`, the
  ``ReconciliationService`` port adapter.

The reconciler never executes agent tasks (ADR-006 p.5) and never depends on
webhooks (ADR-019): the scheduled pass is the authoritative recovery path.
"""

from dark_factory.orchestration.reconcile.models import (
    AnomalyKind,
    ReconcileAction,
    ReconcileActionKind,
    ReconcileReport,
    RunReconciliation,
)
from dark_factory.orchestration.reconcile.rules import (
    ANOMALY_RULES,
    BranchFacts,
    ChangeRequestFacts,
    ExternalFacts,
    LeaseFacts,
    ReconcileObserver,
    ReconcileRule,
    RunFacts,
    plan_action,
    resolve_drift,
)
from dark_factory.orchestration.reconcile.service import (
    ACTIVE_DEADLINE_SECONDS,
    CONCURRENCY_POLICY,
    EXECUTION_LEASE_RESOURCE_TYPE,
    EXECUTION_LEASE_TTL,
    GLOBAL_LEASE_RESOURCE_ID,
    GLOBAL_LEASE_RESOURCE_TYPE,
    GLOBAL_LEASE_TTL,
    RECONCILE_INTERVAL_MINUTES,
    GlobalReconciler,
    PostgresReconciliationService,
    observe_statuses,
)

__all__ = [
    "ACTIVE_DEADLINE_SECONDS",
    "ANOMALY_RULES",
    "CONCURRENCY_POLICY",
    "EXECUTION_LEASE_RESOURCE_TYPE",
    "EXECUTION_LEASE_TTL",
    "GLOBAL_LEASE_RESOURCE_ID",
    "GLOBAL_LEASE_RESOURCE_TYPE",
    "GLOBAL_LEASE_TTL",
    "RECONCILE_INTERVAL_MINUTES",
    "AnomalyKind",
    "BranchFacts",
    "ChangeRequestFacts",
    "ExternalFacts",
    "GlobalReconciler",
    "LeaseFacts",
    "PostgresReconciliationService",
    "ReconcileAction",
    "ReconcileActionKind",
    "ReconcileObserver",
    "ReconcileReport",
    "ReconcileRule",
    "RunFacts",
    "RunReconciliation",
    "observe_statuses",
    "plan_action",
    "resolve_drift",
]
