"""Risk classification R0-R4 and the monotonic effective class (T-080, ADR-023 p.2).

The classifier is a pure function of observable facts and the effective class can
only raise: this file pins the levels, the rule precedence and the monotonicity
that the "routing is deterministic" DoD of T-080 rests on.
"""

import itertools
from dataclasses import replace

import pytest

from dark_factory.changes.enums import BoundaryArea, DecisionClass, RiskClass
from dark_factory.changes.risk import (
    R2_THRESHOLD,
    RISK_ORDER,
    RiskFacts,
    classify_risk,
    effective_risk_class,
    is_r2_or_higher,
)


def _fact_combinations() -> list[RiskFacts]:
    """Every combination of the boolean facts over the boundary and decision axes."""
    combinations: list[RiskFacts] = []
    for boundaries in ((), (BoundaryArea.PUBLIC_API,)):
        for decision_class in (DecisionClass.KNOWN_PATH, DecisionClass.NEW_PATH):
            for flags in itertools.product((False, True), repeat=4):
                irreversible, regulated_data, documentation_only, self_modification = flags
                combinations.append(
                    RiskFacts(
                        boundaries=boundaries,
                        decision_class=decision_class,
                        irreversible=irreversible,
                        regulated_data=regulated_data,
                        documentation_only=documentation_only,
                        factory_self_modification=self_modification,
                    )
                )
    return combinations


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


# --- one case per level ---


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        (RiskFacts(), RiskClass.R1),
        (RiskFacts(documentation_only=True), RiskClass.R0),
        (RiskFacts(boundaries=(BoundaryArea.PUBLIC_API,)), RiskClass.R2),
        (RiskFacts(decision_class=DecisionClass.NEW_PATH), RiskClass.R2),
        (RiskFacts(irreversible=True), RiskClass.R3),
        (RiskFacts(regulated_data=True), RiskClass.R3),
        (RiskFacts(factory_self_modification=True), RiskClass.R4),
    ],
)
def test_classify_risk_levels(facts: RiskFacts, expected: RiskClass) -> None:
    assert classify_risk(facts) is expected


def test_documentation_without_behaviour_change_is_the_lowest_class() -> None:
    """R0 covers documentation/cosmetics only: a boundary already raises to R2."""
    assert classify_risk(RiskFacts(documentation_only=True)) is RiskClass.R0
    assert (
        classify_risk(
            RiskFacts(documentation_only=True, boundaries=(BoundaryArea.ARCHITECTURE_BOUNDARY,))
        )
        is RiskClass.R2
    )
    assert classify_risk(RiskFacts(documentation_only=True, irreversible=True)) is RiskClass.R3


def test_higher_level_fact_wins_over_every_lower_one() -> None:
    """R4 > R3 > R2 > R0/R1: dropping the highest fact exposes the next level."""
    all_facts = RiskFacts(
        boundaries=(BoundaryArea.DATA_SCHEMA,),
        decision_class=DecisionClass.NEW_PATH,
        irreversible=True,
        regulated_data=True,
        documentation_only=True,
        factory_self_modification=True,
    )
    assert classify_risk(all_facts) is RiskClass.R4
    assert classify_risk(replace(all_facts, factory_self_modification=False)) is RiskClass.R3
    assert (
        classify_risk(
            replace(
                all_facts,
                factory_self_modification=False,
                irreversible=False,
                regulated_data=False,
            )
        )
        is RiskClass.R2
    )
    assert classify_risk(RiskFacts(documentation_only=True)) is RiskClass.R0
    assert classify_risk(RiskFacts()) is RiskClass.R1


def test_classification_is_deterministic_over_every_fact_combination() -> None:
    for facts in _fact_combinations():
        assert classify_risk(facts) is classify_risk(facts)


# --- effective class: it can only raise ---


def test_effective_class_keeps_a_higher_declared_class() -> None:
    assert effective_risk_class(RiskClass.R3, RiskFacts()) is RiskClass.R3


def test_effective_class_is_raised_by_the_facts() -> None:
    assert effective_risk_class(RiskClass.R0, RiskFacts(regulated_data=True)) is RiskClass.R3


def test_effective_class_is_raised_by_the_route_floor() -> None:
    assert effective_risk_class(RiskClass.R0, RiskFacts(), route_floor=RiskClass.R3) is RiskClass.R3


def test_neither_facts_nor_the_floor_lower_the_declared_class() -> None:
    lowest_facts = RiskFacts(documentation_only=True)
    assert effective_risk_class(RiskClass.R4, lowest_facts) is RiskClass.R4
    assert (
        effective_risk_class(RiskClass.R4, lowest_facts, route_floor=RiskClass.R0) is RiskClass.R4
    )


def test_effective_class_is_monotonic_over_every_input() -> None:
    for facts in _fact_combinations():
        for declared in RiskClass:
            for route_floor in RiskClass:
                effective = effective_risk_class(declared, facts, route_floor=route_floor)
                assert RISK_ORDER[effective] >= RISK_ORDER[declared]
                assert RISK_ORDER[effective] >= RISK_ORDER[classify_risk(facts)]
                assert RISK_ORDER[effective] >= RISK_ORDER[route_floor]
