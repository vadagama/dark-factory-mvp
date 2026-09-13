"""Gate policy per route and stage (T-004, vision 3.9)."""

import pytest

from dark_factory.changes.enums import Gate, GateStatus, Route, Stage
from dark_factory.changes.findings import GateResult
from dark_factory.rules.gates import required_gates, unsatisfied_gates

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


def test_gate_stage_binding() -> None:
    assert required_gates(Route.STANDARD, Stage.SPECIFICATION) == {Gate.SPECIFICATION}
    assert required_gates(Route.STANDARD, Stage.PLANNING) == {Gate.PLANNING}
    assert required_gates(Route.STANDARD, Stage.CONSTRUCTION) == {Gate.CODE, Gate.UI}
    assert required_gates(Route.STANDARD, Stage.REVIEW_VERIFICATION) == {
        Gate.REVIEW,
        Gate.VERIFICATION,
    }
    assert required_gates(Route.STANDARD, Stage.RELEASE) == {Gate.RELEASE}


def _gate_result(gate: Gate, status: GateStatus) -> GateResult:
    return GateResult(gate=gate, status=status, sha="731ac91")


@pytest.mark.parametrize(
    ("results", "expected"),
    [
        ([], [Gate.CODE, Gate.UI]),
        ([_gate_result(Gate.CODE, GateStatus.PASSED)], [Gate.UI]),
        (
            [
                _gate_result(Gate.CODE, GateStatus.PASSED),
                _gate_result(Gate.UI, GateStatus.SKIPPED),
            ],
            [],
        ),
        (
            [
                _gate_result(Gate.CODE, GateStatus.PASSED),
                _gate_result(Gate.UI, GateStatus.FAILED),
            ],
            [Gate.UI],
        ),
        (
            [
                _gate_result(Gate.CODE, GateStatus.PASSED),
                _gate_result(Gate.UI, GateStatus.PENDING),
            ],
            [Gate.UI],
        ),
        # The last result per gate wins: a newer failure supersedes an older pass.
        (
            [
                _gate_result(Gate.CODE, GateStatus.PASSED),
                _gate_result(Gate.UI, GateStatus.PASSED),
                _gate_result(Gate.UI, GateStatus.FAILED),
            ],
            [Gate.UI],
        ),
        (
            [
                _gate_result(Gate.CODE, GateStatus.PASSED),
                _gate_result(Gate.UI, GateStatus.FAILED),
                _gate_result(Gate.UI, GateStatus.PASSED),
            ],
            [],
        ),
    ],
)
def test_unsatisfied_gates_on_standard_construction(
    results: list[GateResult], expected: list[Gate]
) -> None:
    assert unsatisfied_gates(Route.STANDARD, Stage.CONSTRUCTION, results) == expected


def test_unsatisfied_gates_are_ordered_by_gate_value() -> None:
    assert unsatisfied_gates(Route.STANDARD, Stage.REVIEW_VERIFICATION, []) == [
        Gate.REVIEW,
        Gate.VERIFICATION,
    ]
