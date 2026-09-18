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

from dark_factory.changes.enums import Gate, RiskClass, Route, Stage
from dark_factory.changes.run import Change
from dark_factory.changes.usage import BudgetSnapshot
from dark_factory.rules.gates import required_gates, required_human_gates


@dataclass(frozen=True, slots=True)
class StageContext:
    """Fixed inputs of one deterministic stage attempt (FR-001, ADR-005).

    ``required_gates`` is the route-specific slice of the gate policy for the
    stage: the gates the flow requires before the stage may advance. A risk
    class never adds a gate to it (ADR-023 p.3): the risk-driven human gates and
    control points are computed by the policy where they are consumed.

    ``human_gates`` is the risk-aware human subset of that slice (ADR-029 p.3):
    the gates only a human decision may satisfy. The machine gates are
    ``required_gates - human_gates``, and the stage's wait follows that set — a
    stage with machine gates parks for CI, a purely human-gated one for the
    human (ADR-029; found in T-043 increment 1, where the risk-widened ``ui``
    gate leaked into the pipeline verdict).
    """

    change: Change
    stage: Stage
    route: Route
    run_id: str
    input_revision: str | None
    required_gates: frozenset[Gate]
    human_gates: frozenset[Gate]
    budget: BudgetSnapshot
    attempt_number: int = 1
    """Physical attempt this context belongs to (ADR-006 p.7).

    The executor puts it on the result, so a retry of the same logical operation
    produces a result whose attempt matches the active stage run; the flow
    rejects a stale attempt (ADR-006 p.4). A caller that does not track attempts
    (a one-shot deterministic run) keeps the default.
    """


def build_context(
    *,
    change: Change,
    stage: Stage,
    route: Route,
    run_id: str,
    input_revision: str | None,
    budget: BudgetSnapshot,
    attempt_number: int = 1,
    risk_class: RiskClass | None = None,
) -> StageContext:
    """Assemble the stage context from the validated snapshot and the flow tables.

    Both gate sets come from ``rules.gates`` — the single source of gate policy
    (ADR-005, ADR-029): ``required_gates`` is the full set of the stage (on the
    standard route the design stage additionally carries the UI gate, the quick
    route skips it) and ``human_gates`` is its risk-aware human subset.
    ``risk_class`` is the run's effective class (declared, derived facts and the
    route floor, ADR-023 p.2) — a caller that holds a run passes the policy's
    derivation so the context weighs the same set the flow checks; without it
    the snapshot's declared class is used. ``attempt_number`` is the physical
    attempt of the operation (ADR-006 p.7); it defaults to the first attempt for
    callers that do not track retries.
    """
    effective_class = risk_class if risk_class is not None else change.risk_class
    return StageContext(
        change=change,
        stage=stage,
        route=route,
        run_id=run_id,
        input_revision=input_revision,
        required_gates=required_gates(route, stage),
        human_gates=required_human_gates(route, stage, effective_class),
        budget=budget,
        attempt_number=attempt_number,
    )
