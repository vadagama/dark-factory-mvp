"""Risk-class policy: monotonic transitions, control points, R2 threshold (T-016, T-080).

ADR-023 splits the risk domain in two: the pure primitives (``changes.risk``,
covered by ``test_changes_risk.py`` / ``test_rules_gates_risk.py``) and the
policy surface of this module — the monotonic transition, the binding of the
four control points to existing gates and the query for the ones still missing a
version-bound human approval.
"""

from datetime import UTC, datetime

import pytest

from dark_factory.changes.enums import (
    BoundaryArea,
    ControlPoint,
    DecisionClass,
    DecisionOutcome,
    DecisionSource,
    Gate,
    RiskClass,
    Role,
    Route,
    Stage,
)
from dark_factory.changes.findings import Decision
from dark_factory.changes.implementation_contract import ChangeScope
from dark_factory.orchestration.policy.risk import (
    CONTROL_POINT_BINDING,
    R2_THRESHOLD,
    RISK_ORDER,
    ControlPointBinding,
    RiskClassPolicyError,
    effective_change_risk_class,
    is_r2_or_higher,
    missing_control_points,
    required_control_points,
    risk_facts,
    transition_risk_class,
)
from tests.changes_factories import make_contract

NOW = datetime(2026, 9, 16, 12, 0, 0, tzinfo=UTC)
SHA = "731ac91"
OLD_SHA = "aaa111"


def _decision(
    gate: Gate,
    *,
    outcome: DecisionOutcome = DecisionOutcome.APPROVED,
    decided_by: DecisionSource = DecisionSource.HUMAN,
    commit_sha: str | None = None,
) -> Decision:
    return Decision(
        id=f"dec-{gate.value}-{outcome.value}-{decided_by.value}",
        gate=gate,
        outcome=outcome,
        decided_by=decided_by,
        role=Role.PRODUCT,
        decided_at=NOW,
        commit_sha=commit_sha,
    )


# --- monotonicity ---


def test_risk_order_covers_all_classes_in_ascending_order() -> None:
    assert list(RISK_ORDER) == [
        RiskClass.R0,
        RiskClass.R1,
        RiskClass.R2,
        RiskClass.R3,
        RiskClass.R4,
    ]
    assert [RISK_ORDER[risk] for risk in RiskClass] == [0, 1, 2, 3, 4]


def test_r2_threshold_is_the_third_class() -> None:
    assert R2_THRESHOLD is RiskClass.R2


def test_agent_may_raise_risk_class() -> None:
    raised = transition_risk_class(RiskClass.R1, RiskClass.R2, decided_by=DecisionSource.AGENT)
    assert raised is RiskClass.R2


def test_agent_cannot_lower_risk_class() -> None:
    with pytest.raises(RiskClassPolicyError, match="formal policy"):
        transition_risk_class(RiskClass.R2, RiskClass.R1, decided_by=DecisionSource.AGENT)


def test_policy_and_human_may_lower_risk_class() -> None:
    lowered = transition_risk_class(RiskClass.R3, RiskClass.R2, decided_by=DecisionSource.POLICY)
    assert lowered is RiskClass.R2
    lowered = transition_risk_class(RiskClass.R3, RiskClass.R0, decided_by=DecisionSource.HUMAN)
    assert lowered is RiskClass.R0


@pytest.mark.parametrize(
    ("risk_class", "expected"),
    [
        (RiskClass.R0, False),
        (RiskClass.R1, False),
        (RiskClass.R2, True),
        (RiskClass.R3, True),
        (RiskClass.R4, True),
    ],
)
def test_is_r2_or_higher(risk_class: RiskClass, expected: bool) -> None:
    assert is_r2_or_higher(risk_class) is expected


# --- derived facts and the effective class (T-080, ADR-023 p.2/p.6/p.7) ---


