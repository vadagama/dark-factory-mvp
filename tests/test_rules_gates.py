"""Gate policy per route and stage (T-004, vision 3.9)."""

import pytest

from dark_factory.changes.enums import Gate, GateStatus, Route, Stage
from dark_factory.changes.findings import GateResult
from dark_factory.orchestration.rules.gates import required_gates, unsatisfied_gates

ALL_GATES: frozenset[Gate] = frozenset(Gate)


def test_standard_route_covers_all_seven_mvp_gates() -> None:
    bound: set[Gate] = set()
    for stage in Stage:
        bound |= required_gates(Route.STANDARD, stage)
    assert bound == ALL_GATES
    assert len(ALL_GATES) == 7


def test_quick_route_skips_only_the_ui_gate() -> None:
    standard = {gate for stage in Stage for gate in required_gates(Route.STANDARD, stage)}
    quick = {gate for stage in Stage for gate in required_gates(Route.QUICK, stage)}
    assert standard - quick == {Gate.UI}


def test_ui_gate_is_required_on_specification_by_every_route_but_quick() -> None:
    """Absolute, not relative: every route except ``quick`` carries the UI gate (ADR-023 p.6).

    Since ADR-029 p.1 the gate sits on the design stage, so construction carries
    it on no route at all.
    """
    for route in Route:
        ui_required = Gate.UI in required_gates(route, Stage.SPECIFICATION)
        assert ui_required is (route is not Route.QUICK), route.value
        assert Gate.UI not in required_gates(route, Stage.CONSTRUCTION), route.value


def test_every_route_but_quick_covers_all_seven_mvp_gates() -> None:
    for route in Route:
        bound = {gate for stage in Stage for gate in required_gates(route, stage)}
        expected = ALL_GATES - {Gate.UI} if route is Route.QUICK else ALL_GATES
        assert bound == expected, route.value


def test_gate_stage_binding() -> None:
    assert required_gates(Route.STANDARD, Stage.SPECIFICATION) == {Gate.SPECIFICATION, Gate.UI}
    assert required_gates(Route.STANDARD, Stage.PLANNING) == {Gate.PLANNING}
    assert required_gates(Route.STANDARD, Stage.CONSTRUCTION) == {Gate.CODE}
    assert required_gates(Route.STANDARD, Stage.REVIEW_VERIFICATION) == {
        Gate.REVIEW,
        Gate.VERIFICATION,
    }
    assert required_gates(Route.STANDARD, Stage.RELEASE) == {Gate.RELEASE}
    assert required_gates(Route.QUICK, Stage.SPECIFICATION) == {Gate.SPECIFICATION}


def _gate_result(gate: Gate, status: GateStatus) -> GateResult:
    return GateResult(gate=gate, status=status, sha="731ac91")


@pytest.mark.parametrize(
    ("results", "expected"),
    [
        ([], [Gate.CODE]),
        ([_gate_result(Gate.CODE, GateStatus.PASSED)], []),
        ([_gate_result(Gate.CODE, GateStatus.SKIPPED)], []),
        ([_gate_result(Gate.CODE, GateStatus.FAILED)], [Gate.CODE]),
        ([_gate_result(Gate.CODE, GateStatus.PENDING)], [Gate.CODE]),
        # The last result per gate wins: a newer failure supersedes an older pass.
        (
            [
                _gate_result(Gate.CODE, GateStatus.PASSED),
                _gate_result(Gate.CODE, GateStatus.FAILED),
            ],
            [Gate.CODE],
        ),
        (
            [
                _gate_result(Gate.CODE, GateStatus.FAILED),
                _gate_result(Gate.CODE, GateStatus.PASSED),
            ],
            [],
        ),
    ],
)
def test_unsatisfied_gates_on_standard_construction(
    results: list[GateResult], expected: list[Gate]
) -> None:
    """Construction carries the machine gate ``code`` alone (ADR-029 p.1)."""
    assert unsatisfied_gates(Route.STANDARD, Stage.CONSTRUCTION, results) == expected


@pytest.mark.parametrize(
    ("results", "expected"),
    [
        ([], [Gate.SPECIFICATION, Gate.UI]),
        ([_gate_result(Gate.SPECIFICATION, GateStatus.PASSED)], [Gate.UI]),
        (
            [
                _gate_result(Gate.SPECIFICATION, GateStatus.PASSED),
                _gate_result(Gate.UI, GateStatus.PASSED),
            ],
            [],
        ),
        (
            [
                _gate_result(Gate.SPECIFICATION, GateStatus.PASSED),
                _gate_result(Gate.UI, GateStatus.FAILED),
            ],
            [Gate.UI],
        ),
        # A skipped gate was evaluated as not applicable, so it does not block.
        (
            [
                _gate_result(Gate.SPECIFICATION, GateStatus.PASSED),
                _gate_result(Gate.UI, GateStatus.SKIPPED),
            ],
            [],
        ),
    ],
)
def test_unsatisfied_gates_on_standard_specification(
    results: list[GateResult], expected: list[Gate]
) -> None:
    """The design stage requires the specification and the UI gate (ADR-029 p.1)."""
    assert unsatisfied_gates(Route.STANDARD, Stage.SPECIFICATION, results) == expected


def test_unsatisfied_gates_are_ordered_by_gate_value() -> None:
    assert unsatisfied_gates(Route.STANDARD, Stage.REVIEW_VERIFICATION, []) == [
        Gate.REVIEW,
        Gate.VERIFICATION,
    ]
