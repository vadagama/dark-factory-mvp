"""Machine-checkable escalation conditions of autonomous implementation (T-016, ADR-018 p.5).

Every check is a deterministic pure function of its inputs and returns either
``None`` (condition silent) or one ``EscalationViolation`` with diagnostics.
The flow vetoes autonomous continuation while a violation is declared on
``StageResult.escalations``; the implementation-contract check additionally
gates entering construction directly (T-016 DoD). Fixable gate findings stay
in the bounded rework cycle (T-014): escalation fires only for non-retryable
policy violations or an exhausted rework budget.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from dark_factory.changes.enums import BoundaryArea, EscalationRule, RiskClass, Route, Stage
from dark_factory.changes.escalations import EscalationViolation
from dark_factory.changes.findings import Decision
from dark_factory.changes.implementation_contract import ChangeScope, ImplementationContract
from dark_factory.changes.risk import is_r2_or_higher
from dark_factory.orchestration.policy.risk import missing_control_points
from dark_factory.orchestration.routes import route_allows_risk, route_profile

_UNSUPPORTED_TEXT: Final = "not provided for by the approved implementation contract"


@dataclass(frozen=True)
class BoundaryChange:
    """A change touching a protected boundary, as declared by the implementation (ADR-018 p.5)."""

    area: BoundaryArea
    compatible: bool = True
    migration_plan_approved: bool = False


def contract_entry_violation(
    contract: ImplementationContract | None,
) -> EscalationViolation | None:
    """Construction entry gate: the change carries an approved contract (T-016 DoD)."""
    if contract is None:
        return EscalationViolation(
            rule=EscalationRule.IMPLEMENTATION_CONTRACT_UNAPPROVED,
            reason=(
                "no implementation contract attached to the run; "
                "implementation must not start without one (ADR-018 p.3)"
            ),
        )
    if contract.approval is None:
        return EscalationViolation(
            rule=EscalationRule.IMPLEMENTATION_CONTRACT_UNAPPROVED,
            reason=(
                f"implementation contract {contract.id!r} is not approved; "
                "implementation must not start before a human approves it (ADR-018 p.3)"
            ),
        )
    return None


def requirements_violation(conflicts: Sequence[str]) -> EscalationViolation | None:
    """Contradictory or insufficient requirements escalate (ADR-018 p.5)."""
    if not conflicts:
        return None
    return EscalationViolation(
        rule=EscalationRule.REQUIREMENTS_DEFICIENT,
        reason="requirements are contradictory or insufficient: " + "; ".join(conflicts),
    )


def scope_exit_violation(items: Sequence[str], scope: ChangeScope) -> EscalationViolation | None:
    """Touching anything outside the approved scope escalates (ADR-018 p.5)."""
    out_of_scope = [item for item in items if item not in scope.in_scope]
    if not out_of_scope:
        return None
    return EscalationViolation(
        rule=EscalationRule.SCOPE_EXIT,
        reason=("implementation leaves the approved scope: " + ", ".join(out_of_scope)),
    )


def boundary_change_violation(
    change: BoundaryChange, contract: ImplementationContract | None
) -> EscalationViolation | None:
    """Protected-boundary changes escalate unless the contract covers them (ADR-018 p.5).

    A boundary the approved contract explicitly permits is autonomous as long
    as the change is compatible or carries an approved migration plan; an
    incompatible change without one escalates as well.
    """
    covered = contract is not None and change.area in contract.allowed_boundaries
    if covered and (change.compatible or change.migration_plan_approved):
        return None
    if not covered:
        return EscalationViolation(
            rule=EscalationRule.BOUNDARY_CHANGE,
            reason=(
                f"{change.area.value} change is {_UNSUPPORTED_TEXT} "
                "(ADR-018 p.5: public API, data schema, IAM, architecture boundary)"
            ),
        )
    return EscalationViolation(
        rule=EscalationRule.BOUNDARY_CHANGE,
        reason=(
            f"incompatible {change.area.value} change without an approved migration plan "
            f"is {_UNSUPPORTED_TEXT}"
        ),
    )


def adr_proposal_violation(proposal: str | None) -> EscalationViolation | None:
    """A proposed new ADR always escalates: significant decisions are human-owned."""
    if proposal is None:
        return None
    return EscalationViolation(
        rule=EscalationRule.NEW_ADR_PROPOSAL,
        reason=f"agent proposes a new ADR: {proposal} (ADR-018 p.6: New path requires a human)",
    )


def risk_escalation_violation(
    *,
    risk_class: RiskClass,
    route: Route,
    stage: Stage,
    decisions: Sequence[Decision] = (),
    sha: str | None = None,
) -> EscalationViolation | None:
    """Unmet obligations of a R2+ risk class; ``None`` below R2 (ADR-023 p.5).

    Closes the deferred condition of ADR-018 p.5: the raise to R2+ is no longer a
    manual assessment but a machine check of the class obligations — the route
    band and the human control points of the stage. ``reason`` names every unmet
    obligation, so a human sees what is missing instead of a bare verdict.

    ``decisions`` are the human decisions known to the caller; a control point
    counts as closed only by an ``APPROVED`` human decision bound to ``sha``
    (version-bound approval, ADR-009 p.7).
    """
    if not is_r2_or_higher(risk_class):
        return None
    unmet: list[str] = []
    if not route_allows_risk(route, risk_class):
        profile = route_profile(route)
        unmet.append(
            f"route {route.value!r} does not allow risk class {risk_class.value} "
            f"(band {profile.min_risk_class.value}-{profile.max_risk_class.value})"
        )
    missing = missing_control_points(route, stage, risk_class, decisions, sha=sha)
    if missing:
        names = ", ".join(sorted(point.value for point in missing))
        unmet.append(f"no human approval on the control points of stage {stage.value!r}: {names}")
    if not unmet:
        return None
    return EscalationViolation(
        rule=EscalationRule.RISK_RAISED_TO_R2,
        reason=f"risk class {risk_class.value} obligations are not met: " + "; ".join(unmet),
    )


def gate_failure_violation(
    *,
    policy_violations: Sequence[str],
    rework_budget_exhausted: bool,
) -> EscalationViolation | None:
    """Gate failures fixable in bounded rework stay in the cycle (T-14); the rest escalate.

    Non-retryable policy violations escalate immediately; an exhausted
    rework/retry budget escalates the still-failing gates.
    """
    if policy_violations:
        return EscalationViolation(
            rule=EscalationRule.UNRECOVERABLE_GATE_FAILURE,
            reason=(
                "non-retryable policy violation: "
                + "; ".join(policy_violations)
                + "; bounded rework cannot fix it (ADR-018 p.5)"
            ),
        )
    if rework_budget_exhausted:
        return EscalationViolation(
            rule=EscalationRule.UNRECOVERABLE_GATE_FAILURE,
            reason="rework budget exhausted with gates still failing (ADR-018 p.5, T-014)",
        )
    return None


def autonomy_budget_violation(
    contract: ImplementationContract | None,
    *,
    iterations_used: int,
) -> EscalationViolation | None:
    """Autonomous iterations beyond the contract budget escalate (ADR-018 p.5, T-062)."""
    if contract is None:
        return None
    allowed = contract.budget.max_autonomous_iterations
    if iterations_used < allowed:
        return None
    return EscalationViolation(
        rule=EscalationRule.AUTONOMY_BUDGET_EXHAUSTED,
        reason=(
            f"autonomous iteration budget exhausted: {iterations_used}/{allowed} iterations used"
        ),
    )


def ui_verification_violation(
    *,
    auto_verifiable: bool,
    detail: str | None = None,
) -> EscalationViolation | None:
    """UI that automatic comparison cannot confirm against acceptance criteria escalates."""
    if auto_verifiable:
        return None
    detail_text = detail if detail is not None else "no automatic comparison could be run"
    return EscalationViolation(
        rule=EscalationRule.UI_UNVERIFIABLE,
        reason=f"UI cannot be confirmed automatically: {detail_text} (ADR-018 p.5)",
    )


def irreversible_operation_violation(operation: str | None) -> EscalationViolation | None:
    """An irreversible operation always escalates to a human (ADR-018 p.5)."""
    if operation is None:
        return None
    return EscalationViolation(
        rule=EscalationRule.IRREVERSIBLE_OPERATION,
        reason=f"irreversible operation requested: {operation} (ADR-018 p.5)",
    )


def escalation_stop_reason(escalations: Sequence[EscalationViolation]) -> str | None:
    """Joint diagnostics of declared escalations; ``None`` when nothing is declared.

    The flow turns this into the ``Blocked`` stop reason: deterministic,
    human-readable, one line per violation.
    """
    if not escalations:
        return None
    return "; ".join(f"{violation.rule.value}: {violation.reason}" for violation in escalations)
