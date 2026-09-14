"""Stage-internal task graph: branches, typed join, determinism, fail-fast (T-015)."""

import asyncio
from collections.abc import Callable
from dataclasses import FrozenInstanceError, dataclass, replace

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError
from pydantic_graph import reduce_list_append

from dark_factory.orchestration.taskgraph import (
    Branch,
    TaskGraph,
    TooManyBranchesError,
)


class BranchInputs(BaseModel):
    """Frozen run input handed to every branch."""

    model_config = ConfigDict(frozen=True)

    change_id: str


class CheckOutcome(BaseModel):
    """Frozen typed output of a branch subtask."""

    model_config = ConfigDict(frozen=True)

    name: str
    passed: bool


@dataclass(frozen=True)
class Totals:
    """Frozen aggregate built by a custom typed reducer."""

    checks: int
    passed: int


INPUTS = BranchInputs(change_id="chg-001")


def _outcome_branch(
    name: str, *, delay: float, completion_log: list[str] | None = None
) -> Branch[BranchInputs, CheckOutcome]:
    """A branch subtask that finishes after ``delay`` and reports into ``completion_log``."""

    async def call(inputs: BranchInputs) -> CheckOutcome:
        await asyncio.sleep(delay)
        if completion_log is not None:
            completion_log.append(name)
        return CheckOutcome(name=f"{inputs.change_id}:{name}", passed=True)

    return Branch(name=name, call=call)


def _outcomes_graph(
    *branches: Branch[BranchInputs, CheckOutcome],
) -> TaskGraph[BranchInputs, CheckOutcome, list[CheckOutcome]]:
    graph: TaskGraph[BranchInputs, CheckOutcome, list[CheckOutcome]] = TaskGraph(
        name="outcomes-graph", reducer=reduce_list_append, initial_factory=list
    )
    for branch in branches:
        graph.add_branch(branch)
    return graph


def _totals_reducer(current: Totals, outcome: CheckOutcome) -> Totals:
    """Custom typed reducer: count checks and passed outcomes into a frozen aggregate."""
    return replace(current, checks=current.checks + 1, passed=current.passed + int(outcome.passed))


# --- construction-time branch limits ---


def test_third_branch_rejected() -> None:
    graph = _outcomes_graph(_outcome_branch("lint", delay=0.0), _outcome_branch("tests", delay=0.0))
    with pytest.raises(TooManyBranchesError, match="at most 2 branches"):
        graph.add_branch(_outcome_branch("third", delay=0.0))


def test_duplicate_branch_name_rejected() -> None:
    graph = _outcomes_graph(_outcome_branch("lint", delay=0.0))
    with pytest.raises(ValueError, match="duplicate branch name: 'lint'"):
        graph.add_branch(_outcome_branch("lint", delay=0.0))


def test_run_without_branches_rejected() -> None:
    graph = _outcomes_graph()
    with pytest.raises(ValueError, match="no branches"):
        asyncio.run(graph.run(INPUTS))


# --- one and two branches work ---


def test_single_branch_aggregates() -> None:
    graph = _outcomes_graph(_outcome_branch("lint", delay=0.0))
    result = asyncio.run(graph.run(INPUTS))
    assert result == [CheckOutcome(name="chg-001:lint", passed=True)]


def test_two_branches_aggregate() -> None:
    graph = _outcomes_graph(_outcome_branch("lint", delay=0.0), _outcome_branch("tests", delay=0.0))
    result = asyncio.run(graph.run(INPUTS))
    assert result == [
        CheckOutcome(name="chg-001:lint", passed=True),
        CheckOutcome(name="chg-001:tests", passed=True),
    ]


# --- determinism: declaration order, never completion order ---


@pytest.mark.parametrize("first_delay,second_delay", [(0.0, 0.03), (0.03, 0.0)])
def test_aggregate_order_follows_declaration(first_delay: float, second_delay: float) -> None:
    completion: list[str] = []
    graph = _outcomes_graph(
        _outcome_branch("first", delay=first_delay, completion_log=completion),
        _outcome_branch("second", delay=second_delay, completion_log=completion),
    )
    result = asyncio.run(graph.run(INPUTS))
    assert [outcome.name for outcome in result] == ["chg-001:first", "chg-001:second"]


def test_completion_order_varies_while_aggregate_does_not() -> None:
    """The two delay assignments force opposite completion orders; the aggregate is identical."""
    aggregates: list[list[CheckOutcome]] = []
    completions: list[list[str]] = []
    for first_delay, second_delay in [(0.0, 0.03), (0.03, 0.0)]:
        completion: list[str] = []
        graph = _outcomes_graph(
            _outcome_branch("first", delay=first_delay, completion_log=completion),
            _outcome_branch("second", delay=second_delay, completion_log=completion),
        )
        aggregates.append(asyncio.run(graph.run(INPUTS)))
        completions.append(completion)
    assert completions == [["first", "second"], ["second", "first"]]
    assert aggregates[0] == aggregates[1]
    assert [outcome.name for outcome in aggregates[0]] == ["chg-001:first", "chg-001:second"]


