"""Deterministic stage context: the fixed inputs of one stage attempt (T010).

Context assembly (FR-001) freezes everything the deterministic steps may look
at before any check runs: the validated ``Change`` snapshot, the executed
stage, the run identity (ADR-006 p.3), the route with its gate policy slice
(ADR-005) and the budget snapshot that carries the rework/limit state
(FR-008, FR-016). Nothing else is read later: checks and aggregation see
only this context, so the whole deterministic path stays reproducible from
the persisted snapshot and calls no harness/LLM (ADR-003).
"""

from dataclasses import dataclass

from dark_factory.changes.enums import Gate, Route, Stage
from dark_factory.changes.run import Change
from dark_factory.changes.usage import BudgetSnapshot
from dark_factory.rules.gates import required_gates


@dataclass(frozen=True, slots=True)
class StageContext:
    """Fixed inputs of one deterministic stage attempt (FR-001, ADR-005).

    ``required_gates`` is the route-specific slice of the gate policy for the
    stage: the gates the flow requires before the stage may advance. A risk
    class never adds a gate to it (ADR-023 p.3): the risk-driven human gates and
    control points are computed by the policy where they are consumed.
    """

    change: Change
    stage: Stage
    route: Route
    run_id: str
    input_revision: str | None
    required_gates: frozenset[Gate]
    budget: BudgetSnapshot


def build_context(
    *,
    change: Change,
    stage: Stage,
    route: Route,
    run_id: str,
    input_revision: str | None,
    budget: BudgetSnapshot,
) -> StageContext:
    """Assemble the stage context from the validated snapshot and the flow tables.

    The machine gate set comes from ``rules.gates`` — the single source of gate
    policy (ADR-005): on the standard route construction additionally carries the
    UI gate, the quick route skips it.
    """
    return StageContext(
        change=change,
        stage=stage,
        route=route,
        run_id=run_id,
        input_revision=input_revision,
        required_gates=required_gates(route, stage),
        budget=budget,
    )