@pytest.mark.parametrize(
    ("in_scope", "expected"),
    [
        (("src/dark_factory/rules/gates.py",), True),
        (("src/dark_factory/orchestration/policy/risk.py",), True),
        (("src/dark_factory/quality/gates/specification.py",), True),
        (("src/dark_factory/changes/risk.py",), True),
        (("./src/dark_factory/rules/gates.py",), True),
        (("src/dark_factory/api/routes_runs.py",), False),
        (("docs/hld.md",), False),
        (("checkout timeout configuration",), False),
    ],
)
def test_factory_self_modification_is_derived_from_the_trusted_paths(
    in_scope: tuple[str, ...], expected: bool
) -> None:
    contract = make_contract().model_copy(
        update={"scope": ChangeScope(in_scope=in_scope, out_of_scope=())}
    )
    assert risk_facts(contract).factory_self_modification is expected


def test_risk_facts_read_the_boundaries_the_contract_permits() -> None:
    contract = make_contract().model_copy(
        update={"allowed_boundaries": (BoundaryArea.PUBLIC_API, BoundaryArea.IAM)}
    )
    assert risk_facts(contract).boundaries == (BoundaryArea.PUBLIC_API, BoundaryArea.IAM)


def test_risk_facts_leave_the_unobservable_facts_at_their_defaults() -> None:
    """Nothing is invented for facts without a pipeline source (T-080, ADR-023 p.2; TD-013)."""
    facts = risk_facts(make_contract())
    assert facts.irreversible is False
    assert facts.regulated_data is False
    assert facts.documentation_only is False
    assert facts.decision_class is DecisionClass.KNOWN_PATH


def test_effective_class_applies_the_facts_of_the_contract() -> None:
    contract = make_contract().model_copy(
        update={
            "risk_class": RiskClass.R1,
            "scope": ChangeScope(in_scope=("src/dark_factory/orchestration/policy/risk.py",)),
        }
    )
    assert effective_change_risk_class(contract, Route.STANDARD) is RiskClass.R4


@pytest.mark.parametrize(
    ("route", "expected"),
    [
        (Route.QUICK, RiskClass.R1),
        (Route.STANDARD, RiskClass.R1),
        (Route.ARCHITECTURE, RiskClass.R2),
        (Route.FOUNDATION, RiskClass.R3),
    ],
)
def test_effective_class_applies_the_route_floor(route: Route, expected: RiskClass) -> None:
    contract = make_contract().model_copy(update={"risk_class": RiskClass.R1})
    assert effective_change_risk_class(contract, route) is expected


def test_effective_class_never_falls_below_the_declared_class() -> None:
    contract = make_contract().model_copy(update={"risk_class": RiskClass.R4})
    for route in Route:
        assert effective_change_risk_class(contract, route) is RiskClass.R4


def test_effective_class_without_an_approved_contract_keeps_only_the_floor() -> None:
    unapproved = make_contract().model_copy(update={"approval": None})
    for contract in (None, unapproved):
        assert effective_change_risk_class(contract, Route.QUICK) is RiskClass.R1
        assert effective_change_risk_class(contract, Route.FOUNDATION) is RiskClass.R3


# --- control-point bindings (ADR-023 p.4) ---


def test_control_point_bindings_cover_every_point() -> None:
    assert set(CONTROL_POINT_BINDING) == set(ControlPoint)


@pytest.mark.parametrize(
    ("point", "stage", "gate"),
    [
        (ControlPoint.PROBLEM, Stage.SPECIFICATION, Gate.SPECIFICATION),
        (ControlPoint.SOLUTION, Stage.PLANNING, Gate.PLANNING),
        (ControlPoint.UX, Stage.SPECIFICATION, Gate.UI),
        (ControlPoint.DISCOVERY_RELEASE, Stage.REVIEW_VERIFICATION, Gate.REVIEW),
    ],
)
def test_control_point_is_bound_to_an_existing_gate(
    point: ControlPoint, stage: Stage, gate: Gate
) -> None:
    assert CONTROL_POINT_BINDING[point] == ControlPointBinding(stage=stage, gate=gate)


# --- required control points ---