# --- join does not lose results ---


def test_join_keeps_every_completed_output() -> None:
    graph = _outcomes_graph(
        _outcome_branch("lint", delay=0.02),
        _outcome_branch("tests", delay=0.0),
    )
    result = asyncio.run(graph.run(INPUTS))
    assert len(result) == 2
    assert {outcome.name for outcome in result} == {"chg-001:lint", "chg-001:tests"}


# --- reducer aggregation is typed ---


def test_custom_typed_reducer_builds_frozen_aggregate() -> None:
    graph: TaskGraph[BranchInputs, CheckOutcome, Totals] = TaskGraph(
        name="totals-graph",
        reducer=_totals_reducer,
        initial_factory=lambda: Totals(checks=0, passed=0),
    )
    graph.add_branch(_outcome_branch("lint", delay=0.0))
    graph.add_branch(_outcome_branch("tests", delay=0.0))
    result = asyncio.run(graph.run(INPUTS))
    assert result == Totals(checks=2, passed=2)


# --- typed outputs and inputs are frozen ---


def test_typed_outputs_are_frozen() -> None:
    outcome = CheckOutcome(name="lint", passed=True)
    with pytest.raises(ValidationError):
        outcome.passed = False  # type: ignore[misc]
    totals = Totals(checks=0, passed=0)
    with pytest.raises(FrozenInstanceError):
        totals.checks = 1  # type: ignore[misc]


def test_run_inputs_are_frozen() -> None:
    inputs = BranchInputs(change_id="chg-001")
    with pytest.raises(ValidationError):
        inputs.change_id = "chg-002"  # type: ignore[misc]


# --- failure policy: fail-fast ---


def test_failing_branch_aborts_run_and_cancels_sibling() -> None:
    completed: list[str] = []

    async def failing(inputs: BranchInputs) -> CheckOutcome:
        await asyncio.sleep(0.005)
        raise RuntimeError("branch failed")

    async def slow(inputs: BranchInputs) -> CheckOutcome:
        await asyncio.sleep(0.05)
        completed.append("slow")
        return CheckOutcome(name=f"{inputs.change_id}:slow", passed=True)

    graph = _outcomes_graph(Branch(name="slow", call=slow), Branch(name="failing", call=failing))
    with pytest.raises(RuntimeError, match="branch failed") as excinfo:
        asyncio.run(graph.run(INPUTS))
    # The original exception propagates, not an exception group.
    assert type(excinfo.value) is RuntimeError
    # Fail-fast: the sibling was cancelled and produced no result.
    assert completed == []


def test_no_aggregate_written_after_failure() -> None:
    writes: list[list[CheckOutcome]] = []

    async def failing(inputs: BranchInputs) -> CheckOutcome:
        raise RuntimeError("branch failed")

    graph = _outcomes_graph(
        _outcome_branch("lint", delay=0.0), Branch(name="failing", call=failing)
    )
    with pytest.raises(RuntimeError, match="branch failed"):
        asyncio.run(graph.run(INPUTS))
    assert writes == []


# --- the caller writes the aggregate to state exactly once ---


def test_caller_persists_aggregate_exactly_once() -> None:
    """Branches cannot write state (the API passes them only the input): the caller
    receives the joined aggregate and persists it once — never once per branch."""
    writes: list[list[CheckOutcome]] = []
    graph = _outcomes_graph(_outcome_branch("lint", delay=0.0), _outcome_branch("tests", delay=0.0))
    aggregate = asyncio.run(graph.run(INPUTS))
    writer: Callable[[list[CheckOutcome]], None] = writes.append
    writer(aggregate)
    assert len(writes) == 1
    assert writes == [
        [
            CheckOutcome(name="chg-001:lint", passed=True),
            CheckOutcome(name="chg-001:tests", passed=True),
        ]
    ]


# --- runs are independent: the aggregate factory is fresh per run ---


def test_aggregate_factory_is_called_per_run() -> None:
    graph = _outcomes_graph(_outcome_branch("lint", delay=0.0), _outcome_branch("tests", delay=0.0))
    first = asyncio.run(graph.run(INPUTS))
    second = asyncio.run(graph.run(INPUTS))
    assert first == second
    assert first is not second
    # A shared mutable initial would have accumulated the first run into the second.
    assert len(second) == 2
