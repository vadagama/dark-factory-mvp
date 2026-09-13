"""Deterministic machine checks of the stage path (T010, FR-009).

Only checks that genuinely run on the fixed context live here, and every
outcome is honest: limit exhaustion is evaluated against the carried budget
snapshot (FR-008, FR-016, ADR-018 p.5), gate applicability against the route
policy (ADR-005). A gate the deterministic path cannot execute — machine
checks run on the final SHA in CI (FR-009, SC-004) — is reported as
``pending``, never as ``passed``. There are no LLM/harness calls (ADR-003).
"""

from datetime import datetime
from typing import Final

from dark_factory.changes.enums import Gate, GateStatus, Stage
from dark_factory.changes.findings import GateResult
from dark_factory.changes.run import Change
from dark_factory.changes.usage import BudgetSnapshot
from dark_factory.rules.limits import LimitViolation, continuation_violations, rework_violation

# Stages whose deterministic success path needs a change request: review and
# verification complete through ``merge`` (ADR-005 p.2), and ``MergeAction``
# carries a mandatory ``ChangeRequestRef`` (ADR-011).
_STAGES_REQUIRING_CHANGE_REQUEST: Final[frozenset[Stage]] = frozenset({Stage.REVIEW_VERIFICATION})


def budget_exhaustions(budget: BudgetSnapshot, *, now: datetime) -> list[LimitViolation]:
    """Limit rules that stop autonomous execution, in stable order (FR-008, ADR-018 p.5).

    The rework check asks whether one more round would still be within the
    limit (FR-008: at most 3 rounds by default); the remaining rules are the
    existing continuation checks (token/cost budget, deadline). Every
    triggered rule means the attempt ends ``Blocked`` (FR-008, SC-006).
    """
    violations: list[LimitViolation] = []
    rework = rework_violation(budget, requested_round=budget.used_rework_rounds + 1)
    if rework is not None:
        violations.append(rework)
    violations.extend(continuation_violations(budget, now=now))
    return violations


def pending_gate_results(required_gates: frozenset[Gate]) -> list[GateResult]:
    """One ``pending`` record per required gate, in stable gate order.

    The deterministic stage path executes no machine checks on a product SHA
    (FR-009, SC-004), so no gate can honestly be ``passed`` or ``failed``
    here: the records document which gates this stage/route requires and that
    they are not evaluated yet. ``pending`` does not satisfy a gate in the
    flow policy, which matches the stage's own decision to wait.
    """
    return [
        GateResult(gate=gate, status=GateStatus.PENDING)
        for gate in sorted(required_gates, key=lambda gate: gate.value)
    ]


def change_request_missing(stage: Stage, change: Change) -> bool:
    """True when the stage's success path needs a change request and none is attached.

    Only the review/verification stage completes through ``merge`` (ADR-005),
    and merge policy works on a change request (ADR-011). On other stages the
    check is not applicable — ``False`` says nothing about their inputs.
    """
    if stage not in _STAGES_REQUIRING_CHANGE_REQUEST:
        return False
    return change.change_request is None
