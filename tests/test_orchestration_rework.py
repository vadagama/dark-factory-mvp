"""Bounded rework loop policy: pass history, limits, repeats, escalations (T-014)."""

import pytest
from pydantic import ValidationError

from dark_factory.changes.enums import FindingOrigin, FindingSeverity, FindingStatus
from dark_factory.changes.escalations import EscalationViolation
from dark_factory.changes.usage import BudgetSnapshot
from dark_factory.orchestration.policy.escalation import (
    escalation_stop_reason,
    requirements_violation,
)
from dark_factory.orchestration.rework import (
    FindingSignature,
    ReviewPass,
    ReworkDecision,
    finding_signature,
    plan_rework,
)
from dark_factory.orchestration.rules.limits import rework_violation
from tests.changes_factories import make_finding

SHA_A = "731ac91"
SHA_B = "9f31ab2"
SHA_C = "ca31c10"
SHA_D = "4f86c2a"

S1 = FindingSignature(origin=FindingOrigin.AGENT, category="tests", file="src/app.py", line=10)
S2 = FindingSignature(origin=FindingOrigin.AGENT, category="lint", file="src/lint.py", line=2)
S3 = FindingSignature(origin=FindingOrigin.CI, category="lint", file="src/lint.py", line=7)


def _pass(
    round_number: int,
    sha: str = SHA_A,
    *,
    blocking: tuple[FindingSignature, ...] = (S1,),
    escalations: list[EscalationViolation] | None = None,
) -> ReviewPass:
    return ReviewPass(
        round=round_number,
        sha=sha,
        blocking=blocking,
        escalations=escalations if escalations is not None else [],
    )


def _requirements_escalation() -> EscalationViolation:
    violation = requirements_violation(["AC-1 and AC-2 contradict"])
    assert violation is not None
    return violation


# --- rework is planned for a failed review ---


def test_first_failed_review_plans_rework() -> None:
    budget = BudgetSnapshot(max_rework_rounds=3, used_rework_rounds=0)
    decision = plan_rework(budget=budget, passes=[_pass(1, SHA_A)])
    assert decision.outcome == "rework"
    assert decision.round == 1
    assert decision.max_rounds == 3
    assert decision.signatures == (S1,)
    assert "review pass 1" in decision.reason
    assert "agent/tests src/app.py:10" in decision.reason


def test_rework_round_follows_the_budget_counter() -> None:
    budget = BudgetSnapshot(max_rework_rounds=3, used_rework_rounds=2)
    passes = [_pass(1, SHA_A, blocking=(S1,)), _pass(2, SHA_B, blocking=(S2,))]
    decision = plan_rework(budget=budget, passes=passes)
    assert decision.outcome == "rework"
    assert decision.round == 3  # the last allowed round: 2 of 3 already spent


def test_partial_fix_allows_next_round() -> None:
    budget = BudgetSnapshot(max_rework_rounds=3, used_rework_rounds=0)
    first = plan_rework(budget=budget, passes=[_pass(1, SHA_A, blocking=(S1, S2))])
    assert first.outcome == "rework"
    assert first.round == 1
    spent = budget.model_copy(update={"used_rework_rounds": 1})
    second = plan_rework(
        budget=spent,
        passes=[
            _pass(1, SHA_A, blocking=(S1, S2)),
            _pass(2, SHA_B, blocking=(S1,)),
        ],
    )
    assert second.outcome == "rework"
    assert second.round == 2


def test_progress_plans_rework_until_the_limit_is_exhausted() -> None:
    budget = BudgetSnapshot(max_rework_rounds=3, used_rework_rounds=0)
    history: list[ReviewPass] = []
    decisions: list[ReworkDecision] = []
    scenarios = [
        (SHA_A, (S1,)),
        (SHA_B, (S1, S2)),
        (SHA_C, (S2,)),
        (SHA_D, (S3,)),
    ]
    for index, (sha, blocking) in enumerate(scenarios, start=1):
        history.append(_pass(index, sha, blocking=blocking))
        decision = plan_rework(budget=budget, passes=history)
        decisions.append(decision)
        if decision.round is not None:
            budget = budget.model_copy(update={"used_rework_rounds": decision.round})
    assert [decision.outcome for decision in decisions] == ["rework", "rework", "rework", "blocked"]
    assert [decision.round for decision in decisions] == [1, 2, 3, None]
    assert "rework limit exhausted: 3/3" in decisions[-1].reason


