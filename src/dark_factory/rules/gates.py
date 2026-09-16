"""Gate policy of the Factory Flow (T-004, vision 3.9).

The seven MVP gates are bound to the stages that evaluate them. Every route
except ``quick`` requires all seven — ``quick`` skips the UI gate
(contracts/cli.md: quick skips UI/extended gates, human gates stay per
ADR-018; the four-route matrix is ADR-023 p.6). Gate execution itself (machine
checks on the final SHA) is T-013/T-021; this module only decides which gates a
stage must satisfy before the flow may advance.

``RISK_HUMAN_GATES`` and :func:`required_human_gates` add the risk-class side of
the same policy (T-080, ADR-023 p.3): the class never adds machine gates — the
machine set stays the function :func:`required_gates` of ``(route, stage)`` — it
widens the *human* gate set of the stage (ADR-018 p.1).

This module is the single source of gate policy (ADR-005), so the base human
set of ADR-018 lives here too and ``flows.routes`` re-exports it.
"""

from collections.abc import Mapping, Sequence
from typing import Final

from dark_factory.changes.enums import Gate, GateStatus, RiskClass, Route, Stage
from dark_factory.changes.findings import GateResult

_STAGE_GATES: Final[Mapping[Stage, frozenset[Gate]]] = {
    Stage.SPECIFICATION: frozenset({Gate.SPECIFICATION}),
    Stage.PLANNING: frozenset({Gate.PLANNING}),
    Stage.CONSTRUCTION: frozenset({Gate.CODE}),
    Stage.REVIEW_VERIFICATION: frozenset({Gate.REVIEW, Gate.VERIFICATION}),
    Stage.RELEASE: frozenset({Gate.RELEASE}),
}

# Gates added by a route on top of the stage base set. ``quick`` is the only
# route without the UI gate: every other route requires all seven gates
# (ADR-023 p.6), so ``ui`` — and with it the ``ux`` control point — is part of
# their construction stage.
_ROUTE_EXTRA_GATES: Final[Mapping[Route, Mapping[Stage, frozenset[Gate]]]] = {
    Route.QUICK: {},
    Route.STANDARD: {Stage.CONSTRUCTION: frozenset({Gate.UI})},
    Route.ARCHITECTURE: {Stage.CONSTRUCTION: frozenset({Gate.UI})},
    Route.FOUNDATION: {Stage.CONSTRUCTION: frozenset({Gate.UI})},
}

# A skipped gate was evaluated as not applicable, so it does not block.
_GATE_SATISFIED: Final[frozenset[GateStatus]] = frozenset({GateStatus.PASSED, GateStatus.SKIPPED})

# Stages where the flow waits for an explicit human decision (ADR-018 p.1):
# specification covers requirement/UX/architecture discovery approval, review
# carries the human-confirmed merge (ADR-011 p.2). Deploy to dev after merge
# is human-off-the-loop, prod is manual post-MVP (T-091). This is the *base*
# set: it does not depend on the route (contracts/cli.md: quick skips
# UI/extended gates, not human ones) nor on the risk class — the class widens it
# through :func:`required_human_gates` (ADR-023 p.3).
HUMAN_GATES: Final[frozenset[Gate]] = frozenset({Gate.SPECIFICATION, Gate.REVIEW})

# Human gates a risk class adds on top of the ADR-018 base set (ADR-023 p.3);
# the class never removes a base human gate. R3/R4 make every gate of the stage
# human, so those rows enumerate all seven gates.
RISK_HUMAN_GATES: Final[Mapping[RiskClass, frozenset[Gate]]] = {
    RiskClass.R0: frozenset(),
    RiskClass.R1: frozenset({Gate.UI}),
    RiskClass.R2: frozenset({Gate.UI, Gate.PLANNING}),
    RiskClass.R3: frozenset(Gate),
    RiskClass.R4: frozenset(Gate),
}


def required_gates(route: Route, stage: Stage) -> frozenset[Gate]:
    """Gates the stage must satisfy on ``route`` before the flow may advance."""
    extra = _ROUTE_EXTRA_GATES.get(route, {}).get(stage, frozenset())
    return _STAGE_GATES[stage] | extra


def required_human_gates(route: Route, stage: Stage, risk_class: RiskClass) -> frozenset[Gate]:
    """Gates of ``(route, stage)`` the risk class requires a human decision on (ADR-023 p.3).

    ``(HUMAN_GATES | RISK_HUMAN_GATES[risk_class]) & required_gates(route, stage)``:
    the intersection makes a gate human only when the route actually requires it
    (``ui`` on ``quick`` never becomes a control point), and the union keeps every
    ADR-018 base human gate — risk only adds, it never removes.
    """
    return (HUMAN_GATES | RISK_HUMAN_GATES[risk_class]) & required_gates(route, stage)


def unsatisfied_gates(route: Route, stage: Stage, results: Sequence[GateResult]) -> list[Gate]:
    """Required gates not satisfied by ``results``, in stable gate order.

    The last result per gate wins: later evaluations supersede earlier ones
    because a new SHA invalidates previous passes (ADR-009 p.7, T-014).
    ``skipped`` counts as satisfied; ``failed``, ``pending`` or a missing
    result does not.
    """
    latest: dict[Gate, GateStatus] = {}
    for item in results:
        latest[item.gate] = item.status
    return [
        gate
        for gate in sorted(required_gates(route, stage), key=lambda g: g.value)
        if latest.get(gate) not in _GATE_SATISFIED
    ]
