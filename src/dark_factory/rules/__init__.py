"""Flow rules: gate policy, deterministic limits and merge protection (T-004, T-026)."""

from dark_factory.rules.gates import required_gates, unsatisfied_gates
from dark_factory.rules.limits import (
    LimitRule,
    LimitViolation,
    continuation_violations,
    rework_violation,
)
from dark_factory.rules.merge_protection import (
    DEFAULT_MERGE_PROTECTION,
    MergeProtectionPolicy,
    MergeProtectionRule,
    MergeProtectionViolation,
    ObservedBranchProtection,
    protection_violations,
)

__all__ = [
    "DEFAULT_MERGE_PROTECTION",
    "LimitRule",
    "LimitViolation",
    "MergeProtectionPolicy",
    "MergeProtectionRule",
    "MergeProtectionViolation",
    "ObservedBranchProtection",
    "continuation_violations",
    "protection_violations",
    "required_gates",
    "rework_violation",
    "unsatisfied_gates",
]
