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

Rework and budget limits live in ``dark_factory.orchestration.rules.limits``, gate policy in
``dark_factory.orchestration.rules.gates``, route topology and risk bands in
``dark_factory.orchestration.routes``, escalation policy (T-016, ADR-018 p.5) in
``dark_factory.orchestration.policy``, merge policy (T-026, ADR-011 p.2) in
``dark_factory.orchestration.policy.merge`` and the risk-class obligations
(T-080, ADR-023 p.3/p.5) in ``dark_factory.orchestration.policy.risk``: the
engine re-derives the effective class from the approved contract and checks it
against the route band and the human control points on every advance, merge and
release. Stop conditions deterministically end in ``Blocked`` (FR-008, ADR-018
p.5).
Stage-internal execution (TaskGraph, pydantic-graph) is T-015 and deliberately
out of scope here.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Final, Literal, assert_never

from dark_factory.changes.enums import (
    GateStatus,
    RunStatus,
    Stage,
    StageStatus,
    StopOutcome,
)
from dark_factory.changes.findings import Decision, GateResult
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
    InvalidStatusTransition,
    StageResult,
    StageRun,
    completion_violations,
)
from dark_factory.changes.usage import BudgetSnapshot, Usage
from dark_factory.orchestration.policy.escalation import (
    autonomy_budget_violation,
    escalation_stop_reason,
    risk_escalation_violation,
)
from dark_factory.orchestration.policy.merge import (
    DEFAULT_MERGE_POLICY,
    MergeDecision,
    MergePolicy,
    MergeRequestContext,
    evaluate_merge,
)
from dark_factory.orchestration.policy.risk import effective_change_risk_class
from dark_factory.orchestration.routes import route_allows_risk, route_profile
from dark_factory.orchestration.rules.gates import required_human_gates, unsatisfied_gates
from dark_factory.orchestration.rules.limits import continuation_violations, rework_violation

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
        {"execute_stage", "wait_for_input", "wait_for_ci", "rework", "request_approval", "stop"}
    ),
    # Construction and planning publish their work as one commit + change
    # request (T-092 S2): their machine gates run on the final SHA in CI
    # (FR-009), so both stages park on ``wait_for_ci`` after a produced
    # attempt. Specification has no machine gate on the base set — it parks
    # for the human decision instead.
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
    human_decisions: Sequence[Decision] = (),
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

    ``merge_context`` carries the merge facts (executor, SHAs, human approvals)
    the ``MergeAction`` handler needs to consult the merge policy (T-026). The
    flow anchors ``route``, ``stage``, the effective ``risk_class`` and
    ``gate_results`` of the context to the run and the result — the caller
    contributes only executor, SHAs and approvals. Without a context the merge
    parks the run in Waiting: absent merge facts mean manual mode, the machine
    never merges on missing data (FR-010).

    ``human_decisions`` are the human approvals the caller observed (the state
    store, a reconciler pass). They close the control points of a R2+ risk
    class (T-080, ADR-023 p.5) on the stage boundary: absent decisions the
    obligations are unmet by definition and the advance stops — the check is
    fail-closed, never fail-open.

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
    return _handle_action(
        run,
        stage_run,
        result,
        history,
        reference_now,
        merge_context,
        human_decisions,
    )


def _handle_action(
    run: ChangeRun,
    stage_run: StageRun,
    result: StageResult,
    history: Sequence[StageResult],
    now: datetime,
    merge_context: MergeRequestContext | None,
    human_decisions: Sequence[Decision] = (),
) -> FlowDecision:
    """Apply the effects of one honored-or-blocked action; exhaustively checked."""
    _accumulate_usage(run.budget, result.usage)
    match result.next_action:
        case ExecuteStageAction(next_stage=next_stage):
            return _handle_execute(run, stage_run, result, next_stage, now, human_decisions)
        case WaitForInputAction() | WaitForCIAction() | RequestApprovalAction():
            # External wait (input, CI or the human gate of ADR-018): the
            # StageResult must be persisted before the job ends (ADR-006 p.8).
            return _park(stage_run, run, result.next_action)
        case ReworkAction(round=requested_round):
            return _handle_rework(run, stage_run, result, requested_round)
        case MergeAction():
            return _handle_merge(run, stage_run, result, now, merge_context)
        case ReleaseAction():
            return _handle_release(run, stage_run, result, history, now)
        case StopAction(outcome=outcome):
            stage_status, run_status = _STOP_STATUS[outcome]
            stage_run.apply_status(stage_status)
            run.apply_status(run_status)
            return _decision(stage_run, run, result.next_action, None)
        case _:
            assert_never(result.next_action)


