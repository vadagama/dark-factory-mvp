"""The anomaly→action table of the Reconciler (T-063, ADR-006 p.5/p.7/p.9).

Deterministic, pure-domain planning: no harness/LLM calls, no I/O, no clock —
identical facts and an identical ``now`` yield an identical plan (same
discipline as ``orchestration.policy.merge`` and ``orchestration.rework``).
Rules run in a fixed order and the first match wins; one pass plans at most
one action per run, and a remaining anomaly is picked up by the next
scheduled pass. Together with the applied mutations this keeps every pass
idempotent: a repeated pass on the same observed data does not change the
state again (ADR-006 p.5).

The table (fixed order, DoD T-063):

1. ``repeated_error``                → escalate to ``Blocked``: the same failure
   recurred on consecutive attempts of one stage — recovery would repeat it
   (ADR-006 p.7 terminal, ADR-018 p.5);
2. ``expired_lease``                 → takeover through the LeaseRepository
   semantics: an expired lease is stolen with a fresh monotonic fencing token
   (ADR-006 p.6);
3. ``duplicate_runs``                → close the non-canonical duplicate as
   ``superseded``; among the active runs of one change the canonical run is
   the lexicographically smallest run id (deterministic, ADR-006 p.5);
4. ``merged_cr_without_run``         → record the merge fact — a merged change
   request without a completed run must not be lost (ADR-006 p.5);
5. ``approved_passed_without_merge`` → merge action through the existing merge
   policy (T-026, ``orchestration.policy.merge``): the reconciler evaluates it
   for the trusted finalizer — ``finalizer_merge_allowed`` plans the merge for
   the finalizer job, manual mode (the MVP default) waits for the human, a
   policy violation escalates. Auto-merge stays forbidden without explicit
   policy authorization (FR-010, ADR-011 p.2);
6. ``branch_behind``                 → branch update action for the provider
   wiring; a persistent failure to update resurfaces as a repeated error and
   escalates through rule 1.

Webhooks are only an accelerator (ADR-019): every external wait is also
resolved by the scheduled pass — the reconciler never depends on a webhook
having fired. The reconciler never executes agent tasks (ADR-006 p.5): the
actions it applies are state-level recovery mutations only.
"""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Final, Protocol

from dark_factory.changes.enums import ChangeRequestStatus, RunStatus
from dark_factory.orchestration.policy.merge import (
    DEFAULT_MERGE_POLICY,
    MergePolicy,
    MergeRequestContext,
    evaluate_merge,
)
from dark_factory.orchestration.reconcile.models import (
    AnomalyKind,
    ReconcileAction,
    ReconcileActionKind,
)
from dark_factory.ports import ReconcileDesired, ReconcileObserved, ReconcileResult


@dataclass(frozen=True)
class LeaseFacts:
    """Operational lease of one execution as read from PostgreSQL (ADR-006 p.6)."""

    owner_id: str
    fencing_token: int
    expires_at: datetime
    """Aware UTC instant; ``expires_at <= now`` makes the lease expired."""


@dataclass(frozen=True)
class ChangeRequestFacts:
    """Change request as observed from the provider (ADR-019)."""

    status: ChangeRequestStatus
    head_sha: str | None = None
    merged_sha: str | None = None


@dataclass(frozen=True)
class BranchFacts:
    """Branch position relative to its base, as observed from Git."""

    head_sha: str
    base_sha: str
    behind_count: int = 0


@dataclass(frozen=True)
class ExternalFacts:
    """Recovery sources beyond PostgreSQL for one run (MR, Git, StageResult; T-063).

    The merge facts carry the assembled :class:`MergeRequestContext`; the merge
    rule re-validates it through the merge policy, so an incompletely assembled
    context can only ever degrade to a manual/waiting outcome, never to an
    unauthorized merge.
    """

    change_request: ChangeRequestFacts | None = None
    branch: BranchFacts | None = None
    merge: MergeRequestContext | None = None


class ReconcileObserver(Protocol):
    """Assembles the external observations of one run (provider wiring, T-063).

    PostgreSQL is the authoritative desired state; the change request, the
    branch and the merge facts come from Git/MR/StageResult through this
    seam. Returning ``None`` means "nothing observed" and keeps the
    observation rules silent for the run.
    """

    async def observe_external(self, *, run_id: str, change_id: str) -> ExternalFacts | None: ...


@dataclass(frozen=True)
class RunFacts:
    """Facts of one run assembled from PostgreSQL (desired) plus observations.

    ``observed_status`` is the workflow-engine status (``None`` = not
    observed); it feeds only the CI↔PostgreSQL drift decision, none of the
    anomaly rules depends on it. ``active_sibling_ids`` lists the other
    non-terminal runs of the same change — the duplicate-runs input.
    ``repeated_error`` is precomputed by the caller from the attempt history
    (the two latest attempts of one stage both failed).
    """

    run_id: str
    change_id: str
    desired_status: RunStatus
    desired_revision: int
    observed_status: RunStatus | None = None
    lease: LeaseFacts | None = None
    active_sibling_ids: tuple[str, ...] = ()
    change_request: ChangeRequestFacts | None = None
    branch: BranchFacts | None = None
    merge: MergeRequestContext | None = None
    repeated_error: bool = False
    repeated_error_stage: str | None = None


