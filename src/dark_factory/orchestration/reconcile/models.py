"""Models of one reconciliation pass: anomalies, actions and the report (T-063, ADR-006 p.5/p.9).

The pass output is a journal entry: every scanned run gets one
:class:`RunReconciliation` carrying the CI↔PostgreSQL drift decision and at
most one planned :class:`ReconcileAction` (fixed rule order, first match
wins — the anomaly→action table lives in ``reconcile.rules``). The models are
frozen pydantic so a pass report serializes to JSON for ``factory reconcile
--json`` and stays comparable between passes.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from dark_factory.changes.enums import RunStatus


class AnomalyKind(StrEnum):
    """Anomaly covered by a row of the anomaly→action table (T-063, DoD docs T-063)."""

    REPEATED_ERROR = "repeated_error"
    EXPIRED_LEASE = "expired_lease"
    DUPLICATE_RUNS = "duplicate_runs"
    MERGED_CR_WITHOUT_RUN = "merged_cr_without_run"
    APPROVED_PASSED_WITHOUT_MERGE = "approved_passed_without_merge"
    BRANCH_BEHIND = "branch_behind"


class ReconcileActionKind(StrEnum):
    """Recovery action planned for one anomaly (T-063, ADR-006 p.5).

    The reconciler never executes agent tasks (ADR-006 p.5): it applies only
    state-level recovery mutations — lease takeover, supersede, record merge,
    escalate. Provider operations (branch update, merge) and human decisions
    are planned for their executors: the trusted finalizer merges, the human
    merges in manual mode, the provider wiring updates branches.
    """

    LEASE_TAKEOVER = "lease_takeover"
    SUPERSEDE = "supersede"
    RECORD_MERGE = "record_merge"
    PLAN_MERGE = "plan_merge"
    WAIT_FOR_HUMAN = "wait_for_human"
    UPDATE_BRANCH = "update_branch"
    ESCALATE = "escalate"


class ReconcileAction(BaseModel):
    """One planned recovery action of the anomaly→action table (T-063).

    ``reason`` carries human-readable diagnostics for the reconcile journal.
    ``merged_sha`` and ``merge_method`` are payloads of ``record_merge`` and
    ``plan_merge`` respectively; both are ``None`` for every other kind.
    """

    model_config = ConfigDict(frozen=True)

    kind: ReconcileActionKind
    anomaly: AnomalyKind
    run_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    merged_sha: str | None = None
    merge_method: str | None = None


class RunReconciliation(BaseModel):
    """Outcome of one pass for one run (ADR-006 p.2/p.9).

    ``in_sync`` is the CI↔PostgreSQL drift decision: ``True``/``False`` when
    the engine state was observed, ``None`` when it was not (no engine wired,
    or the engine lost the run). On drift the decision is made in favour of
    PostgreSQL: ``desired_status``/``desired_revision`` carry the
    authoritative values (ADR-006 p.9). ``applied`` is ``True`` only when the
    pass actually mutated the state; a planned-but-not-applied action carries
    a ``note`` explaining why.
    """

    model_config = ConfigDict(frozen=True)

    run_id: str = Field(min_length=1)
    change_id: str = Field(min_length=1)
    desired_status: RunStatus
    desired_revision: int = Field(ge=1)
    observed_status: RunStatus | None = None
    in_sync: bool | None = None
    action: ReconcileAction | None = None
    applied: bool = False
    note: str | None = None


class ReconcileReport(BaseModel):
    """Journal entry of one reconciliation pass (ADR-006 p.5: the result is written to the log).

    ``lease_acquired=False`` means another reconciler holds the global lease:
    the pass made no changes and planned nothing (a normal outcome under a
    concurrent CronJob launch, ADR-006 p.6).
    """

    model_config = ConfigDict(frozen=True)

    schema_version: int = 1
    lease_acquired: bool
    owner_id: str = Field(min_length=1)
    scanned: int = Field(ge=0)
    entries: tuple[RunReconciliation, ...] = ()
