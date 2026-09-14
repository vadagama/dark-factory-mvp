"""Stage-internal task graph: deterministic branch/join with typed outputs (T-015, FR-005, ADR-005).

pydantic-graph is used strictly inside one stage (ADR-005): the inter-stage flow stays the
table-based FSM in ``dark_factory.orchestration.flow``, and this module never touches it.
The facade wraps a pydantic-graph ``GraphBuilder`` fork/join and adds the two guarantees
the T-015 DoD calls out: parallel execution is deterministic and the join never loses a
completed branch output.

Shape of a run:

- 1..2 branches are declared on the graph; a third is rejected at construction time with
  ``TooManyBranchesError`` (FR-005 caps parallel work inside one job at two agents);
- every branch is an independent subtask: it receives only the run input and returns a
  typed, frozen output (a frozen pydantic model or frozen dataclass). The API has no
  shared state object, so mutation-based sharing is not expressible: the only way a
  branch's work leaves the branch is its return value;
- each branch output is emitted tagged with its declaration index; the pydantic-graph
  ``Join`` buffers the emissions and fires exactly once, after every branch under the
  fork has completed, so no completed output can be missed. A final step verifies the
  full index set (invariant, raises if a completed output were ever lost) and folds the
  caller's typed reducer over the outputs in declaration order;
- the aggregate returned by ``run`` is therefore a deterministic function of branch
  declaration order, never of completion order (the reducer receives outputs ordered by
  branch position even though the branches themselves complete in any order). The
  caller persists the aggregate to state exactly once; the facade writes nothing itself.

Failure policy — fail-fast: the first failing branch aborts the run with the original
exception (pydantic-graph surfaces it unchanged, not wrapped in an exception group), the
sibling branch is cancelled by the graph teardown, and no aggregate is produced or
persisted. When several branches fail, one of the original failures propagates
(completion-ordered); either way there is no aggregate.

Pure orchestration: no harness/LLM/clock/database. Dependencies are the stdlib and
pydantic-graph — core-stack infrastructure per ADR-002 — and nothing else from the
codebase. Branch dependencies (ports, clients) are closed over by the branch callables,
never passed through graph state.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, cast

from pydantic_graph import GraphBuilder, StepContext
from pydantic_graph.paths import EdgePath, EdgePathBuilder

type BranchCallable[InputT, OutputT] = Callable[[InputT], Awaitable[OutputT]]
"""An async branch subtask: the run input in, one typed output out."""

type Reducer[OutputT, AggregateT] = Callable[[AggregateT, OutputT], AggregateT]
"""Plain two-argument reducer ``(aggregate, output) -> aggregate`` folded over the
ordered branch outputs (the same shape as a plain pydantic-graph ``ReducerFunction``)."""

_MAX_BRANCHES = 2


class TooManyBranchesError(ValueError):
    """A third branch was declared — FR-005 caps parallel subtasks at two per job."""


@dataclass(frozen=True)
class Branch[InputT, OutputT]:
    """One independent parallel subtask: a name and an async callable to a typed output.

    ``call`` receives only the run input. It must not mutate the input and must return
    a frozen output; the callable closes over whatever dependencies it needs.
    """

    name: str
    call: BranchCallable[InputT, OutputT]


@dataclass(frozen=True)
class _BranchEmission[OutputT]:
    """A branch output tagged with the branch's declaration index (the determinism key)."""

    index: int
    output: OutputT


def _collect_emission[OutputT](
    current: list[_BranchEmission[OutputT]], emission: _BranchEmission[OutputT]
) -> list[_BranchEmission[OutputT]]:
    """Join reducer: buffer emissions as they arrive (completion order); order is fixed later."""
    current.append(emission)
    return current


def _branch_call[InputT, OutputT](
    branch: Branch[InputT, OutputT], index: int
) -> Callable[[StepContext], Awaitable[Any]]:
    """Graph step call for one branch: run the subtask and tag its output with ``index``.

    The declared return widens the emission to ``Awaitable[Any]``: mypy 2.3.1 cannot
    resolve pydantic-graph's ``typing_extensions`` TypeVars, so a generic return never
    matches the ``StepFunction`` protocol; ``finalize`` restores the type with a cast
    whose producer is this module's own join reducer.
    """

    async def call(ctx: StepContext) -> _BranchEmission[OutputT]:
        return _BranchEmission(index=index, output=await branch.call(ctx.inputs))

    return call


