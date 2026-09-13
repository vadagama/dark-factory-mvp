"""Route profiles of the Factory Flow (T-004)."""

import pytest

from dark_factory.changes.enums import Route, Stage
from dark_factory.flows.routes import ROUTE_PROFILES, STAGE_SEQUENCE, route_profile

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
