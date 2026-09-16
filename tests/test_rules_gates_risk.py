"""Risk-class gate policy: route bands and required human gates (T-080, ADR-023 p.3).

The risk class never adds a machine gate: it closes routes (``route_allows_risk``)
and widens the human gate set of the stage (``required_human_gates``). The
invariant "a human gate is always a required gate" is pinned here for every
``(route, stage, risk_class)`` triple; the control points of ADR-023 p.4 derive
from it in ``orchestration.policy.risk``.
"""

from itertools import pairwise

import pytest

from dark_factory.changes.enums import ControlPoint, Gate, RiskClass, Route, Stage
from dark_factory.flows.routes import route_allows_risk
from dark_factory.orchestration.policy.risk import required_control_points
from dark_factory.rules.gates import (
    HUMAN_GATES,
    RISK_HUMAN_GATES,
    required_gates,
    required_human_gates,
)


def test_risk_human_gates_cover_every_risk_class() -> None:
    assert set(RISK_HUMAN_GATES) == set(RiskClass)


def test_risk_human_gates_are_monotone_in_the_class() -> None:
    assert RISK_HUMAN_GATES[RiskClass.R0] == frozenset()
    assert RISK_HUMAN_GATES[RiskClass.R1] == frozenset({Gate.UI})
    assert RISK_HUMAN_GATES[RiskClass.R2] == frozenset({Gate.UI, Gate.PLANNING})
    assert RISK_HUMAN_GATES[RiskClass.R3] == frozenset(Gate)
    assert RISK_HUMAN_GATES[RiskClass.R4] == frozenset(Gate)


# --- route bands ---


@pytest.mark.parametrize(
    ("route", "risk_class", "expected"),
    [
        (Route.QUICK, RiskClass.R0, True),
        (Route.QUICK, RiskClass.R1, True),
        (Route.QUICK, RiskClass.R2, False),
        (Route.QUICK, RiskClass.R3, False),
        (Route.QUICK, RiskClass.R4, False),
        (Route.STANDARD, RiskClass.R0, True),
        (Route.STANDARD, RiskClass.R2, True),
        (Route.STANDARD, RiskClass.R4, True),
        (Route.ARCHITECTURE, RiskClass.R1, False),
        (Route.ARCHITECTURE, RiskClass.R2, True),
        (Route.ARCHITECTURE, RiskClass.R4, True),
        (Route.FOUNDATION, RiskClass.R2, False),
        (Route.FOUNDATION, RiskClass.R3, True),
        (Route.FOUNDATION, RiskClass.R4, True),
    ],
)
def test_route_allows_risk_matches_the_declared_band(
    route: Route, risk_class: RiskClass, expected: bool
) -> None:
    assert route_allows_risk(route, risk_class) is expected


def test_every_r2_change_is_rejected_on_the_quick_route() -> None:
    """Dangerous changes do not take the short path, for every R2+ class."""
    for risk_class in (RiskClass.R2, RiskClass.R3, RiskClass.R4):
        assert not route_allows_risk(Route.QUICK, risk_class)


# --- required human gates ---


def test_required_human_gates_are_the_base_set_at_r0() -> None:
    for route in Route:
        for stage in Stage:
            assert required_human_gates(route, stage, RiskClass.R0) == HUMAN_GATES & required_gates(
                route, stage
            )


def test_required_human_gates_follow_the_class_and_the_route() -> None:
    assert required_human_gates(Route.STANDARD, Stage.PLANNING, RiskClass.R1) == frozenset()
    assert required_human_gates(Route.STANDARD, Stage.PLANNING, RiskClass.R2) == frozenset(
        {Gate.PLANNING}
    )
    assert required_human_gates(Route.STANDARD, Stage.CONSTRUCTION, RiskClass.R0) == frozenset()
    assert required_human_gates(Route.STANDARD, Stage.CONSTRUCTION, RiskClass.R1) == frozenset(
        {Gate.UI}
    )


def test_a_gate_the_route_does_not_require_never_becomes_human() -> None:
    """``ui`` is human from R1 on, but ``quick`` does not require it at all.

    R3/R4 make every gate the route requires human, so the empty set holds only
    while the UI gate is the class-side addition (R1/R2) — exactly the "R0/R1
    short route, no UI gate" rule of ADR-023 p.4.
    """
    assert required_human_gates(Route.STANDARD, Stage.CONSTRUCTION, RiskClass.R1) == frozenset(
        {Gate.UI}
    )
    assert route_allows_risk(Route.QUICK, RiskClass.R2) is False
    for risk_class in (RiskClass.R0, RiskClass.R1, RiskClass.R2):
        assert required_human_gates(Route.QUICK, Stage.CONSTRUCTION, risk_class) == frozenset()
    assert required_human_gates(Route.QUICK, Stage.CONSTRUCTION, RiskClass.R3) == frozenset(
        {Gate.CODE}
    )


def test_r3_and_r4_make_every_required_gate_human() -> None:
    for route in Route:
        for stage in Stage:
            for risk_class in (RiskClass.R3, RiskClass.R4):
                assert required_human_gates(route, stage, risk_class) == required_gates(
                    route, stage
                )


@pytest.mark.parametrize("route", [Route.STANDARD, Route.ARCHITECTURE, Route.FOUNDATION])
def test_the_ux_control_point_is_mandatory_on_every_route_but_quick(route: Route) -> None:
    """Every route but ``quick`` requires the UI gate, so ``ux`` is a real point there."""
    assert required_control_points(route, Stage.CONSTRUCTION, RiskClass.R2) == frozenset(
        {ControlPoint.UX}
    )


def test_the_ux_control_point_never_appears_on_the_quick_route() -> None:
    for risk_class in RiskClass:
        required = required_control_points(Route.QUICK, Stage.CONSTRUCTION, risk_class)
        assert ControlPoint.UX not in required


def test_human_gates_are_always_a_subset_of_the_required_gates() -> None:
    for route in Route:
        for stage in Stage:
            for risk_class in RiskClass:
                assert required_human_gates(route, stage, risk_class) <= required_gates(
                    route, stage
                )


def test_risk_never_removes_a_base_human_gate() -> None:
    for route in Route:
        for stage in Stage:
            for risk_class in RiskClass:
                base = HUMAN_GATES & required_gates(route, stage)
                assert base <= required_human_gates(route, stage, risk_class)


def test_the_human_gate_set_grows_with_the_class() -> None:
    for route in Route:
        for stage in Stage:
            sets = [required_human_gates(route, stage, risk) for risk in RiskClass]
            assert all(earlier <= later for earlier, later in pairwise(sets))