class TaskGraph[InputT, OutputT, AggregateT]:
    """Fork/join of 1..2 parallel branch subtasks aggregated by a typed reducer.

    The graph carries no state object: branches cannot write shared state, and the
    aggregate they produce through the join is the single value the caller persists.
    ``initial_factory`` is called once per run, so mutable aggregates (lists, dicts)
    are never shared between runs.
    """

    def __init__(
        self,
        *,
        name: str,
        reducer: Reducer[OutputT, AggregateT],
        initial_factory: Callable[[], AggregateT],
    ) -> None:
        """Create an empty task graph.

        Args:
            name: Stable graph name (also the pydantic-graph graph name).
            reducer: Typed reducer folded over the branch outputs in declaration order.
            initial_factory: Builds the fresh initial aggregate for every run.

        """
        self._name = name
        self._reducer = reducer
        self._initial_factory = initial_factory
        self._branches: list[Branch[InputT, OutputT]] = []

    def add_branch(self, branch: Branch[InputT, OutputT]) -> None:
        """Declare a branch before any execution; the third branch is rejected here.

        Raises:
            TooManyBranchesError: If two branches are already declared (FR-005).
            ValueError: If ``branch.name`` duplicates a declared branch name — branch
                names label the subtask in diagnostics and graph node IDs.

        """
        if len(self._branches) >= _MAX_BRANCHES:
            declared = ", ".join(repr(item.name) for item in self._branches)
            raise TooManyBranchesError(
                f"a task graph runs at most {_MAX_BRANCHES} branches: "
                f"refusing to add {branch.name!r} next to {declared}"
            )
        if any(item.name == branch.name for item in self._branches):
            raise ValueError(f"duplicate branch name: {branch.name!r} is already declared")
        self._branches.append(branch)

    async def run(self, inputs: InputT) -> AggregateT:
        """Run all declared branches concurrently and return the aggregated result.

        Every branch receives the same ``inputs`` instance and must treat it as
        immutable. The branches complete in any order; the returned aggregate is
        always the reducer folded over the outputs in declaration order. On the
        first branch failure the run aborts with that branch's original exception
        and the sibling is cancelled (fail-fast — see the module docstring).

        Args:
            inputs: The input handed to every branch; immutable by contract.

        Returns:
            The aggregate produced by ``reducer`` from all branch outputs.

        Raises:
            ValueError: If no branch was declared.
            RuntimeError: If the join lost a completed branch output (internal
                invariant — a completed branch always emits exactly one output).
            BaseException: The original exception of the first failing branch.

        """
        if not self._branches:
            raise ValueError("task graph has no branches: declare at least one branch before run()")
        # pydantic_graph classes are used unparameterized: mypy 2.3.1 cannot resolve the
        # typing_extensions TypeVars pydantic-graph 2.43 declares with infer_variance=True,
        # so subscripting them fails typecheck; the wiring below stays unchecked and the
        # facade's own signatures carry the typing contract. Module-level async defs match
        # the StepFunction protocol, closures do not — hence the call-overload ignore.
        builder = GraphBuilder(name=self._name, auto_instrument=False)
        steps = [
            builder.step(  # type: ignore[call-overload]
                call=_branch_call(branch, index), node_id=f"taskgraph-branch-{index}"
            )
            for index, branch in enumerate(self._branches)
        ]
        join = builder.join(_collect_emission, initial_factory=list, node_id="taskgraph-join")

        async def finalize(ctx: StepContext) -> AggregateT:
            buffered = cast(list[_BranchEmission[OutputT]], ctx.inputs)
            emissions = sorted(buffered, key=lambda emission: emission.index)
            if [emission.index for emission in emissions] != list(range(len(self._branches))):
                raise RuntimeError(
                    f"task graph {self._name!r} lost branch results: join produced "
                    f"{[emission.index for emission in emissions]}, "
                    f"expected {list(range(len(self._branches)))}"
                )
            aggregate = self._initial_factory()
            for emission in emissions:
                aggregate = self._reducer(aggregate, emission.output)
            return aggregate

        finalize_step = builder.step(call=finalize, node_id="taskgraph-finalize")

        def fork_paths(eb: EdgePathBuilder) -> list[EdgePath]:
            return [eb.to(step) for step in steps]

        fork_edge = builder.edge_from(builder.start_node).broadcast(
            fork_paths, fork_id="taskgraph-fork"
        )
        builder.add(fork_edge)
        for step in steps:
            builder.add_edge(step, join)
        builder.add_edge(join, finalize_step)
        builder.add_edge(finalize_step, builder.end_node)
        graph = builder.build()
        aggregate: AggregateT = await graph.run(inputs=inputs)
        return aggregate
