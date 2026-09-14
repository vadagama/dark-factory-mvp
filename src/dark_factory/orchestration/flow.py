"""Inter-stage Factory Flow: the deterministic transition core (T-004, ADR-005).

Between stages the factory is a table-driven FSM, not a graph (ADR-005 p.2):
the single source of allowed transitions is the immutable ``FLOW_TRANSITIONS``
table, and a transition is applied by exactly one function (``apply_result``),
which verifies the ``(stage, NextAction)`` pair against the table and rejects
a missing pair with an explicit error instead of undefined behaviour.
Continuation between CI jobs happens through immutable ``StageResult`` /
``NextAction`` artifacts (ADR-006 p.2); this module is pure domain logic —
no harness/LLM calls and no database access.

Guarantees are layered (ADR-005 p.2):

- types: ``NextAction`` is a closed union; the handlers cover every variant and
  end with ``assert_never`` (enforced by mypy strict);
- runtime: out-of-table transitions raise ``InvalidFlowTransition``;
- tests: the table is traversed exhaustively, including dead-edge detection
  (see ``tests/test_flow_transitions.py``).

Rework and budget limits live in ``dark_factory.rules.limits``, gate policy in
``dark_factory.rules.gates``, route topology in ``dark_factory.flows.routes``,
escalation policy (T-016, ADR-018 p.5) in ``dark_factory.orchestration.policy``
and merge policy (T-026, ADR-011 p.2) in
``dark_factory.orchestration.policy.merge``. Stop conditions deterministically
end in ``Blocked`` (FR-008, ADR-018 p.5).
Stage-internal execution (TaskGraph, pydantic-graph) is T-015 and deliberately
out of scope here.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Final, Literal, assert_never

from dark_factory.changes.enums import RunStatus, Stage, StageStatus, StopOutcome
from dark_factory.changes.findings import GateResult
from dark_factory.changes.next_action import (
    ExecuteStageAction,
    MergeAction,
    NextAction,
    ReleaseAction,
    RequestApprovalAction,
    ReworkAction,
    StopAction,
    WaitForCIAction,
    WaitForInputAction,
)
from dark_factory.changes.run import (
    RUN_TERMINAL_STATUSES,
    STAGE_TERMINAL_STATUSES,
    ChangeRun,
    StageResult,
    StageRun,
    completion_violations,
)
from dark_factory.changes.usage import BudgetSnapshot, Usage
from dark_factory.flows.routes import route_profile
from dark_factory.orchestration.policy.escalation import (
    autonomy_budget_violation,
    contract_entry_violation,
    escalation_stop_reason,
)
from dark_factory.orchestration.policy.merge import (
    DEFAULT_MERGE_POLICY,
    MergeDecision,
    MergePolicy,
    MergeRequestContext,
    evaluate_merge,
)
from dark_factory.rules.gates import unsatisfied_gates
from dark_factory.rules.limits import continuation_violations, rework_violation

type NextActionType = Literal[
    "execute_stage",
    "wait_for_input",
    "wait_for_ci",
    "rework",
    "request_approval",
    "merge",
    "release",
    "stop",
]
"""Discriminator values of the closed ``NextAction`` union (ADR-005 p.2)."""

FLOW_TRANSITIONS: Final[dict[Stage, frozenset[NextActionType]]] = {
    Stage.SPECIFICATION: frozenset(
        {"execute_stage", "wait_for_input", "rework", "request_approval", "stop"}
    ),
    Stage.PLANNING: frozenset(
        {"execute_stage", "wait_for_input", "rework", "request_approval", "stop"}
    ),
    Stage.CONSTRUCTION: frozenset(
        {"execute_stage", "wait_for_input", "wait_for_ci", "rework", "request_approval", "stop"}
    ),
    # Review/verification completes through ``merge``: the change request is
    # merged per policy (human-confirmed in MVP, ADR-011), which unlocks the
    # release stage. There is no execute_stage shortcut past merge policy.
    Stage.REVIEW_VERIFICATION: frozenset(
        {"merge", "wait_for_input", "wait_for_ci", "rework", "request_approval", "stop"}
    ),
    # Deploy to dev is human-off-the-loop in MVP (ADR-018 p.1): the release
    # stage waits only for pipelines; failures escalate via stop.
    Stage.RELEASE: frozenset({"release", "wait_for_ci", "stop"}),
}

# Where a rework round sends the work (T-014: review -> rework -> re-review).
# Findings from review/verification are fixed by implementation, so the round
# re-enters construction; other stages rework themselves. The mapping is total
# over stages for lookup totality, but only stages whose table set contains
# ``rework`` are ever consulted.
REWORK_TARGET: Final[dict[Stage, Stage]] = {
    Stage.SPECIFICATION: Stage.SPECIFICATION,
    Stage.PLANNING: Stage.PLANNING,
    Stage.CONSTRUCTION: Stage.CONSTRUCTION,
    Stage.REVIEW_VERIFICATION: Stage.CONSTRUCTION,
    Stage.RELEASE: Stage.RELEASE,
}

# Stage/run status pair applied for each StopAction outcome (T-004: stop
# conditions end in Blocked; failed and canceled map to their own terminals).
# A canceled stop cannot be carried by a StageResult (result statuses have no
# canceled value, T-003): the runner applies cancellation directly through
# ``ChangeRun.apply_status``; the mapping stays total for exhaustiveness.
_STOP_STATUS: Final[dict[StopOutcome, tuple[StageStatus, RunStatus]]] = {
    StopOutcome.BLOCKED: (StageStatus.BLOCKED, RunStatus.BLOCKED),
    StopOutcome.FAILED: (StageStatus.FAILED, RunStatus.FAILED),
    StopOutcome.CANCELED: (StageStatus.CANCELED, RunStatus.CANCELED),
}

MERGE_POLICY: Final[MergePolicy] = DEFAULT_MERGE_POLICY
"""Merge policy consulted by the ``MergeAction`` handler (T-026).

