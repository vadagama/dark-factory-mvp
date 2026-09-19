"""Risk-class policy: effective class, control points and the R2 threshold (T-016, T-080).

The risk class is the input of the release and escalation policies: an agent may
only raise the class; lowering it requires the formal policy or a human decision
(ADR-011 p.5). The domain primitives — the total order, the classifier and the
effective class — live in ``dark_factory.changes.risk`` (lowest layer) and are
re-exported here, where the package surface has always carried them.

This module owns the *derivation*: :func:`risk_facts` reads the observable facts
of a change off the approved Implementation Contract and
:func:`effective_change_risk_class` takes ``max(declared, classify(facts),
route_floor)``, so the class in the pipeline is recomputed by the policy and can
never be self-reported below its facts or its route (ADR-023 p.2/p.7).

T-080 (ADR-023) adds the control-point matrix on top: a control point is a named
human decision bound to an existing ``(stage, gate)`` pair, mandatory exactly
when its gate is required *and* human for the triple ``(route, stage, risk)``.
The matrix follows from ``rules.gates.required_human_gates`` — it is not
maintained as a second table, so it cannot drift from the gate policy. ADR-029
p.2 moves the ``ux`` point with its gate to the design phase: it is bound to
``(specification, ui)``, because the human confirms UX/UI before construction.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from dark_factory.changes.enums import (
    ControlPoint,
    DecisionOutcome,
    DecisionSource,
    Gate,
    RiskClass,
    Route,
    Stage,
)
from dark_factory.changes.findings import Decision
from dark_factory.changes.implementation_contract import ImplementationContract
from dark_factory.changes.risk import (
    R2_THRESHOLD,
    RISK_ORDER,
    RiskFacts,
    effective_risk_class,
    is_r2_or_higher,
)
from dark_factory.orchestration.routes import route_profile
from dark_factory.orchestration.rules.gates import required_human_gates

__all__ = [
    "CONTROL_POINT_BINDING",
    "R2_THRESHOLD",
    "RISK_ORDER",
    "TRUSTED_CHANGE_PATHS",
    "ControlPointBinding",
    "RiskClassPolicyError",
    "effective_change_risk_class",
    "is_r2_or_higher",
    "missing_control_points",
    "required_control_points",
    "risk_facts",
    "transition_risk_class",
]

TRUSTED_CHANGE_PATHS: Final[tuple[str, ...]] = (
    "src/dark_factory/rules/",
    "src/dark_factory/orchestration/policy/",
    "src/dark_factory/changes/risk.py",
    "src/dark_factory/quality/gates/",
)
"""Paths of the trusted layer whose change is a factory self-modification (ADR-015 p.3).

