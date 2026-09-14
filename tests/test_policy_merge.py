"""Merge policy decision branches (T-026, docs T-032, ADR-011 p.2).

Every outcome kind and every precondition is covered:

- blocked: agent executor (FR-004, FR-023), SHA drift or unknown SHA (FR-011),
  gates stale or failed at the final SHA (T-032, ADR-009 p.7);
- manual_merge_required: no version-bound human approval, a non-approval as
  the latest decision, a finalizer without an explicit risk-class allowance
  (FR-010: manual mode by default);
- human_merge_authorized / finalizer_merge_allowed: the authorized paths.
"""

from collections.abc import Sequence
from datetime import UTC, datetime

from dark_factory.changes.enums import (
    DecisionOutcome,
    DecisionSource,
    Gate,
    GateStatus,
    RiskClass,
    Role,
    Route,
    Stage,
)
from dark_factory.changes.findings import Decision, GateResult
from dark_factory.orchestration.policy.merge import (
    DEFAULT_MERGE_POLICY,
    MergeDecision,
    MergeExecutor,
    MergePolicy,
    MergeRequestContext,
    evaluate_merge,
)
from dark_factory.rules.gates import required_gates
from tests.changes_factories import make_merge_approval

SHA = "731ac91"
OLD_SHA = "aaa111"
NOW = datetime(2026, 9, 13, 12, 0, 0, tzinfo=UTC)


def _decision(
    *,
    outcome: DecisionOutcome = DecisionOutcome.APPROVED,
    decided_by: DecisionSource = DecisionSource.HUMAN,
    gate: Gate = Gate.REVIEW,
    commit_sha: str | None = SHA,
) -> Decision:
    return Decision(
        id=f"dec-{decided_by.value}-{gate.value}-{outcome.value}",
        gate=gate,
        outcome=outcome,
        decided_by=decided_by,
        role=Role.QUALITY,
        decided_at=NOW,
        commit_sha=commit_sha,
    )


def _approved(sha: str = SHA) -> tuple[Decision, ...]:
    return (make_merge_approval(sha),)


def _passing_gates(
    route: Route = Route.STANDARD,
    stage: Stage = Stage.REVIEW_VERIFICATION,
    *,
    sha: str | None = SHA,
    statuses: dict[Gate, GateStatus] | None = None,
) -> list[GateResult]:
    overrides = statuses or {}
    return [
        GateResult(gate=gate, status=overrides.get(gate, GateStatus.PASSED), sha=sha)
        for gate in sorted(required_gates(route, stage), key=lambda g: g.value)
    ]


def _context(
    *,
    executor: MergeExecutor = "human",
    risk_class: RiskClass = RiskClass.R1,
    route: Route = Route.STANDARD,
    stage: Stage = Stage.REVIEW_VERIFICATION,
    expected_sha: str | None = SHA,
    head_sha: str | None = SHA,
    human_approvals: Sequence[Decision] = (),
    gate_results: Sequence[GateResult] | None = None,
) -> MergeRequestContext:
    """Merge facts; gates default to green results evaluated at the final SHA."""
    return MergeRequestContext(
        executor=executor,
        risk_class=risk_class,
        route=route,
        stage=stage,
        expected_sha=expected_sha,
        head_sha=head_sha,
        human_approvals=human_approvals,
        gate_results=_passing_gates(route, stage) if gate_results is None else gate_results,
    )


# --- blocked: executor ---


def test_agent_executor_is_blocked() -> None:
    decision = evaluate_merge(_context(executor="agent", human_approvals=_approved()))
    assert decision.kind == "blocked"
    assert decision.reason is not None
    assert "agent" in decision.reason
    assert "FR-004" in decision.reason and "FR-023" in decision.reason


def test_agent_executor_is_checked_before_everything_else() -> None:
    decision = evaluate_merge(
        _context(executor="agent", expected_sha=SHA, head_sha=OLD_SHA, human_approvals=())
    )
    assert decision.kind == "blocked"
    assert decision.reason is not None and "agent" in decision.reason