def _handle_execute(
    run: ChangeRun,
    stage_run: StageRun,
    result: StageResult,
    next_stage: Stage,
    now: datetime,
    human_decisions: Sequence[Decision],
) -> FlowDecision:
    """Advance to the next stage of the route, or stop on a gate/escalation reason."""
    expected = route_profile(run.route).next_stage(result.stage)
    if expected is None or next_stage != expected:
        shown = expected.value if expected is not None else "<none>"
        raise InvalidFlowTransition(
            f"stage {result.stage.value} on route {run.route.value} must advance to "
            f"{shown}, got {next_stage.value}"
        )
    reason = _first_stop_reason(
        lambda: _block_reason(run, result.stage, result.gate_results, now),
        lambda: _escalation_reason(run, result, human_decisions=human_decisions),
    )
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


def _handle_rework(
    run: ChangeRun,
    stage_run: StageRun,
    result: StageResult,
    requested_round: int,
) -> FlowDecision:
    """Spend one rework round and re-enter the rework target stage (T-014)."""
    # Declared escalations and the autonomy budget veto a rework round
    # without burning one: an escalation is not a rework iteration.
    reason = _first_stop_reason(
        lambda: escalation_stop_reason(result.escalations),
        lambda: _autonomy_budget_reason(run),
    )
    if reason is not None:
        return _stop(stage_run, run, reason)
    violation = rework_violation(run.budget, requested_round=requested_round)
    if violation is not None:
        return _stop(stage_run, run, violation.reason)
    run.budget.used_rework_rounds += 1
    stage_run.apply_status(StageStatus.FAILED)
    rework_target = REWORK_TARGET[result.stage]
    _start(run, rework_target)
    return _decision(stage_run, run, result.next_action, rework_target)


def _handle_merge(
    run: ChangeRun,
    stage_run: StageRun,
    result: StageResult,
    now: datetime,
    merge_context: MergeRequestContext | None,
) -> FlowDecision:
    """Merge per policy (T-026): stop, park for the human merge, or advance."""
    target = route_profile(run.route).next_stage(result.stage)
    if target is None:
        raise FlowStateError(f"no stage follows {result.stage.value} on route {run.route.value}")
    # Stage gates (SHA-blind), escalations and the route risk band block
    # first: their stop reasons are preserved for existing callers. The
    # merge policy then adds the merge-specific preconditions on the
    # final SHA (T-026) and the control points of a R2+ class (T-080).
    reason = _first_stop_reason(
        lambda: _block_reason(run, result.stage, result.gate_results, now),
        lambda: escalation_stop_reason(result.escalations),
        lambda: _risk_band_reason(run),
    )
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


def _handle_release(
    run: ChangeRun,
    stage_run: StageRun,
    result: StageResult,
    history: Sequence[StageResult],
    now: datetime,
) -> FlowDecision:
    """Complete the run, or stop on a gate, escalation, band or completion invariant."""
    reason = _first_stop_reason(
        lambda: _block_reason(run, result.stage, result.gate_results, now),
        lambda: escalation_stop_reason(result.escalations),
        lambda: _risk_band_reason(run),
    )
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


def _park(stage_run: StageRun, run: ChangeRun, action: NextAction) -> FlowDecision:
    """Park the stage and the run in Waiting for an external event."""
    stage_run.apply_status(StageStatus.WAITING)
    run.apply_status(RunStatus.WAITING)
    return _decision(stage_run, run, action, None)


def _first_stop_reason(*reasons: Callable[[], str | None]) -> str | None:
    """First stop reason produced by ``reasons``, evaluated lazily in order.

    The order encodes precedence: an earlier check that fires hides the later
    ones, so callers list the reasons from the most to the least specific.
    """
    for reason in reasons:
        found = reason()
        if found is not None:
            return found
    return None


