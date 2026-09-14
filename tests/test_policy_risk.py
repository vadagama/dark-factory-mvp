"""Risk-class monotonicity policy and the R2 threshold (T-016, ADR-011 p.5)."""

import pytest

from dark_factory.changes.enums import DecisionSource, RiskClass
from dark_factory.orchestration.policy.risk import (
    R2_THRESHOLD,
    RISK_ASSESSMENT_MANUAL,
    RISK_ORDER,
    RiskClassPolicyError,
    is_r2_or_higher,
    transition_risk_class,
)


def test_risk_order_covers_all_classes_in_ascending_order() -> None:
    assert list(RISK_ORDER) == [
        RiskClass.R0,
        RiskClass.R1,
        RiskClass.R2,
        RiskClass.R3,
        RiskClass.R4,
    ]
    assert [RISK_ORDER[risk] for risk in RiskClass] == [0, 1, 2, 3, 4]


def test_r2_threshold_is_the_third_class() -> None:
    assert R2_THRESHOLD is RiskClass.R2
    # Until T-080 the R2+ raise stays a manual assessment (ADR-018 p.5).
    assert RISK_ASSESSMENT_MANUAL is True


def test_agent_may_raise_risk_class() -> None:
    raised = transition_risk_class(RiskClass.R1, RiskClass.R2, decided_by=DecisionSource.AGENT)
    assert raised is RiskClass.R2


def test_agent_cannot_lower_risk_class() -> None:
    with pytest.raises(RiskClassPolicyError, match="formal policy"):
        transition_risk_class(RiskClass.R2, RiskClass.R1, decided_by=DecisionSource.AGENT)


def test_policy_and_human_may_lower_risk_class() -> None:
    lowered = transition_risk_class(RiskClass.R3, RiskClass.R2, decided_by=DecisionSource.POLICY)
    assert lowered is RiskClass.R2
    lowered = transition_risk_class(RiskClass.R3, RiskClass.R0, decided_by=DecisionSource.HUMAN)
    assert lowered is RiskClass.R0


@pytest.mark.parametrize(
    ("risk_class", "expected"),
    [
        (RiskClass.R0, False),
        (RiskClass.R1, False),
        (RiskClass.R2, True),
        (RiskClass.R3, True),
        (RiskClass.R4, True),
    ],
)
def test_is_r2_or_higher(risk_class: RiskClass, expected: bool) -> None:
    assert is_r2_or_higher(risk_class) is expected
