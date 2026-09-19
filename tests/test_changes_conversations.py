"""Discussion domain: questions, answers, comments, rework orders (T078/T079, ADR-034)."""

import pytest
from pydantic import ValidationError

from dark_factory.changes import (
    COMMENT_STATUS_TRANSITIONS,
    QUESTION_STATUS_TRANSITIONS,
    REWORK_ORDER_STATUS_TRANSITIONS,
    AnswerKind,
    ArtifactAnchor,
    Comment,
    CommentStatus,
    InvalidAnswer,
    InvalidStatusTransition,
    Phase,
    Question,
    QuestionDraft,
    QuestionStatus,
    ReworkOrder,
    ReworkOrderStatus,
    ReworkSummary,
    Role,
)


def _status(
    entity: Question | Comment | ReworkOrder,
) -> QuestionStatus | CommentStatus | ReworkOrderStatus:
    """The current status read through a call: mypy narrows ``entity.status`` to the literal
    asserted before a mutation, so a direct comparison after the mutation reads as
    non-overlapping — the declared return type keeps the full enum."""
    return entity.status


def _round(order: ReworkOrder) -> int | None:
    """``order.round`` without the ``None`` narrowing of the assertion before ``start``."""
    return order.round


ANCHOR = ArtifactAnchor(
    artifact="spec/requirements/REQ-001.md", anchor_id="REQ-001", revision="abc"
)


def _question(kind: AnswerKind = AnswerKind.TEXT, options: tuple[str, ...] = ()) -> Question:
    return Question(
        id="q_1",
        change_id="chg_1",
        phase=Phase.REQUIREMENTS,
        text="Which rounding?",
        kind=kind,
        options=options,
        anchor=ANCHOR,
        asked_by=Role.PRODUCT,
    )


# --- questions ------------------------------------------------------------------------


def test_question_lifecycle_open_answered_resolved() -> None:
    question = _question()
    assert question.status is QuestionStatus.OPEN
    assert question.blocks_gate
    answer = question.answer_with("  round half up ", answered_by="alice", comment=" ok ")
    assert _status(question) is QuestionStatus.ANSWERED
    assert answer.value == "round half up"
    assert answer.comment == "ok"
    assert not question.blocks_gate
    question.apply_status(QuestionStatus.RESOLVED)
    assert question.status is QuestionStatus.RESOLVED
    with pytest.raises(InvalidStatusTransition):
        question.apply_status(QuestionStatus.OPEN)


def test_question_goes_stale_from_open_and_from_answered_but_never_back() -> None:
    open_question = _question()
    open_question.apply_status(QuestionStatus.STALE)
    assert open_question.status is QuestionStatus.STALE
    answered = _question()
    answered.answer_with("x", answered_by="alice")
    answered.apply_status(QuestionStatus.STALE)
    with pytest.raises(InvalidStatusTransition):
        answered.apply_status(QuestionStatus.ANSWERED)
    # The table is total over the enum and the terminal states have no exits.
    assert set(QUESTION_STATUS_TRANSITIONS) == set(QuestionStatus)
    assert QUESTION_STATUS_TRANSITIONS[QuestionStatus.RESOLVED] == frozenset()
    assert QUESTION_STATUS_TRANSITIONS[QuestionStatus.STALE] == frozenset()


def test_a_choice_answer_must_be_one_of_the_options() -> None:
    question = _question(AnswerKind.CHOICE, ("cents", "whole units"))
    with pytest.raises(InvalidAnswer):
        question.answer_with("dollars", answered_by="alice")
    assert question.status is QuestionStatus.OPEN, "a rejected answer changes nothing"
    assert question.answer_with("cents", answered_by="alice").value == "cents"


def test_a_number_answer_must_parse_and_a_blank_answer_is_rejected() -> None:
    question = _question(AnswerKind.NUMBER)
    with pytest.raises(InvalidAnswer):
        question.answer_with("two", answered_by="alice")
    with pytest.raises(InvalidAnswer):
        question.answer_with("   ", answered_by="alice")
    assert question.answer_with(" 2.5 ", answered_by="alice").value == "2.5"


