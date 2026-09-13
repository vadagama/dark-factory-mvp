"""Flow rules: gate policy and deterministic limits (T-004)."""

from dark_factory.rules.gates import required_gates, unsatisfied_gates
from dark_factory.rules.limits import (
    LimitRule,
    LimitViolation,
    continuation_violations,
    rework_violation,
)

__all__ = [
    "LimitRule",
    "LimitViolation",
    "continuation_violations",
    "required_gates",
    "rework_violation",
    "unsatisfied_gates",
]