def _merge_policy_decision(
    run: ChangeRun,
    result: StageResult,
    merge_context: MergeRequestContext | None,
) -> MergeDecision:
    """Consult the merge policy for one ``MergeAction`` result (T-026, T-080).

    The context is anchored to the run: route, stage, the effective risk class
    and the attempt's gate results are authoritative — the caller cannot widen
    or shrink the gate set, nor lower the class it travels with. Without a
    context the policy is not consulted at all: absent merge facts mean manual
    mode (FR-010) — the run waits for the human merge instead of advancing on
    missing data.
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
        risk_class=effective_change_risk_class(run.implementation_contract, run.route),
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


def _escalation_reason(
    run: ChangeRun,
    result: StageResult,
    *,
    human_decisions: Sequence[Decision] = (),
) -> str | None:
    """Escalation-policy reason blocking an autonomous stage advance, if any.

    Declared escalations always stop the advance. The obligations of a R2+
    effective risk class — the route band and the human control points of the
    stage being left — are checked next (T-080, ADR-023 p.3/p.5); the check is
    produced here, by the engine, so a R2+ change never advances on a missing,
    unbound or stale approval. A human gate that passed on a resolved wait is
    bound to the revision its approval authorizes — the observed head of the
    stage's change request (T-043 increment 1) — so the control points close on
    the approval bound to that same revision; a gate without a passing SHA falls
    back to the stage's input revision. The contract's autonomy budget bounds
    the stage attempts last (an iteration is one ``StageRun`` occurrence).

    The Construction entry gate of ADR-018 p.3 is deliberately **not** here
    (T-063): it is a precondition of entering Construction, so it is checked by
    the stage executors' pre-flight where Construction is entered, before any
    external effect. Blocking the outgoing stage here attributed the stop to the
    wrong stage and made a retry re-run completed work (the B2 defect of the
    T043 pilot).
    """
    declared = escalation_stop_reason(result.escalations)
    if declared is not None:
        return declared
    obligations = risk_escalation_violation(
        risk_class=effective_change_risk_class(run.implementation_contract, run.route),
        route=run.route,
        stage=result.stage,
        decisions=human_decisions,
        sha=_control_point_sha(run, result),
    )
    if obligations is not None:
        # Rendered with its rule, as declared escalations are: the stop reason
        # names the gate that fired, not just the missing obligation.
        return f"{obligations.rule.value}: {obligations.reason}"
    return _autonomy_budget_reason(run)


def _control_point_sha(run: ChangeRun, result: StageResult) -> str | None:
    """The revision the stage's human control points are checked against.

    A human gate that passed on a resolved wait carries the SHA its approval
    was observed at — the head of the stage's change request (T-043 increment
    1, ADR-009 p.7): the approval authorizes that revision, so the control
    points of the stage being left bind to it. Gates that passed without a
    SHA (or that are still pending on the deterministic path) fall back to the
    stage's input revision — the historical binding.
    """
    risk_class = effective_change_risk_class(run.implementation_contract, run.route)
    human_gates = required_human_gates(run.route, result.stage, risk_class)
    for gate_result in result.gate_results:
        if gate_result.gate in human_gates and gate_result.status is GateStatus.PASSED:
            return gate_result.sha or result.input_revision
    return result.input_revision


def _risk_band_reason(run: ChangeRun) -> str | None:
    """Reason for an effective risk class the route of the run does not allow, if any.

    The band is checked against the *effective* class, so a low declared class
    on a route with a higher floor (``architecture``, ``foundation``) is raised
    by the floor instead of being rejected, while a class the band refuses (R2+
    on ``quick``) stops the transition. Called on every action that advances the
    run — stage advance, merge and release — so no onward path carries a risky
    change along the short route (ADR-023 p.3).
    """
    risk_class = effective_change_risk_class(run.implementation_contract, run.route)
    if route_allows_risk(run.route, risk_class):
        return None
    profile = route_profile(run.route)
    return (
        f"risk class {risk_class.value} is not allowed on route {run.route.value} "
        f"(band {profile.min_risk_class.value}-{profile.max_risk_class.value}): "
        "a risky change does not take the short route (ADR-023 p.3, ADR-018 p.5)"
    )


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
    A result of the *next* attempt of an active FAILED/BLOCKED stage run is the
    retry of the same logical operation (ADR-006 p.7): the attempt number
    advances on the existing stage run and the operation key is unchanged. A
    result whose attempt matches neither is stale and rejected (ADR-006 p.4:
    protection against outdated results).

    The active run is (re)entered as in progress before the result is applied:
    ``pending -> in_progress`` on first entry, ``waiting -> in_progress`` on
    resume, ``failed``/``blocked -> in_progress`` on a retry — the T-003 status
    table has no direct ``pending/waiting -> succeeded`` edge, so handlers can
    complete the stage in one step.
    """
    active = [
        s for s in run.stages if s.stage == result.stage and s.status not in STAGE_TERMINAL_STATUSES
    ]
    if active:
        stage_run = active[-1]
        if stage_run.attempt_number != result.attempt_number:
            _begin_retry(stage_run, result)
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


def _begin_retry(stage_run: StageRun, result: StageResult) -> None:
    """Advance an active stage run to a retry attempt, or reject a stale result.

    Only the next attempt of a FAILED/BLOCKED stage run is a retry (ADR-006
    p.7); every other mismatch — an older attempt, a jump forward, a result for
    a stage that is not retryable — is a stale or foreign result and is refused
    as before (ADR-006 p.4). The domain owns the rule (``StageRun.begin_retry``);
    the flow translates its refusal into a flow error so the surface stays the
    same for callers.
    """
    try:
        stage_run.begin_retry(result.attempt_number)
    except InvalidStatusTransition as exc:
        raise FlowStateError(
            f"stage {result.stage.value} result from attempt {result.attempt_number} "
            f"does not match active attempt {stage_run.attempt_number}"
        ) from exc


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
