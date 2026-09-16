"""Route profiles of the Factory Flow (T-004, ADR-005; risk bands: T-080, ADR-023 p.6).

``RouteProfile`` is the topology slice of the FlowProfile (data-model 1: stage
sequence per route). All MVP routes traverse the same five AI-DLC-like phases;
they differ in gate policy (``dark_factory.rules.gates``) and in the risk-class
band they allow. A future route that skips stages only changes its profile here
— the transition table and the engine are unaffected.

The band is part of the risk policy, not of the topology: its floor raises the
effective class of a run (:func:`dark_factory.changes.risk.effective_risk_class`)
and :func:`route_allows_risk` tells whether a class may travel on a route at all
(``quick`` is closed for R2+, ADR-023 p.3). :func:`select_route` turns the
strictness profile of a ChangeSet into a route and only ever tightens it.

The base human gate set of ADR-018 is re-exported from ``rules.gates`` — the
single source of gate policy (ADR-005) — so ``RouteProfile.human_gates`` and the
historical ``flows.routes.HUMAN_GATES`` import path keep working unchanged.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from dark_factory.changes.enums import Gate, RiskClass, Route, Stage
from dark_factory.changes.risk import RISK_ORDER
from dark_factory.context.sdd.strictness import PROFILE_ROUTE, WorkflowProfile
from dark_factory.rules.gates import HUMAN_GATES

__all__ = [
    "HUMAN_GATES",
    "PROFILE_ROUTE",
    "ROUTE_PROFILES",
    "ROUTE_STRICTNESS",
    "STAGE_SEQUENCE",
    "RouteProfile",
    "WorkflowProfile",
    "route_allows_risk",
    "route_profile",
    "select_route",
]

STAGE_SEQUENCE: Final[tuple[Stage, ...]] = (
    Stage.SPECIFICATION,
    Stage.PLANNING,
    Stage.CONSTRUCTION,
    Stage.REVIEW_VERIFICATION,
    Stage.RELEASE,
)

ROUTE_STRICTNESS: Final[Mapping[Route, int]] = {
    Route.QUICK: 0,
    Route.STANDARD: 1,
    Route.ARCHITECTURE: 2,
    Route.FOUNDATION: 3,
}
"""Strictness order quick < standard < architecture < foundation (ADR-023 p.6).

The ADR states the order weakly (``standard <= architecture <= foundation``);
distinct ranks keep :func:`select_route` deterministic.
"""


@dataclass(frozen=True)
class RouteProfile:
    """Stage sequence and risk-class band of one route (ADR-005, ADR-023 p.6)."""

    route: Route
    stages: tuple[Stage, ...]
    min_risk_class: RiskClass = RiskClass.R0
    max_risk_class: RiskClass = RiskClass.R4

    @property
    def initial_stage(self) -> Stage:
        """Stage a run starts from on this route."""
        return self.stages[0]

    @property
    def human_gates(self) -> frozenset[Gate]:
        """Base gates requiring a human decision on this route (ADR-018 p.1)."""
        return HUMAN_GATES

    def next_stage(self, stage: Stage) -> Stage | None:
        """Immediate successor of ``stage`` on this route, if any."""
        try:
            index = self.stages.index(stage)
        except ValueError:
            return None
        if index + 1 >= len(self.stages):
            return None
        return self.stages[index + 1]


# Explicit rather than a comprehension over ``Route``: the bands differ per
# route, so each profile states its own floor and ceiling (ADR-023 p.6).
ROUTE_PROFILES: Final[dict[Route, RouteProfile]] = {
    Route.QUICK: RouteProfile(
        route=Route.QUICK,
        stages=STAGE_SEQUENCE,
        min_risk_class=RiskClass.R0,
        max_risk_class=RiskClass.R1,
    ),
    Route.STANDARD: RouteProfile(route=Route.STANDARD, stages=STAGE_SEQUENCE),
    Route.ARCHITECTURE: RouteProfile(
        route=Route.ARCHITECTURE,
        stages=STAGE_SEQUENCE,
        min_risk_class=RiskClass.R2,
        max_risk_class=RiskClass.R4,
    ),
    Route.FOUNDATION: RouteProfile(
        route=Route.FOUNDATION,
        stages=STAGE_SEQUENCE,
        min_risk_class=RiskClass.R3,
        max_risk_class=RiskClass.R4,
    ),
}


def route_profile(route: Route) -> RouteProfile:
    """Profile of ``route``; every ``Route`` member has a profile."""
    return ROUTE_PROFILES[route]


def route_allows_risk(route: Route, risk_class: RiskClass) -> bool:
    """Whether ``risk_class`` falls inside the band of ``route`` (ADR-023 p.3).

    False means the change may not travel on this route as it is: on ``quick``
    every R2+ change is rejected instead of being quietly carried, which is the
    machine form of "dangerous changes do not take the short path".
    """
    profile = route_profile(route)
    return (
        RISK_ORDER[profile.min_risk_class]
        <= RISK_ORDER[risk_class]
        <= RISK_ORDER[profile.max_risk_class]
    )


def select_route(profile: WorkflowProfile, risk_class: RiskClass) -> Route:
    """Deterministic route of a strictness profile tightened by the class (ADR-023 p.6).

    The profile fixes the base route; a class above the route's ceiling raises
    the route to the least stricter route that allows it (R2+ with ``bugfix-r0``
    gives ``standard``). A class below the route's floor keeps the base route:
    the route floor raises the effective class instead, and a route may only
    raise the class, never lower it. ``standard`` admits every class, so a route
    exists for any input and the tightening is total.
    """
    base = route_profile(PROFILE_ROUTE[profile])
    if RISK_ORDER[risk_class] <= RISK_ORDER[base.max_risk_class]:
        return base.route
    return min(
        (route for route in Route if route_allows_risk(route, risk_class)),
        key=lambda route: ROUTE_STRICTNESS[route],
    )
