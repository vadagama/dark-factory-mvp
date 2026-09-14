"""Decision classifier Known/Bounded/New and its monotonicity policy (T-016, ADR-018 p.6)."""

import pytest

from dark_factory.changes.enums import DecisionClass, DecisionSource
from dark_factory.orchestration.policy.decision_class import (
    DecisionClassPolicyError,
    DecisionFacts,
    classify_decision,
    transition_decision_class,
)


def test_known_path_is_autonomous_without_obligations() -> None:
    verdict = classify_decision(DecisionFacts(pinned_by_adr=True))
    assert verdict.decision_class is DecisionClass.KNOWN_PATH
    assert verdict.requires_human is False
    assert verdict.requires_adr is False
    assert verdict.requires_rationale is False


def test_bounded_choice_requires_rationale() -> None:
    verdict = classify_decision(DecisionFacts(allowed_options=("postgres", "mysql")))
    assert verdict.decision_class is DecisionClass.BOUNDED_CHOICE
    assert verdict.requires_human is False
    assert verdict.requires_adr is False
    assert verdict.requires_rationale is True


def test_new_path_requires_human_and_adr() -> None:
    verdict = classify_decision(DecisionFacts(new_boundary=True))
    assert verdict.decision_class is DecisionClass.NEW_PATH
    assert verdict.requires_human is True
    assert verdict.requires_adr is True


def test_new_boundary_wins_over_pinned_adr() -> None:
    verdict = classify_decision(DecisionFacts(pinned_by_adr=True, new_boundary=True))
    assert verdict.decision_class is DecisionClass.NEW_PATH


def test_no_signal_defaults_conservatively_to_new_path() -> None:
    verdict = classify_decision(DecisionFacts())
    assert verdict.decision_class is DecisionClass.NEW_PATH
    assert verdict.requires_human is True
    assert verdict.requires_adr is True


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (DecisionClass.NEW_PATH, DecisionClass.KNOWN_PATH),
        (DecisionClass.NEW_PATH, DecisionClass.BOUNDED_CHOICE),
        (DecisionClass.BOUNDED_CHOICE, DecisionClass.KNOWN_PATH),
    ],
)
def test_agent_cannot_lower_decision_class(current: DecisionClass, target: DecisionClass) -> None:
    with pytest.raises(DecisionClassPolicyError):
        transition_decision_class(current, target, decided_by=DecisionSource.AGENT)


def test_policy_and_human_may_lower_decision_class() -> None:
    lowered = transition_decision_class(
        DecisionClass.NEW_PATH, DecisionClass.BOUNDED_CHOICE, decided_by=DecisionSource.POLICY
    )
    assert lowered is DecisionClass.BOUNDED_CHOICE
    lowered = transition_decision_class(
        DecisionClass.NEW_PATH, DecisionClass.KNOWN_PATH, decided_by=DecisionSource.HUMAN
    )
    assert lowered is DecisionClass.KNOWN_PATH


def test_agent_may_raise_decision_class() -> None:
    raised = transition_decision_class(
        DecisionClass.KNOWN_PATH, DecisionClass.NEW_PATH, decided_by=DecisionSource.AGENT
    )
    assert raised is DecisionClass.NEW_PATH
