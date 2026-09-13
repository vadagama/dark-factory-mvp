"""Gate policy of the Factory Flow (T-004, vision 3.9).

The seven MVP gates are bound to the stages that evaluate them. The standard
route requires all seven; the quick route skips the UI gate (contracts/cli.md:
quick skips UI/extended gates, human gates stay per ADR-018). Gate execution
itself (machine checks on the final SHA) is T-013/T-021; this module only
decides which gates a stage must satisfy before the flow may advance.
"""

from collections.abc import Mapping, Sequence
from typing import Final

from dark_factory.changes.enums import Gate, GateStatus, Route, Stage
from dark_factory.changes.findings import GateResult

_STAGE_GATES: Final[Mapping[Stage, frozenset[Gate]]] = {
    Stage.SPECIFICATION: frozenset({Gate.SPECIFICATION}),
    Stage.PLANNING: frozenset({Gate.PLANNING}),
    Stage.CONSTRUCTION: frozenset({Gate.CODE}),
    Stage.REVIEW_VERIFICATION: frozenset({Gate.REVIEW, Gate.VERIFICATION}),
    Stage.RELEASE: frozenset({Gate.RELEASE}),
}

# Gates added by a route on top of the stage base set; the quick route skips
# the UI gate, so only the standard route adds one.
_ROUTE_EXTRA_GATES: Final[Mapping[Route, Mapping[Stage, frozenset[Gate]]]] = {
    Route.QUICK: {},
    Route.STANDARD: {Stage.CONSTRUCTION: frozenset({Gate.UI})},
}

# A skipped gate was evaluated as not applicable, so it does not block.
_GATE_SATISFIED: Final[frozenset[GateStatus]] = frozenset({GateStatus.PASSED, GateStatus.SKIPPED})


def required_gates(route: Route, stage: Stage) -> frozenset[Gate]:
    """Gates the stage must satisfy on ``route`` before the flow may advance."""
    extra = _ROUTE_EXTRA_GATES.get(route, {}).get(stage, frozenset())
    return _STAGE_GATES[stage] | extra


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