``rules/**``, ``orchestration/policy/**`` and the quality gates are named in
ADR-023 p.7; ``changes/risk.py`` is the risk-classification rule itself, which
ADR-015 p.3 counts as part of the same condition. A change touching any of them
is R4 and travels on ``foundation`` — it can never be applied through the
ordinary process whose classification it rewrites.
"""


def risk_facts(contract: ImplementationContract) -> RiskFacts:
    """Facts of one change observed on the approved contract (T-080, ADR-023 p.2).

    Derived today: ``boundaries`` from ``allowed_boundaries`` and
    ``factory_self_modification`` from the approved scope items naming a path in
    the trusted layer. The remaining facts — ``irreversible``, ``regulated_data``,
    ``documentation_only`` and ``decision_class`` — are **not observable** in the
    pipeline yet (no contract field carries them); they stay at their
    conservative defaults instead of being invented, and that gap is TD-013.

    Nothing here lowers a class: the caller takes the maximum with the declared
    class and the route floor.
    """
    return RiskFacts(
        boundaries=contract.allowed_boundaries,
        factory_self_modification=any(_is_trusted_change(item) for item in contract.scope.in_scope),
    )


def _is_trusted_change(item: str) -> bool:
    """Whether a scope item names a path inside the trusted layer (ADR-015 p.3).

    Scope items are free-form: a plain-language boundary never matches, and an
    item that is not a repository path cannot be classified here — both fall
    back to ``False`` (no self-modification *detected*, never a claim that the
    change is safe).
    """
    path = item.strip().removeprefix("./")
    return any(path.startswith(prefix) for prefix in TRUSTED_CHANGE_PATHS)


def effective_change_risk_class(contract: ImplementationContract | None, route: Route) -> RiskClass:
    """Effective class of a run: maximum of declared, derived facts and the route floor.

    ``max(declared, classify_risk(facts), route_floor)`` (ADR-023 p.2/p.6): the
    class is recomputed by the policy on every transition, so a change whose
    derived facts classify higher than its declared class cannot travel as
    declared, and ``architecture``/``foundation`` raise it further through their
    floor. Without an approved contract the run carries neither a declared class
    nor observable facts: only the route floor and the conservative R1 fallback
    of the classifier apply, and the contract entry gate keeps the run out of
    construction anyway (T-016).
    """
    if contract is not None and contract.approval is not None:
        declared = contract.risk_class
        facts = risk_facts(contract)
    else:
        declared = RiskClass.R0
        facts = RiskFacts()
    return effective_risk_class(declared, facts, route_floor=route_profile(route).min_risk_class)


class RiskClassPolicyError(ValueError):
    """A risk-class transition outside the formal policy was requested."""


def transition_risk_class(
    current: RiskClass, target: RiskClass, *, decided_by: DecisionSource
) -> RiskClass:
    """Apply a risk-class transition under the monotonicity policy.

    An agent decision may only raise the class; lowering it requires the
    formal policy or a human (ADR-011 p.5). Policy and human actors may move
    the class in any direction. Returns ``target`` when the transition is
    allowed.
    """
    if decided_by is DecisionSource.AGENT and RISK_ORDER[target] < RISK_ORDER[current]:
        raise RiskClassPolicyError(
            f"agent cannot lower the risk class {current.value} -> {target.value}: "
            "only the formal policy may lower it (ADR-011 p.5)"
        )
    return target


@dataclass(frozen=True)
class ControlPointBinding:
    """The ``(stage, gate)`` pair carrying one human control point (ADR-023 p.1)."""

    stage: Stage
    gate: Gate


CONTROL_POINT_BINDING: Final[Mapping[ControlPoint, ControlPointBinding]] = {
    ControlPoint.PROBLEM: ControlPointBinding(stage=Stage.SPECIFICATION, gate=Gate.SPECIFICATION),
    ControlPoint.SOLUTION: ControlPointBinding(stage=Stage.PLANNING, gate=Gate.PLANNING),
    # ADR-029 p.2: the UX/UI confirmation is a design-phase decision, so the
    # point follows the ``ui`` gate to the specification stage.
    ControlPoint.UX: ControlPointBinding(stage=Stage.SPECIFICATION, gate=Gate.UI),
    ControlPoint.DISCOVERY_RELEASE: ControlPointBinding(
        stage=Stage.REVIEW_VERIFICATION, gate=Gate.REVIEW
    ),
}
"""Bindings of the four ADR-023 p.4 control points to existing gates (``ux`` on specification,
ADR-029 p.2)."""


def required_control_points(
    route: Route, stage: Stage, risk_class: RiskClass
) -> frozenset[ControlPoint]:
    """Control points mandatory for ``(route, stage, risk_class)`` (ADR-023 p.4).

    The single rule of the matrix: a point is mandatory exactly when its gate is
    a required human gate of the triple. ``problem`` and ``discovery_release``
    therefore hold for every class (they are the ADR-018 base human gates), while
    ``solution`` appears only when the class (and the route) make its gate human
    and ``ux`` whenever the route requires ``ui`` — a base human gate since
    ADR-029 p.2, so it holds on every route that requires it.
    """
    human_gates = required_human_gates(route, stage, risk_class)
    return frozenset(
        point
        for point, binding in CONTROL_POINT_BINDING.items()
        if binding.stage is stage and binding.gate in human_gates
    )


def missing_control_points(
    route: Route,
    stage: Stage,
    risk_class: RiskClass,
    decisions: Sequence[Decision],
    *,
    sha: str | None = None,
) -> frozenset[ControlPoint]:
    """Required control points without a version-bound human approval (ADR-023 p.5).

    A decision closes a point when it is a human ``APPROVED`` decision recorded
    on the point's gate and bound to ``sha`` (version-bound approval, ADR-009
    p.7): ``sha`` pins the revision the approval must authorize, so an approval
    made at an older SHA never closes a point at a new head. ``sha=None`` matches
    an unbound decision — the caller that does not pin a revision (for example
    the planning stage, which has no product SHA yet) accepts the unbound
    approval of that stage.
    """
    human_approvals = {
        decision.gate
        for decision in decisions
        if decision.decided_by is DecisionSource.HUMAN
        and decision.outcome is DecisionOutcome.APPROVED
        and decision.commit_sha == sha
    }
    return frozenset(
        point
        for point in required_control_points(route, stage, risk_class)
        if CONTROL_POINT_BINDING[point].gate not in human_approvals
    )
