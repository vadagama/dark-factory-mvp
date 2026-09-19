"""Discussion semantics: staleness, typed inputs, agent output, rework planning (T080/T081)."""

import pytest

from dark_factory.changes import (
    AnchorState,
    AnswerKind,
    ArtifactAnchor,
    BudgetSnapshot,
    Comment,
    Phase,
    Question,
    QuestionStatus,
    ReworkOrder,
)
from dark_factory.orchestration.conversations import (
    ConversationInputs,
    anchor_state,
    apply_revision,
    parse_agent_conversation_output,
    plan_rework_order,
    render_conversation_inputs,
    revision_state,
)

PATH = "spec/requirements/REQ-001.md"
TEXT_V1 = "# Req\n\n- AC-1: a\n- AC-2: b\n"
TEXT_V2 = "# Req\n\n- AC-1: a\n"


def _question(anchor_id: str | None, status: QuestionStatus = QuestionStatus.OPEN) -> Question:
    question = Question(
        id=f"q_{anchor_id or 'doc'}",
        change_id="chg",
        phase=Phase.REQUIREMENTS,
        text="?",
        anchor=ArtifactAnchor(artifact=PATH, anchor_id=anchor_id, revision="r1"),
    )
    if status is QuestionStatus.ANSWERED:
        question.answer_with("x", answered_by="alice")
    return question


def _comment(anchor_id: str | None) -> Comment:
    return Comment(
        id=f"cmt_{anchor_id or 'doc'}",
        change_id="chg",
        phase=Phase.REQUIREMENTS,
        anchor=ArtifactAnchor(artifact=PATH, anchor_id=anchor_id, revision="r1"),
        body="remark",
        author="alice",
    )


def test_revision_state_stale_current_unbound() -> None:
    assert revision_state("r1", "r1") == "current"
    assert revision_state("r1", "r2") == "stale"
    assert revision_state("r1", None) == "stale", "no fact about the current revision = not green"
    assert revision_state(None, "r1") == "unbound"


def test_anchor_state_is_detached_only_for_a_lost_fragment() -> None:
    assert (
        anchor_state(ArtifactAnchor(artifact=PATH, anchor_id="AC-2"), TEXT_V1)
        is AnchorState.ATTACHED
    )
    assert (
        anchor_state(ArtifactAnchor(artifact=PATH, anchor_id="AC-2"), TEXT_V2)
        is AnchorState.DETACHED
    )
    assert anchor_state(ArtifactAnchor(artifact=PATH), TEXT_V2) is AnchorState.ATTACHED
    assert anchor_state(ArtifactAnchor(artifact=PATH), None) is AnchorState.DETACHED
    assert anchor_state(None, None) is AnchorState.ATTACHED


def test_apply_revision_marks_lost_questions_stale_and_reports_detached_comments() -> None:
    kept = _question("AC-1")
    lost_open = _question("AC-2")
    lost_answered = _question("AC-2", QuestionStatus.ANSWERED)
    lost_answered.id = "q_answered"
    resolved = _question("AC-2")
    resolved.id = "q_resolved"
    resolved.answer_with("x", answered_by="alice")
    resolved.apply_status(QuestionStatus.RESOLVED)
    elsewhere = Question(
        id="q_other",
        change_id="chg",
        phase=Phase.REQUIREMENTS,
        text="?",
        anchor=ArtifactAnchor(artifact="design/overview.md", anchor_id="gone"),
    )
    comments = [_comment("AC-1"), _comment("AC-2"), _comment(None)]
    closed = _comment("AC-2")
    closed.id = "cmt_closed"
    closed.close()

    effects = apply_revision(
        [kept, lost_open, lost_answered, resolved, elsewhere], [*comments, closed], {PATH: TEXT_V2}
    )

    assert {q.id for q in effects.stale_questions} == {"q_AC-2", "q_answered"}
    assert lost_open.status is QuestionStatus.STALE and lost_answered.status is QuestionStatus.STALE
    assert kept.status is QuestionStatus.OPEN
    assert resolved.status is QuestionStatus.RESOLVED, "terminal questions are final"
    assert elsewhere.status is QuestionStatus.OPEN, "artifacts not read are not judged"
    assert [c.id for c in effects.detached_comments] == ["cmt_AC-2"], (
        "closed comments are not reported"
    )


def test_render_inputs_lists_answers_comments_open_questions_and_the_order() -> None:
    answered = _question("AC-1", QuestionStatus.ANSWERED)
    assert answered.answer is not None
    order = ReworkOrder(
        id="rw_1",
        change_id="chg",
        phase=Phase.REQUIREMENTS,
        revisions={PATH: "r1"},
        comment_ids=("cmt_AC-2",),
        instruction="tighten",
        issued_by="alice",
    )
    order.start(round=2, run_id="run")
    text = render_conversation_inputs(
        ConversationInputs(
            answered_questions=(answered,),
            open_questions=(_question("AC-2"),),
            open_comments=(_comment("AC-2"),),
            rework_order=order,
        )
    )
    assert "Rework order rw_1 (round 2)" in text
    assert "instruction: tighten" in text
    assert f"issued against {PATH}@r1" in text
    assert f"q_AC-1 [{PATH}#AC-1]: ?" in text and "answer: x" in text
    assert f"cmt_AC-2 [{PATH}#AC-2]: remark" in text
    assert "do not ask them again" in text and "q_AC-2" in text
    assert ConversationInputs().is_empty
    assert render_conversation_inputs(ConversationInputs()) == ""