# --- stop condition: repeated error ---


def test_repeated_error_blocks() -> None:
    budget = BudgetSnapshot(max_rework_rounds=3, used_rework_rounds=1)
    passes = [_pass(1, SHA_A, blocking=(S1,)), _pass(2, SHA_B, blocking=(S1,))]
    decision = plan_rework(budget=budget, passes=passes)
    assert decision.outcome == "blocked"
    assert decision.round is None
    assert decision.signatures == (S1,)
    assert "rework round 1 did not change the set of blocking findings" in decision.reason
    assert "the same 1 finding(s)" in decision.reason


# --- stop condition: the rework limit ---


def test_exhausted_limit_blocks_with_the_limits_reason() -> None:
    budget = BudgetSnapshot(max_rework_rounds=3, used_rework_rounds=3)
    decision = plan_rework(budget=budget, passes=[_pass(4, SHA_D, blocking=(S3,))])
    assert decision.outcome == "blocked"
    assert decision.round is None
    violation = rework_violation(budget, requested_round=4)
    assert violation is not None
    assert decision.reason == violation.reason
    assert "rework limit exhausted: 3/3" in decision.reason


def test_requested_round_beyond_the_limit_is_rejected() -> None:
    """The limit verdict is delegated to ``rework_violation`` as is, both branches."""
    beyond = rework_violation(
        BudgetSnapshot(max_rework_rounds=3, used_rework_rounds=0), requested_round=4
    )
    assert beyond is not None
    assert beyond.reason == "rework round 4 exceeds the limit of 3"
    # An overshooting counter (used > max) still blocks through the same rule:
    budget = BudgetSnapshot(max_rework_rounds=3, used_rework_rounds=5)
    decision = plan_rework(budget=budget, passes=[_pass(1, SHA_A)])
    assert decision.outcome == "blocked"
    overshoot = rework_violation(budget, requested_round=6)
    assert overshoot is not None
    assert decision.reason == overshoot.reason


# --- stop condition: declared escalations (priority over repeat and limit) ---


def test_declared_escalation_blocks_and_keeps_the_budget() -> None:
    violation = _requirements_escalation()
    budget = BudgetSnapshot(max_rework_rounds=3, used_rework_rounds=1)
    before = budget.model_copy(deep=True)
    passes = [_pass(1, SHA_A, blocking=(S1,)), _pass(2, SHA_B, blocking=(S1,))]
    passes[-1] = _pass(2, SHA_B, blocking=(S1,), escalations=[violation])
    decision = plan_rework(budget=budget, passes=passes)
    assert decision.outcome == "blocked"
    assert decision.round is None
    assert decision.reason == escalation_stop_reason([violation])
    assert "requirements_deficient" in decision.reason
    # An escalation is not a rework iteration: the counter is untouched.
    assert budget == before
    assert budget.used_rework_rounds == 1


def test_escalation_takes_priority_over_repeat_and_limit() -> None:
    violation = _requirements_escalation()
    budget = BudgetSnapshot(max_rework_rounds=3, used_rework_rounds=3)
    passes = [
        _pass(1, SHA_A, blocking=(S1,)),
        _pass(2, SHA_A, blocking=(S1,), escalations=[violation]),
    ]
    decision = plan_rework(budget=budget, passes=passes)
    assert decision.outcome == "blocked"
    # Same SHA, same findings, exhausted limit — the escalation reason wins.
    assert decision.reason == escalation_stop_reason([violation])


# --- stop condition: no new SHA ---


