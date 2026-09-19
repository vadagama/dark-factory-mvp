"""Phase gate preconditions (T087, ADR-032 p.4, ADR-033 p.4, ADR-035 p.7)."""

from datetime import UTC, datetime

from dark_factory.changes import (
    ArtifactAnchor,
    BudgetSnapshot,
    Comment,
    Decision,
    DecisionOutcome,
    DecisionSource,
    Gate,
    Phase,
    Question,
    ReworkOrder,
)
from dark_factory.orchestration.phase_gate import PHASE_GATE, gate_of_phase, phase_gate

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


def _question(question_id: str, *, blocking: bool = True, answered: bool = False) -> Question:
    question = Question(
        id=question_id, change_id="chg", phase=Phase.REQUIREMENTS, text="?", blocking=blocking
    )
    if answered:
        question.answer_with("x", answered_by="alice")
    return question


def _decision(
    outcome: DecisionOutcome, sha: str | None, gate: Gate = Gate.SPECIFICATION
) -> Decision:
    return Decision(
        id=f"dec-{outcome.value}-{sha}",
        gate=gate,
        outcome=outcome,
        decided_by=DecisionSource.HUMAN,
        decided_at=NOW,
        commit_sha=sha,
    )


def test_phase_gate_table_is_total_and_requirements_use_the_specification_gate() -> None:
    assert set(PHASE_GATE) == set(Phase)
    assert gate_of_phase(Phase.REQUIREMENTS) is Gate.SPECIFICATION
    assert gate_of_phase(Phase.INITIATIVE) is None


def test_gate_is_available_with_a_revision_and_no_blockers() -> None:
    gate = phase_gate(change_id="chg", phase=Phase.REQUIREMENTS, current_revision="r2")
    assert gate.available and gate.reasons == ()
    assert gate.gate is Gate.SPECIFICATION and gate.skippable
    assert not gate.approved


def test_blocking_questions_close_the_gate_with_an_actionable_reason() -> None:
    gate = phase_gate(
        change_id="chg",
        phase=Phase.REQUIREMENTS,
        questions=[
            _question("q1"),
            _question("q2", blocking=False),
            _question("q3", answered=True),
        ],
        current_revision="r2",
    )
    assert not gate.available
    assert (
        gate.blocking_questions == 1 and gate.open_questions == 2 and gate.answered_questions == 1
    )
    [reason] = gate.reasons
    assert "q1" in reason.what and "factory change answer --id chg" in reason.how


def test_missing_revision_and_rework_state_close_the_gate() -> None:
    no_revision = phase_gate(change_id="chg", phase=Phase.REQUIREMENTS, current_revision=None)
    assert not no_revision.available
    assert "factory run advance --change-id chg" in no_revision.reasons[0].how

    pending = ReworkOrder(
        id="rw", change_id="chg", phase=Phase.REQUIREMENTS, instruction="x", issued_by="a"
    )
    with_pending = phase_gate(
        change_id="chg", phase=Phase.REQUIREMENTS, rework_orders=[pending], current_revision="r2"
    )
    assert not with_pending.available and with_pending.rework_pending
    assert "раунд ещё не запущен" in with_pending.reasons[0].what

    running = ReworkOrder(
        id="rw2", change_id="chg", phase=Phase.REQUIREMENTS, instruction="x", issued_by="a"
    )
    running.start(round=2, run_id="run")
    with_running = phase_gate(
        change_id="chg",
        phase=Phase.REQUIREMENTS,
        rework_orders=[running],
        current_revision="r2",
        budget=BudgetSnapshot(used_rework_rounds=2),
    )
    assert with_running.rework_in_progress and "раунд 2 из 3" in with_running.reasons[0].what


def test_open_comments_do_not_block_but_are_counted() -> None:
    anchor = ArtifactAnchor(artifact="spec/x.md", anchor_id="AC-1")
    open_comment = Comment(
        id="c1", change_id="chg", phase=Phase.REQUIREMENTS, anchor=anchor, body="b", author="a"
    )
    addressed = Comment(
        id="c2", change_id="chg", phase=Phase.REQUIREMENTS, anchor=anchor, body="b", author="a"
    )
    addressed.mark_addressed()
    gate = phase_gate(
        change_id="chg",
        phase=Phase.REQUIREMENTS,
        comments=[open_comment, addressed],
        current_revision="r2",
        detached_comment_ids=["c1", "c1"],
    )
    assert gate.available
    assert (gate.open_comments, gate.addressed_comments, gate.detached_comments) == (1, 1, 1)


def test_approvals_are_version_bound_and_stale_after_a_new_revision() -> None:
    decisions = [
        _decision(DecisionOutcome.APPROVED, "r1"),
        _decision(DecisionOutcome.REJECTED, "r2"),
        _decision(DecisionOutcome.APPROVED, None),
        _decision(DecisionOutcome.APPROVED, "r2", gate=Gate.UI),
    ]
    stale = phase_gate(
        change_id="chg", phase=Phase.REQUIREMENTS, decisions=decisions, current_revision="r2"
    )
    assert not stale.approved, "the approval of r1 is stale at r2; the unbound one never counts"
    assert [(a.outcome, a.state) for a in stale.approvals] == [
        (DecisionOutcome.APPROVED, "stale"),
        (DecisionOutcome.REJECTED, "current"),
        (DecisionOutcome.APPROVED, "unbound"),
    ], "the UI-gate decision belongs to another phase"
    current = phase_gate(
        change_id="chg",
        phase=Phase.REQUIREMENTS,
        decisions=[_decision(DecisionOutcome.APPROVED, "r2")],
        current_revision="r2",
    )
    assert current.approved


def test_a_phase_without_a_gate_is_never_available_nor_skippable() -> None:
    gate = phase_gate(change_id="chg", phase=Phase.INITIATIVE, current_revision="r1")
    assert not gate.available and not gate.skippable and gate.gate is None
