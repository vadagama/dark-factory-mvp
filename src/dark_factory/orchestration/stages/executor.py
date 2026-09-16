"""Aggregation and the stage decision of the deterministic path (T010).

The executor folds the machine checks of one stage attempt into the
stage-level decision (ADR-005 p.2): exhausted limits stop the run with
``Blocked`` and the violation reasons (FR-008, SC-006, ADR-018 p.5);
otherwise the stage cannot complete deterministically — the required gates
are evaluated on the final SHA in CI (FR-009), outside this path — and the
attempt ends ``waiting`` with an honest ``wait_for_input`` reason naming
what is missing. No gate result produced here satisfies a gate, so no
``succeeded`` decision is reachable (FR-009, SC-004); the path never invokes
the harness (ADR-003). The attempt number of the result is the one the context
carries (ADR-006 p.7): the durable driver supplies the attempt of the physical
execution, so a retry of the same operation is scored against the attempt it
really is, and a caller that does not track attempts keeps the default first
attempt. The budget is whatever the caller fixed in the context — the CLI passes
the default snapshot until the state store lands, so exhaustion outcomes are
produced only for callers that carry a persisted budget.

The shared attempt budget (T-062) enters through the optional ``budget_check``
produced by ``orchestration.budget.BudgetCoordinator``: an ``awaiting_decision``
check blocks the attempt with its diagnostics and one open blocker finding per
triggered limit, so a human sees why (ADR-018 p.5) and
``api.aggregates.open_blocker_count`` counts it. Without the argument — under
``DEFAULT_BUDGET_POLICY``, which sets no limit at all — the produced result is
exactly the one this path produced before T-062: opting into a configured
policy is explicit and never a silent behaviour change.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Final

from dark_factory.changes.enums import StageStatus, StopOutcome
from dark_factory.changes.findings import Finding, GateResult
from dark_factory.changes.next_action import NextAction, StopAction, WaitForInputAction
from dark_factory.changes.run import StageResult
from dark_factory.orchestration.budget import BudgetCheck
from dark_factory.orchestration.stages.checks import (
    budget_exhaustions,
    budget_findings,
    budget_stop_reason,
    change_request_missing,
    pending_gate_results,
)
from dark_factory.orchestration.stages.context import StageContext

_GATES_NOTE: Final[str] = "machine checks run on the final SHA in CI (FR-009)"
_CHANGE_REQUEST_NOTE: Final[str] = "change request is not attached to the change (merge, ADR-011)"


def waiting_reason(context: StageContext) -> str:
    """Why the stage cannot complete deterministically; names every missing piece."""
    gates = ", ".join(gate.value for gate in sorted(context.required_gates, key=lambda g: g.value))
    parts = [f"required gates not evaluated: {gates} ({_GATES_NOTE})"]
    if change_request_missing(context.stage, context.change):
        parts.append(_CHANGE_REQUEST_NOTE)
    return "; ".join(parts)


def run_deterministic_stage(
    context: StageContext,
    *,
    now: datetime | None = None,
    budget_check: BudgetCheck | None = None,
) -> StageResult:
    """Aggregate the machine checks into the StageResult of one attempt.

    Decision order: limit exhaustion wins and ends the attempt ``blocked``
    with the violation reasons (SC-006); an ``awaiting_decision`` budget check
    (T-062) blocks the same way with its diagnostics and a blocker finding per
    triggered limit; otherwise the attempt ends ``waiting`` (FR-009: gate
    execution is out of scope here) with the required-but-unevaluated machine
    gates recorded as ``pending`` gate results and an actionable reason. A risk
    class adds no gate here (T-080, ADR-023 p.3): its human side is policy, not
    a machine check of this path. ``now``
    overrides the wall clock for the deadline check and ``produced_at``
    (determinism in tests, as in ``flow.apply_result``); ``budget_check`` is the
    optional coordinator output — the explicit seam for a configured budget
    policy.
    """
    reference_now = now if now is not None else datetime.now(UTC)
    gate_results = pending_gate_results(context.required_gates)
    violations = budget_exhaustions(context.budget, now=reference_now)
    if violations:
        return _result(
            context,
            status=StageStatus.BLOCKED,
            next_action=StopAction(
                outcome=StopOutcome.BLOCKED,
                reason="; ".join(violation.reason for violation in violations),
            ),
            gate_results=gate_results,
            produced_at=reference_now,
        )
    if budget_check is not None:
        stop_reason = budget_stop_reason(budget_check)
        if stop_reason is not None:
            return _result(
                context,
                status=StageStatus.BLOCKED,
                next_action=StopAction(outcome=StopOutcome.BLOCKED, reason=stop_reason),
                gate_results=gate_results,
                findings=budget_findings(budget_check),
                produced_at=reference_now,
            )
    return _result(
        context,
        status=StageStatus.WAITING,
        next_action=WaitForInputAction(reason=waiting_reason(context)),
        gate_results=gate_results,
        produced_at=reference_now,
    )


def _result(
    context: StageContext,
    *,
    status: StageStatus,
    next_action: NextAction,
    gate_results: list[GateResult],
    produced_at: datetime,
    findings: Sequence[Finding] = (),
) -> StageResult:
    """StageResult skeleton shared by both outcomes: identity from the context.

    ``attempt_number`` comes from the context, so a retry of the same logical
    operation produces a result the flow can match to the active stage run
    (ADR-006 p.7); a caller that does not track attempts keeps the default 1.
    """
    return StageResult(
        stage=context.stage,
        run_id=context.run_id,
        change_id=context.change.id,
        attempt_number=context.attempt_number,
        input_revision=context.input_revision,
        status=status,
        next_action=next_action,
        gate_results=gate_results,
        findings=list(findings),
        produced_at=produced_at,
    )