MVP default: manual mode — every merge waits for a version-bound human
approval (ADR-011 p.2); auto-merge by the trusted finalizer requires the
policy to list the risk class explicitly (FR-010) and stays disabled until
T-085 wires deployment configuration here.
"""


class InvalidFlowTransition(ValueError):
    """A (stage, NextAction) pair outside ``FLOW_TRANSITIONS`` was requested."""


class FlowStateError(ValueError):
    """The run/stage state is inconsistent with the requested flow transition."""


@dataclass(frozen=True)
class FlowDecision:
    """Outcome of applying one ``StageResult`` to the flow.

    ``action`` is the effective action: the requested one when honored, or a
    synthesized action (``StopAction`` for a blocked transition, or
    ``WaitForInputAction`` when the merge policy defers the merge to a human,
    T-026). ``next_stage`` is the stage to execute next, if the flow advances.
    """

    stage: Stage
    action: NextAction
    stage_status: StageStatus
    run_status: RunStatus
    next_stage: Stage | None


def allowed_actions(stage: Stage) -> frozenset[NextActionType]:
    """Actions the table allows from ``stage`` (read-only view of the table)."""
    return FLOW_TRANSITIONS[stage]


def expected_result_status(action: NextAction) -> StageStatus:
    """The ``StageResult.status`` that must accompany ``action``.

    The runner (US1) serializes this pairing; the engine rejects a mismatched
    result so the two fields of the versioned contract cannot diverge.

    A ``StopAction`` with outcome ``canceled`` has no representable result
    status (result statuses carry no canceled value, T-003): cancellation is
    applied by the runner directly through ``ChangeRun.apply_status``, and the
    corresponding ``StageResult`` is rejected by its validator before the
    engine sees it. The mapping stays total for union exhaustiveness.
    """
    match action:
        case ExecuteStageAction() | MergeAction() | ReleaseAction():
            return StageStatus.SUCCEEDED
        case WaitForInputAction() | WaitForCIAction() | RequestApprovalAction():
            return StageStatus.WAITING
        case ReworkAction():
            # The attempt ended with findings/failures; a rework round is a
            # new attempt or a new input revision, not a success.
            return StageStatus.FAILED
        case StopAction(outcome=outcome):
            return _STOP_STATUS[outcome][0]
        case _:
            assert_never(action)


def apply_result(
    run: ChangeRun,
    result: StageResult,
    *,
    history: Sequence[StageResult] = (),
    now: datetime | None = None,
    merge_context: MergeRequestContext | None = None,
) -> FlowDecision:
    """Apply one stage result to the run: the single flow transition point.

    Verifies the ``(result.stage, result.next_action)`` pair against
    ``FLOW_TRANSITIONS`` and raises ``InvalidFlowTransition`` for a pair
    outside the table. Status changes go through ``ChangeRun.apply_status`` /
    ``StageRun.apply_status`` — the single status transition points (T-003).

    ``history`` carries the stage results produced earlier in the run; it is
    used to check the completion invariants (ADR-009 p.9) when the flow
    reaches a successful terminal state. ``now`` overrides the wall clock for
    the deadline limit (determinism in tests and reconciliation replays).

    ``merge_context`` carries the merge facts (executor, risk class, SHAs,
    human approvals) the ``MergeAction`` handler needs to consult the merge
    policy (T-026). The flow anchors ``route``, ``stage`` and
    ``gate_results`` of the context to the run and the result — the caller
    contributes only executor, risk class, SHAs and approvals. Without a
    context the merge parks the run in Waiting: absent merge facts mean
    manual mode, the machine never merges on missing data (FR-010).

    Persisting the run and the result before an external wait is the caller's
    duty (ADR-006 p.8); double application is prevented upstream by the
    idempotency keys (ADR-006 p.3), not here.
    """
    if run.status in RUN_TERMINAL_STATUSES:
        raise FlowStateError(
            f"run {run.id!r} is terminal ({run.status.value}); no transitions allowed"
        )
    allowed = FLOW_TRANSITIONS.get(result.stage)
    if allowed is None or result.next_action.type not in allowed:
        raise InvalidFlowTransition(
            f"Flow transition {result.stage.value} -> {result.next_action.type} is not allowed"
        )
    expected_status = expected_result_status(result.next_action)
    if result.status != expected_status:
        raise FlowStateError(
            f"stage {result.stage.value} result status {result.status.value!r} does not match "
            f"next action {result.next_action.type!r} (expected {expected_status.value!r})"
        )
    reference_now = now if now is not None else datetime.now(UTC)
    stage_run = _ensure_stage_run(run, result)
    if run.status is not RunStatus.RUNNING:
        run.apply_status(RunStatus.RUNNING)
    return _handle_action(run, stage_run, result, history, reference_now, merge_context)


def _handle_action(
    run: ChangeRun,
    stage_run: StageRun,
    result: StageResult,
    history: Sequence[StageResult],
    now: datetime,
    merge_context: MergeRequestContext | None,
) -> FlowDecision:
    """Apply the effects of one honored-or-blocked action; exhaustively checked."""
    budget = run.budget
    _accumulate_usage(budget, result.usage)
    match result.next_action:
        case ExecuteStageAction(next_stage=next_stage):
            expected = route_profile(run.route).next_stage(result.stage)
            if expected is None or next_stage != expected:
                shown = expected.value if expected is not None else "<none>"
                raise InvalidFlowTransition(
                    f"stage {result.stage.value} on route {run.route.value} must advance to "
                    f"{shown}, got {next_stage.value}"
                )
            reason = _block_reason(run, result.stage, result.gate_results, now)
            if reason is None:
                reason = _escalation_reason(run, result, target=next_stage)
            if reason is not None:
                return _stop(stage_run, run, reason)
            _advance(run, stage_run, next_stage)
            return FlowDecision(
                stage=result.stage,
                action=result.next_action,
                stage_status=StageStatus.SUCCEEDED,
                run_status=run.status,
                next_stage=next_stage,
            )
        case WaitForInputAction():
            # The StageResult must be persisted before the job ends (ADR-006 p.8).
            stage_run.apply_status(StageStatus.WAITING)
            run.apply_status(RunStatus.WAITING)
            return _decision(stage_run, run, result.next_action, None)
        case WaitForCIAction():
            stage_run.apply_status(StageStatus.WAITING)
            run.apply_status(RunStatus.WAITING)
            return _decision(stage_run, run, result.next_action, None)
        case ReworkAction(round=requested_round):
            # Declared escalations and the autonomy budget veto a rework round
            # without burning one: an escalation is not a rework iteration.
            reason = escalation_stop_reason(result.escalations)
            if reason is None:
                reason = _autonomy_budget_reason(run)
            if reason is not None:
                return _stop(stage_run, run, reason)
            violation = rework_violation(budget, requested_round=requested_round)
            if violation is not None:
                return _stop(stage_run, run, violation.reason)
            budget.used_rework_rounds += 1
            stage_run.apply_status(StageStatus.FAILED)
            rework_target = REWORK_TARGET[result.stage]
            _start(run, rework_target)
            return _decision(stage_run, run, result.next_action, rework_target)
        case RequestApprovalAction():
            # Human gate (ADR-018): the flow waits for the approval decision.
            stage_run.apply_status(StageStatus.WAITING)
            run.apply_status(RunStatus.WAITING)
            return _decision(stage_run, run, result.next_action, None)
        case MergeAction():
            target = route_profile(run.route).next_stage(result.stage)
            if target is None:
                raise FlowStateError(
                    f"no stage follows {result.stage.value} on route {run.route.value}"
                )
            # Stage gates (SHA-blind) and escalations block first: their stop
            # reasons are preserved for existing callers. The merge policy then
            # adds the merge-specific preconditions on the final SHA (T-026).
            reason = _block_reason(run, result.stage, result.gate_results, now)
            if reason is None:
                reason = escalation_stop_reason(result.escalations)
            if reason is not None:
                return _stop(stage_run, run, reason)
            decision = _merge_policy_decision(run, result, merge_context)
            if decision.kind == "blocked":
                return _stop(stage_run, run, decision.reason or "merge blocked by merge policy")
            if decision.kind == "manual_merge_required":
                return _wait_for_human_merge(stage_run, run, decision.reason)
            # human_merge_authorized / finalizer_merge_allowed: the merge is
            # authorized; the flow advances and the runner executes the merge
            # with the trusted-finalizer credential set — agent pods carry none
            # (FR-023; the human merge is observed on the provider, ADR-011 p.2).
            _advance(run, stage_run, target)
            return _decision(stage_run, run, result.next_action, target)
        case ReleaseAction():
            reason = _block_reason(run, result.stage, result.gate_results, now)
            if reason is None:
                reason = escalation_stop_reason(result.escalations)
            if reason is not None:
                return _stop(stage_run, run, reason)
            violations = completion_violations(
                run.model_copy(update={"status": RunStatus.SUCCEEDED}), [*history, result]
            )
            if violations:
                return _stop(stage_run, run, "; ".join(violations))
            stage_run.apply_status(StageStatus.SUCCEEDED)
            run.apply_status(RunStatus.SUCCEEDED)
            return _decision(stage_run, run, result.next_action, None)
        case StopAction():
            stage_status, run_status = _STOP_STATUS[result.next_action.outcome]
            stage_run.apply_status(stage_status)
            run.apply_status(run_status)
            return _decision(stage_run, run, result.next_action, None)
        case _:
            assert_never(result.next_action)


def _merge_policy_decision(
    run: ChangeRun,
    result: StageResult,
    merge_context: MergeRequestContext | None,
) -> MergeDecision:
    """Consult the merge policy for one ``MergeAction`` result (T-026).

    The context is anchored to the run: route, stage and the attempt's gate
    results are authoritative — the caller cannot widen or shrink the gate
    set by shaping its context. Without a context the policy is not consulted
    at all: absent merge facts mean manual mode (FR-010) — the run waits for
    the human merge instead of advancing on missing data.
    """
    if merge_context is None:
        return MergeDecision(
            kind="manual_merge_required",
            reason="no merge context was provided with the merge result (FR-010: manual mode)",
        )
    anchored = replace(
        merge_context,
        route=run.route,
        stage=result.stage,
        gate_results=result.gate_results,
    )
    return evaluate_merge(anchored, policy=MERGE_POLICY)


def _wait_for_human_merge(stage_run: StageRun, run: ChangeRun, reason: str | None) -> FlowDecision:
    """Park the run in Waiting for the human merge (ADR-011 p.2, T-026).

    Same mechanics as a ``WaitForInput`` result: the StageResult is persisted
    before the job ends (ADR-006 p.8) and the flow resumes when a human merge
    authorization bound to the final SHA is recorded.
    """
    detail = reason or "the merge policy requires the human merge"
    action = WaitForInputAction(reason=f"waiting for human merge (ADR-011 p.2): {detail}")
    stage_run.apply_status(StageStatus.WAITING)
    run.apply_status(RunStatus.WAITING)
    return _decision(stage_run, run, action, None)


def _decision(
    stage_run: StageRun,
    run: ChangeRun,
    action: NextAction,
    next_stage: Stage | None,
) -> FlowDecision:
    return FlowDecision(
        stage=stage_run.stage,
        action=action,
        stage_status=stage_run.status,
        run_status=run.status,
        next_stage=next_stage,
    )


def _stop(stage_run: StageRun, run: ChangeRun, reason: str) -> FlowDecision:
    """Deterministic stop: limits, gates and invariants end in Blocked (T-004)."""
    action = StopAction(outcome=StopOutcome.BLOCKED, reason=reason)
    stage_run.apply_status(StageStatus.BLOCKED)
    run.apply_status(RunStatus.BLOCKED)
    return _decision(stage_run, run, action, None)


def _block_reason(
    run: ChangeRun,
    stage: Stage,
    gate_results: Sequence[GateResult],
    now: datetime,
) -> str | None:
    """First blocking reason for advancing past ``stage``, if any.

    Gates are checked before budgets: stage quality diagnostics win over
    spend diagnostics when both trigger.
    """
    unsatisfied = unsatisfied_gates(run.route, stage, gate_results)
    if unsatisfied:
        names = ", ".join(gate.value for gate in unsatisfied)
        return f"required gates not satisfied: {names}"
    violations = continuation_violations(run.budget, now=now)
    if violations:
        return "; ".join(violation.reason for violation in violations)
    return None


def _escalation_reason(run: ChangeRun, result: StageResult, *, target: Stage) -> str | None:
    """Escalation-policy reason blocking an autonomous stage advance, if any.

    Declared escalations always stop the advance. Entering construction
    additionally requires an approved Implementation Contract (T-016 DoD):
    rework only re-enters construction for runs that already passed this
    gate. Otherwise the contract's autonomy budget bounds the stage attempts
    (an iteration is one ``StageRun`` occurrence).
    """
    declared = escalation_stop_reason(result.escalations)
    if declared is not None:
        return declared
    if target is Stage.CONSTRUCTION:
        violation = contract_entry_violation(run.implementation_contract)
        if violation is not None:
            return violation.reason
    return _autonomy_budget_reason(run)


def _autonomy_budget_reason(run: ChangeRun) -> str | None:
    """Reason for exhausting the contract's autonomy budget, if any."""
    violation = autonomy_budget_violation(
        run.implementation_contract, iterations_used=len(run.stages)
    )
    return violation.reason if violation is not None else None