AGENT_OUTPUT = """I wrote the spec.

```questions
- text: Round half up or half even?
  kind: choice
  options: [half up, half even]
  anchor: spec/requirements/REQ-001.md#AC-2
- text: Maximum operand length?
  kind: number
  anchor:
    artifact: spec/requirements/REQ-001.md
    anchor_id: AC-1
  blocking: false
- text: ""
- kind: choice
  text: needs options
  options: [only-one]
```

```rework-summary
changed:
  - AC-2 reworded per cmt_1
remaining:
  - AC-3 still open
addressed_comments: [cmt_1]
```
"""


def test_parse_agent_output_takes_valid_entries_and_reports_the_broken_ones() -> None:
    parsed = parse_agent_conversation_output(AGENT_OUTPUT)
    assert [q.kind for q in parsed.questions] == [AnswerKind.CHOICE, AnswerKind.NUMBER]
    first, second = parsed.questions
    assert first.options == ("half up", "half even")
    assert first.anchor == ArtifactAnchor(artifact=PATH, anchor_id="AC-2")
    assert first.blocking is True
    assert second.anchor is not None and second.anchor.anchor_id == "AC-1"
    assert second.blocking is False
    assert len(parsed.errors) == 2
    assert "item 3" in parsed.errors[0] and "item 4" in parsed.errors[1]
    assert parsed.rework_summary is not None
    assert parsed.rework_summary.changed == ("AC-2 reworded per cmt_1",)
    assert parsed.rework_summary.addressed_comment_ids == ("cmt_1",)


def test_parse_agent_output_without_blocks_or_with_bad_yaml_never_raises() -> None:
    assert parse_agent_conversation_output("plain prose") == parse_agent_conversation_output("")
    broken = parse_agent_conversation_output("```questions\n- text: [unclosed\n```")
    assert broken.questions == () and broken.errors and "not valid YAML" in broken.errors[0]
    not_a_list = parse_agent_conversation_output("```questions\ntext: x\n```")
    assert "expected a list" in not_a_list.errors[0]
    empty = parse_agent_conversation_output("```questions\n```\n```rework-summary\n- x\n```")
    assert empty.questions == () and "expected a mapping" in empty.errors[0]


def _order(
    order_id: str, revision: str, *comment_ids: str, instruction: str | None = None
) -> ReworkOrder:
    return ReworkOrder(
        id=order_id,
        change_id="chg",
        phase=Phase.REQUIREMENTS,
        revisions={PATH: revision},
        comment_ids=tuple(comment_ids),
        instruction=instruction,
        issued_by="alice",
    )


def test_plan_rework_order_spends_the_next_round_from_the_run_budget() -> None:
    decision = plan_rework_order(
        budget=BudgetSnapshot(used_rework_rounds=1), orders=[_order("rw_1", "r1", "cmt_1")]
    )
    assert decision.outcome == "rework"
    assert decision.round == 2 and decision.max_rounds == 3
    assert "cmt_1" in decision.reason


def test_plan_rework_order_stops_on_the_limit_same_revision_and_repeated_remarks() -> None:
    exhausted = plan_rework_order(
        budget=BudgetSnapshot(used_rework_rounds=3), orders=[_order("rw_1", "r1", "c")]
    )
    assert exhausted.outcome == "blocked" and "rework limit exhausted" in exhausted.reason

    same_revision = plan_rework_order(
        budget=BudgetSnapshot(used_rework_rounds=1),
        orders=[_order("rw_1", "r1", "c1"), _order("rw_2", "r1", "c2")],
    )
    assert (
        same_revision.outcome == "blocked"
        and "did not produce a new commit" in same_revision.reason
    )

    repeated = plan_rework_order(
        budget=BudgetSnapshot(used_rework_rounds=1),
        orders=[_order("rw_1", "r1", "c1"), _order("rw_2", "r2", "c1")],
    )
    assert repeated.outcome == "blocked" and "did not change the set" in repeated.reason

    progress = plan_rework_order(
        budget=BudgetSnapshot(used_rework_rounds=1),
        orders=[_order("rw_1", "r1", "c1"), _order("rw_2", "r2", "c2")],
    )
    assert progress.outcome == "rework" and progress.round == 2


def test_plan_rework_order_accepts_instruction_only_orders_and_refuses_none() -> None:
    decision = plan_rework_order(
        budget=BudgetSnapshot(), orders=[_order("rw_1", "r1", instruction="tighten")]
    )
    assert decision.outcome == "rework" and decision.round == 1
    with pytest.raises(ValueError):
        plan_rework_order(budget=BudgetSnapshot(), orders=[])