# --- blocked: SHA immutability (FR-011) ---


def test_sha_mismatch_blocks_the_merge() -> None:
    decision = evaluate_merge(_context(head_sha=OLD_SHA, human_approvals=_approved()))
    assert decision.kind == "blocked"
    assert decision.reason is not None
    assert SHA in decision.reason and OLD_SHA in decision.reason
    assert "FR-011" in decision.reason


def test_unknown_head_sha_blocks_the_merge() -> None:
    decision = evaluate_merge(_context(head_sha=None, human_approvals=_approved()))
    assert decision.kind == "blocked"
    assert decision.reason is not None and "FR-011" in decision.reason


def test_unknown_expected_sha_blocks_the_merge() -> None:
    decision = evaluate_merge(_context(expected_sha=None, human_approvals=_approved()))
    assert decision.kind == "blocked"
    assert decision.reason is not None and "FR-011" in decision.reason


# --- blocked: gates at the final SHA (T-032) ---


def test_stale_gate_results_do_not_satisfy_the_final_sha() -> None:
    stale = _passing_gates(sha=OLD_SHA)
    decision = evaluate_merge(_context(gate_results=stale, human_approvals=_approved()))
    assert decision.kind == "blocked"
    assert decision.reason is not None
    assert "final SHA" in decision.reason
    assert "review" in decision.reason and "verification" in decision.reason


def test_gate_result_without_sha_does_not_satisfy_the_final_sha() -> None:
    unbound = _passing_gates(sha=None)
    decision = evaluate_merge(_context(gate_results=unbound, human_approvals=_approved()))
    assert decision.kind == "blocked"


def test_failed_gate_blocks_the_merge() -> None:
    gates = _passing_gates(statuses={Gate.REVIEW: GateStatus.FAILED})
    decision = evaluate_merge(_context(gate_results=gates, human_approvals=_approved()))
    assert decision.kind == "blocked"
    assert decision.reason is not None and "review" in decision.reason


def test_missing_gate_result_blocks_the_merge() -> None:
    gates = [GateResult(gate=Gate.REVIEW, status=GateStatus.PASSED, sha=SHA)]
    decision = evaluate_merge(_context(gate_results=gates, human_approvals=_approved()))
    assert decision.kind == "blocked"
    assert decision.reason is not None and "verification" in decision.reason


def test_skipped_gate_satisfies_the_merge_gates() -> None:
    gates = _passing_gates(statuses={Gate.VERIFICATION: GateStatus.SKIPPED})
    decision = evaluate_merge(_context(gate_results=gates, human_approvals=_approved()))
    assert decision.kind == "human_merge_authorized"


# --- manual_merge_required: version-bound human approval (ADR-009 p.7) ---


def test_merge_without_human_approval_requires_manual_merge() -> None:
    decision = evaluate_merge(_context())
    assert decision.kind == "manual_merge_required"
    assert decision.reason is not None
    assert "no human approval" in decision.reason
    assert Gate.REVIEW.value in decision.reason


def test_approval_bound_to_an_old_sha_does_not_authorize() -> None:
    decision = evaluate_merge(_context(human_approvals=_approved(OLD_SHA)))
    assert decision.kind == "manual_merge_required"
    assert decision.reason is not None and "no human approval" in decision.reason


def test_unbound_approval_does_not_authorize() -> None:
    decision = evaluate_merge(_context(human_approvals=(_decision(commit_sha=None),)))
    assert decision.kind == "manual_merge_required"


def test_non_human_decision_does_not_authorize() -> None:
    for source in (DecisionSource.POLICY, DecisionSource.AGENT):
        decision = evaluate_merge(_context(human_approvals=(_decision(decided_by=source),)))
        assert decision.kind == "manual_merge_required"


def test_approval_on_a_different_gate_does_not_authorize() -> None:
    decision = evaluate_merge(_context(human_approvals=(_decision(gate=Gate.PLANNING),)))
    assert decision.kind == "manual_merge_required"