def test_same_sha_blocks_re_review() -> None:
    budget = BudgetSnapshot(max_rework_rounds=3, used_rework_rounds=1)
    passes = [_pass(1, SHA_A, blocking=(S1,)), _pass(2, SHA_A, blocking=(S2,))]
    decision = plan_rework(budget=budget, passes=passes)
    assert decision.outcome == "blocked"
    assert decision.round is None
    assert "rework did not produce a new commit" in decision.reason
    assert SHA_A in decision.reason
    assert "re-review is impossible" in decision.reason


# --- purity and input validation ---


def test_plan_rework_is_pure() -> None:
    budget = BudgetSnapshot(max_rework_rounds=3, used_rework_rounds=1)
    passes = [_pass(1, SHA_A, blocking=(S1,)), _pass(2, SHA_B, blocking=(S2,))]
    budget_before = budget.model_copy(deep=True)
    passes_before = [item.model_copy(deep=True) for item in passes]
    first = plan_rework(budget=budget, passes=passes)
    second = plan_rework(budget=budget, passes=passes)
    assert first == second
    assert first.outcome == "rework"
    assert first.round == 2
    assert budget == budget_before
    assert passes == passes_before


def test_empty_history_raises() -> None:
    with pytest.raises(ValueError, match="no completed review pass"):
        plan_rework(budget=BudgetSnapshot(), passes=[])


def test_pass_without_blocking_findings_raises() -> None:
    with pytest.raises(ValueError, match="no blocking findings"):
        plan_rework(budget=BudgetSnapshot(), passes=[_pass(1, SHA_A, blocking=())])


def test_review_pass_is_immutable() -> None:
    """A recorded pass never changes: a new attempt appends a new pass (FR-008)."""
    recorded = _pass(1, SHA_A)
    with pytest.raises(ValidationError):
        recorded.round = 2  # type: ignore[misc]


# --- finding signatures ---


def test_finding_signature_ignores_id_sha_and_role() -> None:
    first = finding_signature(make_finding("f-1"))
    second = finding_signature(
        make_finding("f-2").model_copy(update={"reviewed_sha": "aaa111", "role": None})
    )
    assert first == second
    assert hash(first) == hash(second)
    assert len({first, second}) == 1


def test_finding_signature_distinguishes_position_fields() -> None:
    base = finding_signature(make_finding("f-1"))
    assert finding_signature(make_finding("f-2").model_copy(update={"file": "src/other.py"})) != (
        base
    )
    assert finding_signature(make_finding("f-2").model_copy(update={"line": 11})) != base
    assert finding_signature(make_finding("f-2").model_copy(update={"category": "lint"})) != base
    assert (
        finding_signature(make_finding("f-2").model_copy(update={"origin": FindingOrigin.CI}))
        != base
    )


def test_only_blocking_findings_enter_the_loop() -> None:
    """The loop sees only what the caller classifies as blocking (T-020): an open
    blocker gates the loop; resolved and non-blocker findings do not."""
    blocker = make_finding("f-blocker", severity=FindingSeverity.BLOCKER)
    resolved = make_finding(
        "f-resolved", severity=FindingSeverity.BLOCKER, status=FindingStatus.RESOLVED
    ).model_copy(update={"file": "src/fixed.py"})
    minor = make_finding("f-minor", severity=FindingSeverity.MINOR).model_copy(
        update={"file": "src/minor.py"}
    )
    blocker_sig = finding_signature(blocker)
    resolved_sig = finding_signature(resolved)
    minor_sig = finding_signature(minor)
    decision = plan_rework(
        budget=BudgetSnapshot(), passes=[_pass(1, SHA_A, blocking=(blocker_sig,))]
    )
    assert decision.outcome == "rework"
    assert decision.signatures == (blocker_sig,)
    assert resolved_sig not in decision.signatures
    assert minor_sig not in decision.signatures
    assert "src/app.py:10" in decision.reason
    assert "src/fixed.py" not in decision.reason
    assert "src/minor.py" not in decision.reason
