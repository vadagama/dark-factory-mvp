"""Route profiles of the Factory Flow (T-004, ADR-005).

``RouteProfile`` is the topology slice of the FlowProfile (data-model 1: stage
sequence per route). In MVP both routes traverse the five AI-DLC-like phases;
they differ in gate policy (``dark_factory.rules.gates``). A future route that
skips stages only changes its profile here — the transition table and the
engine are unaffected.
"""

from dataclasses import dataclass
from typing import Final

from dark_factory.changes.enums import Route, Stage

STAGE_SEQUENCE: Final[tuple[Stage, ...]] = (
    Stage.SPECIFICATION,
    Stage.PLANNING,
    Stage.CONSTRUCTION,
    Stage.REVIEW_VERIFICATION,
    Stage.RELEASE,
)


@dataclass(frozen=True)
class RouteProfile:
    """Stage sequence of one route."""

    route: Route
    stages: tuple[Stage, ...]

    @property
    def initial_stage(self) -> Stage:
        """Stage a run starts from on this route."""
        return self.stages[0]

    def next_stage(self, stage: Stage) -> Stage | None:
        """Immediate successor of ``stage`` on this route, if any."""
        try:
            index = self.stages.index(stage)
        except ValueError:
            return None
        if index + 1 >= len(self.stages):
            return None
        return self.stages[index + 1]


ROUTE_PROFILES: Final[dict[Route, RouteProfile]] = {
    route: RouteProfile(route=route, stages=STAGE_SEQUENCE) for route in Route
}


def route_profile(route: Route) -> RouteProfile:
    """Profile of ``route``; every ``Route`` member has a profile."""
    return ROUTE_PROFILES[route]