def test_a_choice_question_needs_options_and_other_kinds_refuse_them() -> None:
    with pytest.raises(ValidationError):
        QuestionDraft(text="pick", kind=AnswerKind.CHOICE, options=("only one",))
    with pytest.raises(ValidationError):
        QuestionDraft(text="free", kind=AnswerKind.TEXT, options=("a", "b"))
    draft = QuestionDraft(text=" pick ", kind=AnswerKind.CHOICE, options=(" a ", "", "b"))
    assert draft.text == "pick"
    assert draft.options == ("a", "b")
    assert draft.blocking is True


def test_a_non_blocking_open_question_does_not_block_the_gate() -> None:
    question = Question(
        id="q", change_id="c", phase=Phase.REQUIREMENTS, text="nice to know?", blocking=False
    )
    assert question.is_open and not question.blocks_gate


# --- comments -------------------------------------------------------------------------


def _comment() -> Comment:
    return Comment(
        id="cmt_1",
        change_id="chg_1",
        phase=Phase.REQUIREMENTS,
        anchor=ANCHOR,
        body="  too vague  ",
        author="alice",
    )


def test_comment_addressed_is_not_closed_and_only_the_operator_closes() -> None:
    comment = _comment()
    assert comment.body == "too vague"
    comment.mark_addressed(" reworded the criterion ")
    assert comment.status is CommentStatus.ADDRESSED
    assert comment.addressed_note == "reworded the criterion"
    assert comment.is_open, "addressed still owes the operator's re-check"
    comment.reopen()
    assert _status(comment) is CommentStatus.OPEN
    comment.close()
    assert _status(comment) is CommentStatus.CLOSED
    assert not comment.is_open
    with pytest.raises(InvalidStatusTransition):
        comment.mark_addressed()
    assert set(COMMENT_STATUS_TRANSITIONS) == set(CommentStatus)


def test_comment_carries_the_anchor_with_artifact_anchor_id_and_revision() -> None:
    comment = _comment()
    assert comment.anchor == ANCHOR
    assert comment.rework_order_id is None, "a comment never starts rework by itself"
    loose = ArtifactAnchor(artifact="spec/delta.yaml", anchor_id="  ", revision=None)
    assert loose.anchor_id is None


# --- rework orders --------------------------------------------------------------------


def test_rework_order_lifecycle_and_round_from_the_run_budget() -> None:
    order = ReworkOrder(
        id="rw_1",
        change_id="chg_1",
        phase=Phase.REQUIREMENTS,
        revisions={"spec/requirements/REQ-001.md": "abc"},
        comment_ids=("cmt_1",),
        issued_by="alice",
    )
    assert order.is_pending
    assert order.round is None, "the round is the run's counter, set when the round starts"
    order.start(round=2, run_id="run_1")
    assert _status(order) is ReworkOrderStatus.IN_PROGRESS
    assert (_round(order), order.run_id) == (2, "run_1")
    order.finish(
        ReworkSummary(changed=("REQ-001 reworded",), remaining=(), addressed_comment_ids=("cmt_1",))
    )
    assert _status(order) is ReworkOrderStatus.DONE
    assert order.summary is not None and order.summary.addressed_comment_ids == ("cmt_1",)
    with pytest.raises(InvalidStatusTransition):
        order.escalate("too late")
    assert set(REWORK_ORDER_STATUS_TRANSITIONS) == set(ReworkOrderStatus)


def test_rework_order_escalates_from_pending_or_in_progress() -> None:
    order = ReworkOrder(
        id="rw_1",
        change_id="chg_1",
        phase=Phase.REQUIREMENTS,
        instruction=" tighten scope ",
        issued_by="alice",
    )
    assert order.instruction == "tighten scope"
    order.escalate("rework limit exhausted: 3/3 rounds used")
    assert order.status is ReworkOrderStatus.ESCALATED
    assert order.escalation_reason == "rework limit exhausted: 3/3 rounds used"


def test_a_rework_order_without_content_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ReworkOrder(
            id="rw", change_id="c", phase=Phase.REQUIREMENTS, issued_by="alice", instruction="  "
        )