def _accumulate_usage(budget: BudgetSnapshot, usage: Usage | None) -> None:
    """Fold the stage usage into the run budget (FR-016: restarts never reset spend)."""
    if usage is None:
        return
    tokens = usage.total_tokens
    if tokens is None:
        tokens = usage.prompt_tokens + usage.completion_tokens
    budget.tokens_used += tokens
    if usage.cost is not None:
        budget.cost_used += usage.cost


def _ensure_stage_run(run: ChangeRun, result: StageResult) -> StageRun:
    """Find the active stage run for the result or start a fresh occurrence.

    A result for a stage without an active (non-terminal) stage run starts a
    new one — this is how the first execution of a stage enters the flow.
    A result whose attempt does not match the active one is stale and rejected
    (ADR-006 p.4: protection against outdated results).

    The active run is (re)entered as in progress before the result is applied:
    ``pending -> in_progress`` on first entry, ``waiting -> in_progress`` on
    resume — the T-003 status table has no direct ``pending/waiting ->
    succeeded`` edge, so handlers can complete the stage in one step.
    """
    active = [
        s for s in run.stages if s.stage == result.stage and s.status not in STAGE_TERMINAL_STATUSES
    ]
    if active:
        stage_run = active[-1]
        if stage_run.attempt_number != result.attempt_number:
            raise FlowStateError(
                f"stage {result.stage.value} result from attempt {result.attempt_number} "
                f"does not match active attempt {stage_run.attempt_number}"
            )
    else:
        if result.attempt_number != 1:
            raise FlowStateError(
                f"stage {result.stage.value} has no active stage run "
                f"for attempt {result.attempt_number}"
            )
        stage_run = StageRun(id=_stage_run_id(run, result.stage), stage=result.stage)
        run.stages.append(stage_run)
    if stage_run.status is not StageStatus.IN_PROGRESS:
        stage_run.apply_status(StageStatus.IN_PROGRESS)
    return stage_run


def _advance(run: ChangeRun, stage_run: StageRun, target: Stage) -> None:
    """Complete the current stage and make sure the target stage is pending."""
    stage_run.apply_status(StageStatus.SUCCEEDED)
    _start(run, target)


def _start(run: ChangeRun, stage: Stage) -> StageRun:
    """Return the active stage run for ``stage`` or create a fresh pending one."""
    active = [s for s in run.stages if s.stage == stage and s.status not in STAGE_TERMINAL_STATUSES]
    if active:
        return active[-1]
    stage_run = StageRun(id=_stage_run_id(run, stage), stage=stage)
    run.stages.append(stage_run)
    return stage_run


def _stage_run_id(run: ChangeRun, stage: Stage) -> str:
    """Deterministic id: stable across replays, unique per stage occurrence."""
    occurrence = sum(1 for s in run.stages if s.stage == stage) + 1
    return f"{run.id}:{stage.value}:{occurrence}"
