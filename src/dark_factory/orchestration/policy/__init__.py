"""Escalation, participation and merge policy of autonomous implementation
(T-016, ADR-018; merge: T-026, ADR-011 p.2).

Deterministic, pure-domain policy checks consulted by the flow: the
Implementation Contract gate, the machine-checkable escalation conditions
(ADR-018 p.5), the Known/Bounded/New path classifier (p.6), the risk-class
monotonicity (ADR-011 p.5), the phase participation table (p.1) and the
merge policy (T-026, ADR-011 p.2). No harness/LLM calls and no database
access.
"""

from dark_factory.orchestration.policy.decision_class import (
    DecisionClassPolicyError,
    DecisionFacts,
    DecisionVerdict,
    classify_decision,
    transition_decision_class,
)
from dark_factory.orchestration.policy.escalation import (
    BoundaryChange,
    adr_proposal_violation,
    autonomy_budget_violation,
    boundary_change_violation,
    contract_entry_violation,
    escalation_stop_reason,
    gate_failure_violation,
    irreversible_operation_violation,
    requirements_violation,
    risk_raise_violation,
    scope_exit_violation,
    ui_verification_violation,
)
from dark_factory.orchestration.policy.merge import (
    DEFAULT_MERGE_POLICY,
    MergeDecision,
    MergeDecisionKind,
    MergeExecutor,
    MergePolicy,
    MergeRequestContext,
    evaluate_merge,
)
from dark_factory.orchestration.policy.participation import (
    PHASE_PARTICIPATION,
    STAGE_PARTICIPATION,
    PhaseName,
    stage_participation,
)
from dark_factory.orchestration.policy.risk import (
    R2_THRESHOLD,
    RISK_ASSESSMENT_MANUAL,
    RISK_ORDER,
    RiskClassPolicyError,
    is_r2_or_higher,
    transition_risk_class,
)

__all__ = [
    "DEFAULT_MERGE_POLICY",
    "PHASE_PARTICIPATION",
    "R2_THRESHOLD",
    "RISK_ASSESSMENT_MANUAL",
    "RISK_ORDER",
    "STAGE_PARTICIPATION",
    "BoundaryChange",
    "DecisionClassPolicyError",
    "DecisionFacts",
    "DecisionVerdict",
    "MergeDecision",
    "MergeDecisionKind",
    "MergeExecutor",
    "MergePolicy",
    "MergeRequestContext",
    "PhaseName",
    "RiskClassPolicyError",
    "adr_proposal_violation",
    "autonomy_budget_violation",
    "boundary_change_violation",
    "classify_decision",
    "contract_entry_violation",
    "escalation_stop_reason",
    "evaluate_merge",
    "gate_failure_violation",
    "irreversible_operation_violation",
    "is_r2_or_higher",
    "requirements_violation",
    "risk_raise_violation",
    "scope_exit_violation",
    "stage_participation",
    "transition_decision_class",
    "transition_risk_class",
    "ui_verification_violation",
]
