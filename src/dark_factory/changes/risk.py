"""Risk classes R0-R4: classification and the monotonic effective class (T-080, ADR-023).

Domain primitives of risk, free of dependencies on ``quality``, ``rules``,
``flows`` and ``orchestration`` (``changes`` is the lowest layer, ADR-015 p.3):
the total order, the R2 escalation threshold, the pure classifier over the
observed facts of a change and the effective-class rule.

The class is derived, never self-reported: :func:`classify_risk` maps facts to
the first matching level (ADR-023 p.2) and :func:`effective_risk_class` takes
the maximum of the declared class, the classified facts and the route floor —
so no input can lower it (ADR-011 p.5). The per-class gate and control-point
matrices live in ``rules.gates`` and ``orchestration.policy.risk``.
"""

from dataclasses import dataclass
from typing import Final

from dark_factory.changes.enums import BoundaryArea, DecisionClass, RiskClass

RISK_ORDER: Final[dict[RiskClass, int]] = {risk: index for index, risk in enumerate(RiskClass)}
"""Total order R0 < R1 < R2 < R3 < R4 (ADR-011 p.5)."""

R2_THRESHOLD: Final = RiskClass.R2
"""First class whose obligations escalate to a human (ADR-018 p.5)."""


def is_r2_or_higher(risk_class: RiskClass) -> bool:
    """Whether ``risk_class`` is at or above the R2 escalation threshold."""
    return RISK_ORDER[risk_class] >= RISK_ORDER[R2_THRESHOLD]


@dataclass(frozen=True)
class RiskFacts:
    """Observable facts of a change the classifier reads (ADR-023 p.2).

    ``factory_self_modification`` is derived from the diff (implementation,
    risk-classification rules and quality gates changing together, ADR-015 p.3),
    not from a self-report; ``regulated_data`` is the regulated/PDn predicate and
    ``irreversible`` the irreversibility of the requested operation.
    """

    boundaries: tuple[BoundaryArea, ...] = ()
    decision_class: DecisionClass = DecisionClass.KNOWN_PATH
    irreversible: bool = False
    regulated_data: bool = False
    documentation_only: bool = False
    factory_self_modification: bool = False


def classify_risk(facts: RiskFacts) -> RiskClass:
    """Risk class of ``facts``; the first matching level wins (ADR-023 p.2).

    Levels in precedence order: R4 factory self-modification, R3 irreversible or
    regulated data, R2 protected boundary or a New path decision, R0
    documentation/cosmetics, R1 anything else.
    """
    if facts.factory_self_modification:
        return RiskClass.R4
    if facts.irreversible or facts.regulated_data:
        return RiskClass.R3
    if facts.boundaries or facts.decision_class is DecisionClass.NEW_PATH:
        return RiskClass.R2
    if facts.documentation_only:
        return RiskClass.R0
    return RiskClass.R1


def effective_risk_class(
    declared: RiskClass, facts: RiskFacts, *, route_floor: RiskClass = RiskClass.R0
) -> RiskClass:
    """Effective class: the maximum of declared, classified and the route floor (ADR-023 p.2).

    The formal lowering policy *is* the floor: an agent may only raise the class
    (``transition_risk_class``), and the maximum makes it impossible by
    construction to end up below any of the three inputs. ``route_floor`` holds
    the floor of the run's route (for example R3 on ``foundation``), so a route
    can raise the class but never lower it.
    """
    return max((declared, classify_risk(facts), route_floor), key=lambda risk: RISK_ORDER[risk])
