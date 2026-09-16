"""Route profiles of the Factory Flow (T-004) and their risk bands (T-080, ADR-023 p.6)."""

import pytest

from dark_factory.changes.enums import RiskClass, Route, Stage
from dark_factory.context.sdd.strictness import PROFILE_ROUTE, WorkflowProfile
from dark_factory.flows.routes import (
    HUMAN_GATES,
    ROUTE_PROFILES,
    ROUTE_STRICTNESS,
    STAGE_SEQUENCE,
    route_allows_risk,
    route_profile,
    select_route,
)
from dark_factory.orchestration.policy.risk import effective_change_risk_class
from dark_factory.rules.gates import HUMAN_GATES as RULES_HUMAN_GATES
from tests.changes_factories import make_contract

EXPECTED_SEQUENCE: tuple[Stage, ...] = (
    Stage.SPECIFICATION,
    Stage.PLANNING,
    Stage.CONSTRUCTION,
    Stage.REVIEW_VERIFICATION,
    Stage.RELEASE,
)


def test_stage_sequence_is_the_ai_dlc_like_phase_order() -> None:
    assert STAGE_SEQUENCE == EXPECTED_SEQUENCE


@pytest.mark.parametrize("route", list(Route), ids=[route.value for route in Route])
def test_every_route_has_a_profile_traversing_all_mvp_stages(route: Route) -> None:
    profile = route_profile(route)
    assert profile.route == route
    assert profile.stages == EXPECTED_SEQUENCE
    assert profile.initial_stage == Stage.SPECIFICATION
    assert ROUTE_PROFILES[route] is profile


def test_next_stage_walks_the_sequence_and_none_at_the_end() -> None:
    profile = route_profile(Route.STANDARD)
    current = profile.initial_stage
    walked = [current]
    while (nxt := profile.next_stage(current)) is not None:
        walked.append(nxt)
        current = nxt
    assert tuple(walked) == EXPECTED_SEQUENCE


def test_next_stage_of_unknown_stage_is_none() -> None:
    assert route_profile(Route.QUICK).next_stage(Stage.RELEASE) is None


def test_human_gates_are_defined_in_the_gate_policy_module() -> None:
    """``rules.gates`` is the single source of gate policy (ADR-005); routes re-export it."""
    assert HUMAN_GATES is RULES_HUMAN_GATES
    assert all(profile.human_gates is RULES_HUMAN_GATES for profile in ROUTE_PROFILES.values())


# --- risk bands (ADR-023 p.6) ---


@pytest.mark.parametrize(
    ("route", "floor", "ceiling"),
    [
        (Route.QUICK, RiskClass.R0, RiskClass.R1),
        (Route.STANDARD, RiskClass.R0, RiskClass.R4),
        (Route.ARCHITECTURE, RiskClass.R2, RiskClass.R4),
        (Route.FOUNDATION, RiskClass.R3, RiskClass.R4),
    ],
)
def test_every_route_declares_its_risk_band(
    route: Route, floor: RiskClass, ceiling: RiskClass
) -> None:
    profile = route_profile(route)
    assert profile.min_risk_class is floor
    assert profile.max_risk_class is ceiling


def test_the_quick_route_is_the_only_short_route_closed_for_r2() -> None:
    """No R2+ class travels on the short route (ADR-023 p.3)."""
    for risk_class in (RiskClass.R2, RiskClass.R3, RiskClass.R4):
        assert route_allows_risk(Route.QUICK, risk_class) is False
        assert route_allows_risk(Route.STANDARD, risk_class) is True


def test_the_band_is_two_sided() -> None:
    """A route also refuses a class below its floor: the floor raises that class.

    ``foundation`` never carries R2 as such — the route floor lifts the change to
    R3, and only that raised class (R3/R4) is inside the band (ADR-023 p.6).
    """
    assert route_allows_risk(Route.FOUNDATION, RiskClass.R2) is False
    assert route_allows_risk(Route.FOUNDATION, RiskClass.R3) is True


