"""Deterministic machine checks of the stage path (T010, FR-009).

Only checks that genuinely run on the fixed context live here, and every
outcome is honest: limit exhaustion is evaluated against the carried budget
snapshot (FR-008, FR-016, ADR-018 p.5), gate applicability against the route
policy (ADR-005). A gate the deterministic path cannot execute — machine
checks run on the final SHA in CI (FR-009, SC-004) — is reported as
``pending``, never as ``passed``. There are no LLM/harness calls (ADR-003).

:func:`construction_entry_reason` is the Construction entry gate (T-016), read
from the context and shared by both stage executors (T-063): the gate is
attributed to the Construction attempt it stops, and each executor turns the
reason into its own blocked result before any external effect.

The shared attempt budget (T-062) arrives as an already computed
``BudgetCheck`` from ``orchestration.budget``: here it is turned into the stop
diagnostics of the attempt and into the open blocker findings the Console
counts, so the stage path stays free of ledger state and of the wall clock.
"""

from datetime import datetime
from typing import Final

from dark_factory.changes.enums import (
    FindingOrigin,
    FindingSeverity,
    FindingStatus,
    Gate,
    GateStatus,
    Stage,
)
from dark_factory.changes.findings import Finding, GateResult
from dark_factory.changes.run import Change
from dark_factory.changes.usage import BudgetSnapshot
from dark_factory.orchestration.budget import BudgetCheck, BudgetState, BudgetViolation
from dark_factory.orchestration.policy.escalation import contract_entry_violation
from dark_factory.orchestration.rules.limits import (
    LimitViolation,
    continuation_violations,
    rework_violation,
)
from dark_factory.orchestration.stages.context import StageContext

# Stages whose deterministic success path needs a change request: review and
# verification complete through ``merge`` (ADR-005 p.2), and ``MergeAction``
# carries a mandatory ``ChangeRequestRef`` (ADR-011).
_STAGES_REQUIRING_CHANGE_REQUEST: Final[frozenset[Stage]] = frozenset({Stage.REVIEW_VERIFICATION})

# Machine-class detection of a budget finding: the deterministic checks, not an agent.
_BUDGET_FINDING_CATEGORY: Final[str] = "budget"


def construction_entry_reason(context: StageContext) -> str | None:
    """Reason the attempt must not enter Construction, or ``None`` when it may.

    The Construction entry gate of ADR-018 p.3: entering construction requires an
    approved Implementation Contract (T-016 DoD). The reason is produced where
    Construction is *entered* — both stage executors pre-flight it before any
    harness/workspace/publish work (T-063) — so the stop is attributed to the
    Construction attempt and a retry of that stop never re-runs the completed
    outgoing stage.

    The gate applies only to Construction (other stages consume the contract, if
    at all, through their own policies) and only to a run-backed context
    (``context.enforce_contract_entry``): the one-shot deterministic executor has
    no run and therefore no contract to check. A context that is gated and carries
    no approved contract is blocked — the check is fail-closed, never fail-open.
    """
    if context.stage is not Stage.CONSTRUCTION or not context.enforce_contract_entry:
        return None
    violation = contract_entry_violation(context.implementation_contract)
    return violation.reason if violation is not None else None


def budget_exhaustions(budget: BudgetSnapshot, *, now: datetime) -> list[LimitViolation]:
    """Limit rules that stop autonomous execution, in stable order (FR-008, ADR-018 p.5).

    The rework check asks whether one more round would still be within the
    limit (FR-008: at most 3 rounds by default); the remaining rules are the
    existing continuation checks (token/cost budget, deadline). Every
    triggered rule means the attempt ends ``Blocked`` (FR-008, SC-006).
    """
    violations: list[LimitViolation] = []
    rework = rework_violation(budget, requested_round=budget.used_rework_rounds + 1)
    if rework is not None:
        violations.append(rework)
    violations.extend(continuation_violations(budget, now=now))
    return violations


def budget_stop_reason(check: BudgetCheck) -> str | None:
    """Diagnostics that stop autonomous work, or ``None`` while within limits.

    Exhaustion of the run/role budget ends the stage attempt ``Blocked`` with
    these reasons (FR-018, ADR-018 p.5); the string is the joint diagnostics of
    the check, run scope first.
    """
    if check.state is BudgetState.WITHIN_LIMITS:
        return None
    return check.diagnostics


def budget_findings(check: BudgetCheck) -> list[Finding]:
    """Open blocker findings of an exhausted budget check (Console visibility, T-062).

    One blocker per triggered limit with a deterministic id (scope, rule, role),
    so a repeated attempt of the same logical operation deduplicates in the run
    view (``api.aggregates.open_blocker_count``) instead of inflating the count,
    and a run-level violation is reported once no matter how many roles are
    checked. ``origin`` is ``ci``: the finding comes from a deterministic machine
    check, not from an agent or a human. ``required_action`` carries the
    violation diagnostics — the text the Console renders for open blockers
    (``Finding`` has no separate message field).
    """
    if check.state is BudgetState.WITHIN_LIMITS:
        return []
    return [
        Finding(
            id=_budget_finding_id(violation),
            origin=FindingOrigin.CI,
            role=violation.role,
            severity=FindingSeverity.BLOCKER,
            category=_BUDGET_FINDING_CATEGORY,
            required_action=violation.reason,
            status=FindingStatus.OPEN,
        )
        for violation in check.violations
    ]


def _budget_finding_id(violation: BudgetViolation) -> str:
    """Deterministic id: the same triggered limit always yields the same finding."""
    parts = ["budget", violation.scope.value]
    if violation.role is not None:
        parts.append(violation.role.value)
    parts.append(violation.rule)
    return ":".join(parts)


def pending_gate_results(required_gates: frozenset[Gate]) -> list[GateResult]:
    """One ``pending`` record per required gate, in stable gate order.

    The deterministic stage path executes no machine checks on a product SHA
    (FR-009, SC-004), so no gate can honestly be ``passed`` or ``failed``
    here: the records document which gates this stage/route requires and that
    they are not evaluated yet. ``pending`` does not satisfy a gate in the
    flow policy, which matches the stage's own decision to wait.
    """
    return [
        GateResult(gate=gate, status=GateStatus.PENDING)
        for gate in sorted(required_gates, key=lambda gate: gate.value)
    ]


def change_request_missing(stage: Stage, change: Change) -> bool:
    """True when the stage's success path needs a change request and none is attached.

    Only the review/verification stage completes through ``merge`` (ADR-005),
    and merge policy works on a change request (ADR-011). On other stages the
    check is not applicable — ``False`` says nothing about their inputs.
    """
    if stage not in _STAGES_REQUIRING_CHANGE_REQUEST:
        return False
    return change.change_request is None