def test_rejected_decision_vetoes_the_merge() -> None:
    decision = evaluate_merge(
        _context(human_approvals=(_decision(outcome=DecisionOutcome.REJECTED),))
    )
    assert decision.kind == "manual_merge_required"
    assert decision.reason is not None and "rejected" in decision.reason


def test_waived_decision_is_not_an_approval() -> None:
    decision = evaluate_merge(
        _context(human_approvals=(_decision(outcome=DecisionOutcome.WAIVED),))
    )
    assert decision.kind == "manual_merge_required"
    assert decision.reason is not None and "waived" in decision.reason


def test_latest_decision_wins() -> None:
    rejected = _decision(outcome=DecisionOutcome.REJECTED)
    approved = make_merge_approval(SHA).model_copy(update={"id": "dec-merge-2"})
    later_approval = evaluate_merge(_context(human_approvals=(rejected, approved)))
    assert later_approval.kind == "human_merge_authorized"

    later_rejection = evaluate_merge(_context(human_approvals=(_approved()[0], rejected)))
    assert later_rejection.kind == "manual_merge_required"


# --- authorized paths ---


def test_human_executor_with_bound_approval_is_authorized() -> None:
    decision = evaluate_merge(_context(human_approvals=_approved()))
    assert decision.kind == "human_merge_authorized"
    assert decision.reason is None
    assert decision.merge_method is None


def test_finalizer_with_default_policy_falls_back_to_manual() -> None:
    decision = evaluate_merge(_context(executor="trusted_finalizer", human_approvals=_approved()))
    assert decision.kind == "manual_merge_required"
    assert decision.reason is not None and "manual mode" in decision.reason


def test_finalizer_merges_only_explicitly_allowed_classes() -> None:
    policy = MergePolicy(auto_merge_risk_classes=frozenset({RiskClass.R0}))
    allowed = evaluate_merge(
        _context(
            executor="trusted_finalizer",
            risk_class=RiskClass.R0,
            human_approvals=_approved(),
        ),
        policy=policy,
    )
    assert allowed.kind == "finalizer_merge_allowed"
    assert allowed.merge_method == "squash"
    assert allowed.reason is None

    other = evaluate_merge(
        _context(
            executor="trusted_finalizer",
            risk_class=RiskClass.R1,
            human_approvals=_approved(),
        ),
        policy=policy,
    )
    assert other.kind == "manual_merge_required"


def test_finalizer_without_approval_still_waits() -> None:
    """The human release gate holds even on the auto-merge path (ADR-011 p.2)."""
    policy = MergePolicy(auto_merge_risk_classes=frozenset({RiskClass.R0}))
    decision = evaluate_merge(
        _context(executor="trusted_finalizer", risk_class=RiskClass.R0),
        policy=policy,
    )
    assert decision.kind == "manual_merge_required"


def test_declared_merge_method_is_surfaced_only_when_single() -> None:
    policy = MergePolicy(
        auto_merge_risk_classes=frozenset({RiskClass.R0}),
        merge_methods=frozenset({"squash", "merge"}),
    )
    decision = evaluate_merge(
        _context(
            executor="trusted_finalizer",
            risk_class=RiskClass.R0,
            human_approvals=_approved(),
        ),
        policy=policy,
    )
    assert decision.kind == "finalizer_merge_allowed"
    assert decision.merge_method is None


# --- configuration ---


def test_default_policy_is_manual_and_squash_only() -> None:
    assert DEFAULT_MERGE_POLICY.auto_merge_risk_classes == frozenset()
    assert DEFAULT_MERGE_POLICY.merge_methods == frozenset({"squash"})
    assert DEFAULT_MERGE_POLICY.merge_authorization_gate is Gate.REVIEW


def test_evaluation_is_deterministic() -> None:
    context = _context(human_approvals=_approved())
    first = evaluate_merge(context)
    second = evaluate_merge(context)
    assert first == second
    assert isinstance(first, MergeDecision)