type RuleDetect = Callable[[RunFacts, datetime, MergePolicy], "ReconcileAction | None"]
"""Uniform detector signature of one table row: ``(facts, now, policy)``."""


@dataclass(frozen=True)
class ReconcileRule:
    """One row of the anomaly→action table: the anomaly it covers and its detector."""

    anomaly: AnomalyKind
    detect: RuleDetect


_ACTIVE_RUN_STATUSES: Final[frozenset[RunStatus]] = frozenset(
    {RunStatus.PENDING, RunStatus.RUNNING, RunStatus.WAITING, RunStatus.BLOCKED}
)
_WAITING_ON_WORK: Final[frozenset[RunStatus]] = frozenset({RunStatus.RUNNING, RunStatus.WAITING})
# Runs closed deliberately or already done never receive recovery actions;
# the pass journal keeps the merged-CR check reachable for the rest.
_SKIPPED_RUN_STATUSES: Final[frozenset[RunStatus]] = frozenset(
    {RunStatus.SUCCEEDED, RunStatus.SUPERSEDED}
)


def _detect_repeated_error(
    facts: RunFacts, now: datetime, policy: MergePolicy
) -> ReconcileAction | None:
    """A run whose failure repeats must stop for a human, not recover (ADR-006 p.7, ADR-018 p.5)."""
    if not facts.repeated_error or facts.desired_status not in _WAITING_ON_WORK:
        return None
    stage = f" of stage {facts.repeated_error_stage!r}" if facts.repeated_error_stage else ""
    return ReconcileAction(
        kind=ReconcileActionKind.ESCALATE,
        anomaly=AnomalyKind.REPEATED_ERROR,
        run_id=facts.run_id,
        reason=(
            f"repeated error: the same failure recurred on consecutive attempts{stage}; "
            "escalated to blocked for a human (ADR-006 p.7, ADR-018 p.5)"
        ),
    )


def _detect_expired_lease(
    facts: RunFacts, now: datetime, policy: MergePolicy
) -> ReconcileAction | None:
    """An expired lease is stolen with a fresh fencing token (ADR-006 p.6)."""
    lease = facts.lease
    if lease is None or lease.expires_at > now:
        return None
    return ReconcileAction(
        kind=ReconcileActionKind.LEASE_TAKEOVER,
        anomaly=AnomalyKind.EXPIRED_LEASE,
        run_id=facts.run_id,
        reason=(
            f"lease of {lease.owner_id!r} expired at {lease.expires_at.isoformat()} "
            f"(fencing token {lease.fencing_token}); takeover with a fresh fencing token "
            "(ADR-006 p.6)"
        ),
    )


def _detect_duplicate_runs(
    facts: RunFacts, now: datetime, policy: MergePolicy
) -> ReconcileAction | None:
    """A non-canonical active run of an already-active change is superseded (ADR-006 p.5)."""
    if facts.desired_status not in _ACTIVE_RUN_STATUSES or not facts.active_sibling_ids:
        return None
    canonical = min(facts.run_id, *facts.active_sibling_ids)
    if facts.run_id == canonical:
        return None
    return ReconcileAction(
        kind=ReconcileActionKind.SUPERSEDE,
        anomaly=AnomalyKind.DUPLICATE_RUNS,
        run_id=facts.run_id,
        reason=(
            f"duplicate active run of change {facts.change_id}: canonical run {canonical}; "
            "closed as superseded (ADR-006 p.5)"
        ),
    )


def _detect_merged_change_request(
    facts: RunFacts, now: datetime, policy: MergePolicy
) -> ReconcileAction | None:
    """A merged change request without a completed run is recorded, not lost (ADR-006 p.5)."""
    cr = facts.change_request
    if cr is None or cr.status is not ChangeRequestStatus.MERGED:
        return None
    if facts.desired_status is RunStatus.SUCCEEDED:
        return None
    merged = f" (merge commit {cr.merged_sha})" if cr.merged_sha else ""
    return ReconcileAction(
        kind=ReconcileActionKind.RECORD_MERGE,
        anomaly=AnomalyKind.MERGED_CR_WITHOUT_RUN,
        run_id=facts.run_id,
        reason=(
            f"change request merged{merged} without a completed run: the merge fact is "
            "recorded in the authoritative state (ADR-006 p.5)"
        ),
        merged_sha=cr.merged_sha,
    )