@pytest.mark.parametrize(
    ("route", "stage", "risk_class", "expected"),
    [
        (
            Route.STANDARD,
            Stage.SPECIFICATION,
            RiskClass.R0,
            {ControlPoint.PROBLEM, ControlPoint.UX},
        ),
        (
            Route.STANDARD,
            Stage.SPECIFICATION,
            RiskClass.R4,
            {ControlPoint.PROBLEM, ControlPoint.UX},
        ),
        (Route.QUICK, Stage.SPECIFICATION, RiskClass.R0, {ControlPoint.PROBLEM}),
        (
            Route.STANDARD,
            Stage.REVIEW_VERIFICATION,
            RiskClass.R0,
            {ControlPoint.DISCOVERY_RELEASE},
        ),
        (
            Route.STANDARD,
            Stage.REVIEW_VERIFICATION,
            RiskClass.R4,
            {ControlPoint.DISCOVERY_RELEASE},
        ),
        (Route.STANDARD, Stage.PLANNING, RiskClass.R1, set()),
        (Route.STANDARD, Stage.PLANNING, RiskClass.R2, {ControlPoint.SOLUTION}),
        (Route.STANDARD, Stage.CONSTRUCTION, RiskClass.R0, set()),
        (Route.STANDARD, Stage.CONSTRUCTION, RiskClass.R1, set()),
        (Route.QUICK, Stage.CONSTRUCTION, RiskClass.R1, set()),
        (Route.QUICK, Stage.PLANNING, RiskClass.R2, {ControlPoint.SOLUTION}),
        (Route.STANDARD, Stage.RELEASE, RiskClass.R4, set()),
    ],
)
def test_required_control_points(
    route: Route, stage: Stage, risk_class: RiskClass, expected: set[ControlPoint]
) -> None:
    assert required_control_points(route, stage, risk_class) == expected


# --- missing control points ---


def test_every_required_point_is_missing_without_decisions() -> None:
    for route in Route:
        for stage in Stage:
            for risk_class in RiskClass:
                required = required_control_points(route, stage, risk_class)
                assert missing_control_points(route, stage, risk_class, []) == required


def test_a_version_bound_human_approval_closes_the_point() -> None:
    decisions = [_decision(Gate.PLANNING, commit_sha=SHA)]
    missing = missing_control_points(
        Route.STANDARD, Stage.PLANNING, RiskClass.R2, decisions, sha=SHA
    )
    assert missing == frozenset()


def test_an_approval_bound_to_an_older_sha_does_not_close_the_point() -> None:
    """Version-bound approval (ADR-009 p.7): a new head invalidates the decision."""
    decisions = [_decision(Gate.PLANNING, commit_sha=OLD_SHA)]
    missing = missing_control_points(
        Route.STANDARD, Stage.PLANNING, RiskClass.R2, decisions, sha=SHA
    )
    assert missing == frozenset({ControlPoint.SOLUTION})


def test_an_unbound_approval_closes_the_point_only_when_no_sha_is_pinned() -> None:
    decisions = [_decision(Gate.PLANNING)]
    assert (
        missing_control_points(Route.STANDARD, Stage.PLANNING, RiskClass.R2, decisions)
        == frozenset()
    )
    assert missing_control_points(
        Route.STANDARD, Stage.PLANNING, RiskClass.R2, decisions, sha=SHA
    ) == frozenset({ControlPoint.SOLUTION})


@pytest.mark.parametrize(
    "decision",
    [
        _decision(Gate.PLANNING, decided_by=DecisionSource.AGENT),
        _decision(Gate.PLANNING, decided_by=DecisionSource.POLICY),
        _decision(Gate.PLANNING, outcome=DecisionOutcome.REJECTED),
        _decision(Gate.PLANNING, outcome=DecisionOutcome.WAIVED),
        _decision(Gate.UI),
    ],
    ids=["agent", "policy", "rejected", "waived", "other-gate"],
)
def test_only_an_approved_human_decision_on_the_gate_closes_it(decision: Decision) -> None:
    missing = missing_control_points(Route.STANDARD, Stage.PLANNING, RiskClass.R2, [decision])
    assert missing == frozenset({ControlPoint.SOLUTION})


def test_missing_control_points_is_deterministic() -> None:
    decisions = [_decision(Gate.PLANNING)]
    first = missing_control_points(Route.STANDARD, Stage.PLANNING, RiskClass.R2, decisions)
    second = missing_control_points(Route.STANDARD, Stage.PLANNING, RiskClass.R2, decisions)
    assert first == second