# --- route selection (ADR-023 p.6) ---


def test_every_strictness_profile_maps_to_a_known_route() -> None:
    assert set(PROFILE_ROUTE) == set(WorkflowProfile)
    assert set(PROFILE_ROUTE.values()) <= set(Route)


@pytest.mark.parametrize(
    ("profile", "risk_class", "expected"),
    [
        (WorkflowProfile.BUGFIX_R0, RiskClass.R0, Route.QUICK),
        (WorkflowProfile.BUGFIX_R0, RiskClass.R1, Route.QUICK),
        (WorkflowProfile.BUGFIX_R0, RiskClass.R2, Route.STANDARD),
        (WorkflowProfile.BUGFIX_R0, RiskClass.R3, Route.STANDARD),
        (WorkflowProfile.BUGFIX_R0, RiskClass.R4, Route.STANDARD),
        (WorkflowProfile.PRODUCT_FEATURE, RiskClass.R0, Route.STANDARD),
        (WorkflowProfile.PRODUCT_FEATURE, RiskClass.R4, Route.STANDARD),
        (WorkflowProfile.UI_RESEARCH, RiskClass.R2, Route.STANDARD),
        (WorkflowProfile.ARCHITECTURE_CHANGE, RiskClass.R0, Route.ARCHITECTURE),
        (WorkflowProfile.ARCHITECTURE_CHANGE, RiskClass.R3, Route.ARCHITECTURE),
        (WorkflowProfile.REPOSITORY_REBUILD, RiskClass.R0, Route.FOUNDATION),
        (WorkflowProfile.PLATFORM_CHANGE, RiskClass.R4, Route.FOUNDATION),
    ],
)
def test_select_route_is_deterministic_by_profile_and_class(
    profile: WorkflowProfile, risk_class: RiskClass, expected: Route
) -> None:
    assert select_route(profile, risk_class) is expected


def test_select_route_only_tightens_the_profile_route() -> None:
    for profile, base in PROFILE_ROUTE.items():
        for risk_class in RiskClass:
            selected = select_route(profile, risk_class)
            assert ROUTE_STRICTNESS[selected] >= ROUTE_STRICTNESS[base]


def test_select_route_repeats_identically() -> None:
    for profile in WorkflowProfile:
        for risk_class in RiskClass:
            assert select_route(profile, risk_class) is select_route(profile, risk_class)


def test_route_selection_always_carries_the_effective_class() -> None:
    """The selected route must allow the class the run will actually carry (ADR-023 p.6).

    The effective class is ``max(declared, facts, route floor)``: since the floor
    is taken from the *selected* route, no profile/class pair may land on a route
    whose band refuses the resulting class (the declared class alone would: an
    R1 change on ``architecture`` is raised to R2 by the floor, not rejected).
    """
    for profile in WorkflowProfile:
        for declared in RiskClass:
            selected = select_route(profile, declared)
            contract = make_contract().model_copy(update={"risk_class": declared})
            effective = effective_change_risk_class(contract, selected)
            assert route_allows_risk(selected, effective), (
                f"{profile.value}/{declared.value}: {selected.value} refuses {effective.value}"
            )


def test_high_risk_raises_the_bugfix_profile_off_the_short_route() -> None:
    assert select_route(WorkflowProfile.BUGFIX_R0, RiskClass.R2) is Route.STANDARD
    assert select_route(WorkflowProfile.BUGFIX_R0, RiskClass.R4) is Route.STANDARD


def test_a_route_floor_does_not_relax_the_profile_route() -> None:
    """The floor raises the class instead of lowering the route (ADR-023 p.6)."""
    assert select_route(WorkflowProfile.REPOSITORY_REBUILD, RiskClass.R0) is Route.FOUNDATION
    assert route_profile(Route.FOUNDATION).min_risk_class is RiskClass.R3