def _detect_approved_passed(
    facts: RunFacts, now: datetime, policy: MergePolicy
) -> ReconcileAction | None:
    """Approved with gates passed but not merged: merge per the existing policy (T-026).

    The reconciler evaluates the merge policy for the trusted finalizer —
    that is the only merge executor a reconciler may plan for (agent jobs
    have no merge authority, FR-023). Manual mode (the MVP default policy)
    plans a wait for the human instead; a policy violation escalates.
    """
    cr = facts.change_request
    if facts.merge is None or cr is None or cr.status is not ChangeRequestStatus.OPEN:
        return None
    if facts.desired_status not in _WAITING_ON_WORK:
        return None
    decision = evaluate_merge(replace(facts.merge, executor="trusted_finalizer"), policy=policy)
    if decision.kind == "finalizer_merge_allowed":
        method = f" via {decision.merge_method}" if decision.merge_method else ""
        return ReconcileAction(
            kind=ReconcileActionKind.PLAN_MERGE,
            anomaly=AnomalyKind.APPROVED_PASSED_WITHOUT_MERGE,
            run_id=facts.run_id,
            reason=(
                "change request approved with gates passed but not merged: merge planned for "
                f"the trusted finalizer{method} (FR-010, T-026)"
            ),
            merge_method=decision.merge_method,
        )
    if decision.kind == "blocked":
        return ReconcileAction(
            kind=ReconcileActionKind.ESCALATE,
            anomaly=AnomalyKind.APPROVED_PASSED_WITHOUT_MERGE,
            run_id=facts.run_id,
            reason=f"merge policy blocked the approved change request: {decision.reason}",
        )
    return ReconcileAction(
        kind=ReconcileActionKind.WAIT_FOR_HUMAN,
        anomaly=AnomalyKind.APPROVED_PASSED_WITHOUT_MERGE,
        run_id=facts.run_id,
        reason=decision.reason or "merge waits for the human (FR-010, ADR-011 p.2)",
    )


def _detect_branch_behind(
    facts: RunFacts, now: datetime, policy: MergePolicy
) -> ReconcileAction | None:
    """A branch behind its base gets an update action for the provider wiring (T-063)."""
    branch = facts.branch
    if branch is None or branch.behind_count <= 0:
        return None
    if facts.desired_status not in _WAITING_ON_WORK:
        return None
    return ReconcileAction(
        kind=ReconcileActionKind.UPDATE_BRANCH,
        anomaly=AnomalyKind.BRANCH_BEHIND,
        run_id=facts.run_id,
        reason=(
            f"branch {branch.head_sha} is {branch.behind_count} commit(s) behind base "
            f"{branch.base_sha}: update planned for the provider wiring; a persistent "
            "failure resurfaces as a repeated error and escalates"
        ),
    )


ANOMALY_RULES: Final[tuple[ReconcileRule, ...]] = (
    ReconcileRule(AnomalyKind.REPEATED_ERROR, _detect_repeated_error),
    ReconcileRule(AnomalyKind.EXPIRED_LEASE, _detect_expired_lease),
    ReconcileRule(AnomalyKind.DUPLICATE_RUNS, _detect_duplicate_runs),
    ReconcileRule(AnomalyKind.MERGED_CR_WITHOUT_RUN, _detect_merged_change_request),
    ReconcileRule(AnomalyKind.APPROVED_PASSED_WITHOUT_MERGE, _detect_approved_passed),
    ReconcileRule(AnomalyKind.BRANCH_BEHIND, _detect_branch_behind),
)
"""The anomaly→action table in its fixed evaluation order (first match wins, T-063)."""


def plan_action(
    facts: RunFacts, *, now: datetime, policy: MergePolicy = DEFAULT_MERGE_POLICY
) -> ReconcileAction | None:
    """Plan at most one recovery action for ``facts``: fixed rule order, first match wins.

    ``now`` must be an aware UTC instant used for the lease expiry comparison.
    Pure: the facts are neither mutated nor persisted; applying the planned
    action is the caller's (pass executor's) decision.
    """
    if facts.desired_status in _SKIPPED_RUN_STATUSES:
        return None
    for rule in ANOMALY_RULES:
        action = rule.detect(facts, now, policy)
        if action is not None:
            return action
    return None


def resolve_drift(desired: ReconcileDesired, observed: ReconcileObserved) -> ReconcileResult:
    """Resolve the CI↔PostgreSQL desync after comparison, in favour of PostgreSQL (ADR-006 p.2/p.9).

    The result always carries the desired (authoritative) status and
    revision; ``in_sync`` is ``False`` when the observed engine state
    disagrees with PostgreSQL after the comparison.
    """
    if desired.run_id != observed.run_id:
        raise ValueError(f"reconcile scope mismatch: {desired.run_id!r} vs {observed.run_id!r}")
    return ReconcileResult(
        run_id=desired.run_id,
        in_sync=desired.status == observed.status,
        status=desired.status,
        state_revision=desired.state_revision,
    )
