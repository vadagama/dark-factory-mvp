"""Risk-class policy: monotonic transitions and the R2 threshold (T-016, ADR-011 p.5, ADR-018 p.5).

The risk class is the input of the release and escalation policies: an agent
may only raise the class; lowering it requires the formal policy or a human
decision (ADR-011 p.5). The per-class gate and control-point matrix is
T-080 — until then R2+ stays a manual assessment (ADR-018 p.5, "Дальше").
"""

from typing import Final

from dark_factory.changes.enums import DecisionSource, RiskClass


class RiskClassPolicyError(ValueError):
    """A risk-class transition outside the formal policy was requested."""


RISK_ORDER: Final[dict[RiskClass, int]] = {risk: index for index, risk in enumerate(RiskClass)}
"""Total order R0 < R1 < R2 < R3 < R4 (ADR-011 p.5)."""

R2_THRESHOLD: Final = RiskClass.R2
"""First class whose raise escalates to a human (ADR-018 p.5)."""

RISK_ASSESSMENT_MANUAL: Final = True
"""R2+ raises stay manual until the risk matrix lands (T-080, ADR-018 p.5)."""


def transition_risk_class(
    current: RiskClass, target: RiskClass, *, decided_by: DecisionSource
) -> RiskClass:
    """Apply a risk-class transition under the monotonicity policy.

    An agent decision may only raise the class; lowering it requires the
    formal policy or a human (ADR-011 p.5). Policy and human actors may move
    the class in any direction. Returns ``target`` when the transition is
    allowed.
    """
    if decided_by is DecisionSource.AGENT and RISK_ORDER[target] < RISK_ORDER[current]:
        raise RiskClassPolicyError(
            f"agent cannot lower the risk class {current.value} -> {target.value}: "
            "only the formal policy may lower it (ADR-011 p.5)"
        )
    return target


def is_r2_or_higher(risk_class: RiskClass) -> bool:
    """Whether ``risk_class`` is at or above the R2 escalation threshold."""
    return RISK_ORDER[risk_class] >= RISK_ORDER[R2_THRESHOLD]
