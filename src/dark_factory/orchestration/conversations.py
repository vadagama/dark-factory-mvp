"""Semantics of the discussion: staleness, typed agent inputs, agent output, rework (T080/T081).

Pure and deterministic; the store-backed assembly lives in
``orchestration.state.conversations`` and the API/CLI call both. The rules
(ADR-034 p.2/p.3, ADR-035 p.7):

* **«просмотрено» ≠ «согласовано»** — a view mark is a separate record and
  nothing here derives an approval from it; :func:`revision_state` compares a
  version-bound decision (or check) with the current revision and answers
  ``current`` or ``stale`` — a new revision makes the earlier approvals and
  checks *stale*, it never deletes them;
* a comment's anchor is resolved against the current text
  (:func:`anchor_state`) and a lost anchor is *detached*, never re-attached;
  an open question whose fragment is gone goes ``stale``
  (:func:`apply_revision`);
* the answers, the open comments and the pending rework order are **typed
  inputs** of the next stage attempt (:class:`ConversationInputs`), rendered
  into the agent's instruction as data, not as hidden prompt tricks
  (ADR-034 p.5); the agent answers with structured blocks the executor
  parses (:func:`parse_agent_conversation_output`) — a malformed block is an
  observable error, never a silent drop;
* a rework order is executed through the existing bounded loop
  (:func:`plan_rework_order` → ``orchestration.rework.plan_rework``): the
  counter is the run budget's rework rounds, not the number of comments, and
  the loop's stop conditions (limit, no new revision, repeated remark set)
  produce the escalation reason (ADR-018 p.5).
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from dark_factory.changes.conversations import (
    ArtifactAnchor,
    Comment,
    Question,
    QuestionDraft,
    ReworkOrder,
    ReworkSummary,
)
from dark_factory.changes.enums import AnchorState, FindingOrigin, QuestionStatus
from dark_factory.changes.usage import BudgetSnapshot
from dark_factory.context.artifacts import resolve_anchor
from dark_factory.orchestration.rework import (
    FindingSignature,
    ReviewPass,
    ReworkDecision,
    plan_rework,
)

__all__ = [
    "QUESTIONS_BLOCK",
    "REWORK_SUMMARY_BLOCK",
    "AgentConversationOutput",
    "ConversationInputs",
    "RevisionEffects",
    "RevisionState",
    "anchor_state",
    "apply_revision",
    "parse_agent_conversation_output",
    "plan_rework_order",
    "render_conversation_inputs",
    "revision_state",
]

type RevisionState = Literal["current", "stale", "unbound"]

QUESTIONS_BLOCK: Final[str] = "questions"
"""Fence label of the agent's questions block: ```questions ... ```."""

REWORK_SUMMARY_BLOCK: Final[str] = "rework-summary"
"""Fence label of the agent's rework summary block: ```rework-summary ... ```."""


def revision_state(bound: str | None, current: str | None) -> RevisionState:
    """State of a version-bound decision or check against the current revision (ADR-035 p.7).

    ``unbound`` — the decision names no revision, so it never authorizes the
    current one (ADR-009 p.7); ``stale`` — the artifact moved on; ``current``
    — the decision is about what the operator sees now. An unknown current
    revision keeps a bound decision ``stale``: without a fact nothing is green.
    """
    if bound is None:
        return "unbound"
    if current is None or bound != current:
        return "stale"
    return "current"


def anchor_state(anchor: ArtifactAnchor | None, content: str | None) -> AnchorState:
    """Whether an anchor still resolves in the current text of its artifact (ADR-034 p.1)."""
    if anchor is None:
        return AnchorState.ATTACHED
    return (
        AnchorState.ATTACHED if resolve_anchor(content, anchor.anchor_id) else AnchorState.DETACHED
    )


@dataclass(frozen=True)
class RevisionEffects:
    """What a new revision did to the discussion (ADR-034 p.2, ADR-035 p.7)."""

    stale_questions: tuple[Question, ...]
    detached_comments: tuple[Comment, ...]


def apply_revision(
    questions: Sequence[Question],
    comments: Sequence[Comment],
    documents: Mapping[str, str | None],
) -> RevisionEffects:
    """Mark the questions whose fragment is gone ``stale``; report the detached comments.

    ``documents`` maps artifact path → current text (``None`` = the artifact
    no longer exists). Only the artifacts present in the mapping are judged:
    a question on an artifact the caller did not read is left alone. Answered
    questions are stale too when their fragment is gone (the answer can no
    longer be applied); resolved ones are final. Comments are not mutated —
    their anchor state is a read-model fact — but the detached ones are
    returned so the caller can show them explicitly.
    """
    stale: list[Question] = []
    for question in questions:
        if question.anchor is None or question.anchor.artifact not in documents:
            continue
        if question.status not in {QuestionStatus.OPEN, QuestionStatus.ANSWERED}:
            continue
        if (
            anchor_state(question.anchor, documents[question.anchor.artifact])
            is AnchorState.DETACHED
        ):
            question.apply_status(QuestionStatus.STALE)
            stale.append(question)
    detached = tuple(
        comment
        for comment in comments
        if comment.is_open
        and comment.anchor.artifact in documents
        and anchor_state(comment.anchor, documents[comment.anchor.artifact]) is AnchorState.DETACHED
    )
    return RevisionEffects(stale_questions=tuple(stale), detached_comments=detached)


# --- typed inputs of the next attempt ---------------------------------------------


class ConversationInputs(BaseModel):
    """The discussion the next stage attempt must take into account (ADR-034 p.5).

    Assembled from the store for the phase of the stage: the operator's answers
    that are not yet taken into a revision, the questions still open (the agent
    must not ask them again), the comments the operator left and the rework
    order that sent the work back. Frozen: an input of one attempt.
    """

    model_config = ConfigDict(frozen=True)

    answered_questions: tuple[Question, ...] = ()
    open_questions: tuple[Question, ...] = ()
    open_comments: tuple[Comment, ...] = ()
    rework_order: ReworkOrder | None = None

    @property
    def is_empty(self) -> bool:
        return (
            not self.answered_questions
            and not self.open_questions
            and not self.open_comments
            and self.rework_order is None
        )


def _anchor_text(anchor: ArtifactAnchor | None) -> str:
    if anchor is None:
        return ""
    where = anchor.artifact
    if anchor.anchor_id:
        where += f"#{anchor.anchor_id}"
    return f" [{where}]"


def render_conversation_inputs(inputs: ConversationInputs) -> str:
    """The discussion as plain data lines for the agent's instruction (ADR-034 p.5).

    Deterministic and free of instructions about *how* to work: the skill
    owns the method, this block only states the facts — answers to apply,
    remarks to address, questions not to repeat, and the operator's send-back.
    """
    lines: list[str] = []
    if inputs.rework_order is not None:
        order = inputs.rework_order
        lines.append(f"Rework order {order.id} (round {order.round or '?'}):")
        if order.instruction:
            lines.append(f"  instruction: {order.instruction}")
        for path, revision in sorted(order.revisions.items()):
            lines.append(f"  issued against {path}@{revision}")
    if inputs.answered_questions:
        lines.append("Answers from the operator (apply them to the artifacts):")
        for question in inputs.answered_questions:
            answer = question.answer.value if question.answer is not None else "-"
            note = (
                f" — {question.answer.comment}"
                if question.answer is not None and question.answer.comment
                else ""
            )
            lines.append(f"  - {question.id}{_anchor_text(question.anchor)}: {question.text}")
            lines.append(f"    answer: {answer}{note}")
    if inputs.open_comments:
        lines.append("Open comments from the operator (address each; report it by id):")
        for comment in inputs.open_comments:
            lines.append(f"  - {comment.id}{_anchor_text(comment.anchor)}: {comment.body}")
    if inputs.open_questions:
        lines.append("Questions already asked and still open (do not ask them again):")
        for question in inputs.open_questions:
            lines.append(f"  - {question.id}{_anchor_text(question.anchor)}: {question.text}")
    return "\n".join(lines)


# --- the agent's structured output ------------------------------------------------


@dataclass(frozen=True)
class AgentConversationOutput:
    """What the executor parsed from the agent's text: questions, summary, errors."""

    questions: tuple[QuestionDraft, ...] = ()
    rework_summary: ReworkSummary | None = None
    errors: tuple[str, ...] = ()


def _fenced_blocks(output: str, label: str) -> list[str]:
    pattern = re.compile(rf"```{re.escape(label)}[ \t]*\n(.*?)```", flags=re.DOTALL)
    return [match.group(1) for match in pattern.finditer(output)]


def _load_yaml(text: str) -> Any:
    return yaml.safe_load(text)


def _anchor_from(raw: Any) -> ArtifactAnchor | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        artifact, _, anchor_id = raw.partition("#")
        return ArtifactAnchor(artifact=artifact.strip(), anchor_id=anchor_id or None)
    if isinstance(raw, Mapping):
        return ArtifactAnchor.model_validate(dict(raw))
    raise ValueError("anchor must be 'path#id' or a mapping")


def parse_agent_conversation_output(output: str) -> AgentConversationOutput:
    """Parse the ```questions``` and ```rework-summary``` blocks of the agent's text.

    Every block is YAML: the questions block is a list of mappings with
    ``text`` (required), ``kind`` (``choice|text|number``, default ``text``),
    ``options`` (choice only), ``anchor`` (``"path#id"`` or a mapping) and
    ``blocking`` (default true); the summary block is a mapping with
    ``changed``, ``remaining`` and ``addressed_comments`` lists. A block that
    does not parse or a question that does not validate is reported in
    ``errors`` with its position and skipped — the rest of the output is
    still taken, and nothing is invented for the broken entry.
    """
    questions: list[QuestionDraft] = []
    errors: list[str] = []
    for index, block in enumerate(_fenced_blocks(output, QUESTIONS_BLOCK), start=1):
        try:
            data = _load_yaml(block)
        except yaml.YAMLError as error:
            errors.append(f"questions block {index}: not valid YAML ({type(error).__name__})")
            continue
        if data is None:
            continue
        if not isinstance(data, list):
            errors.append(f"questions block {index}: expected a list of questions")
            continue
        for position, item in enumerate(data, start=1):
            if not isinstance(item, Mapping):
                errors.append(f"questions block {index}, item {position}: expected a mapping")
                continue
            fields = dict(item)
            try:
                fields["anchor"] = _anchor_from(fields.get("anchor"))
                if "options" in fields and fields["options"] is None:
                    fields["options"] = ()
                questions.append(QuestionDraft.model_validate(fields))
            except (ValidationError, ValueError) as error:
                errors.append(f"questions block {index}, item {position}: {_first_error(error)}")
    summary: ReworkSummary | None = None
    for index, block in enumerate(_fenced_blocks(output, REWORK_SUMMARY_BLOCK), start=1):
        try:
            data = _load_yaml(block)
        except yaml.YAMLError as error:
            errors.append(f"rework-summary block {index}: not valid YAML ({type(error).__name__})")
            continue
        if not isinstance(data, Mapping):
            errors.append(f"rework-summary block {index}: expected a mapping")
            continue
        fields = dict(data)
        if "addressed_comments" in fields and "addressed_comment_ids" not in fields:
            fields["addressed_comment_ids"] = fields.pop("addressed_comments")
        try:
            summary = ReworkSummary.model_validate(
                {
                    key: value if value is not None else ()
                    for key, value in fields.items()
                    if key in {"changed", "remaining", "addressed_comment_ids"}
                }
            )
        except ValidationError as error:
            errors.append(f"rework-summary block {index}: {_first_error(error)}")
    return AgentConversationOutput(
        questions=tuple(questions), rework_summary=summary, errors=tuple(errors)
    )


def _first_error(error: Exception) -> str:
    if isinstance(error, ValidationError):
        first = error.errors()[0]
        location = ".".join(str(part) for part in first.get("loc", ()))
        return f"{location}: {first.get('msg', 'invalid')}" if location else str(first.get("msg"))
    return str(error)


# --- the rework loop over rework orders ---------------------------------------------


def _order_signatures(order: ReworkOrder) -> tuple[FindingSignature, ...]:
    """The remarks of an order as loop signatures: one per comment, one for the instruction.

    Identity is the comment id (a re-sent comment is the same remark), so the
    loop's "repeated error" stop fires when the operator sends the very same
    set back after a round changed nothing for it (ADR-018 p.5).
    """
    signatures = [
        FindingSignature(
            origin=FindingOrigin.HUMAN, category=f"comment:{comment_id}", file=None, line=None
        )
        for comment_id in order.comment_ids
    ]
    if order.instruction is not None:
        signatures.append(
            FindingSignature(
                origin=FindingOrigin.HUMAN, category=f"order:{order.id}", file=None, line=None
            )
        )
    if not signatures:
        # An order made of answered questions alone still asks for a round.
        signatures.append(
            FindingSignature(
                origin=FindingOrigin.HUMAN, category=f"answers:{order.id}", file=None, line=None
            )
        )
    return tuple(signatures)


def _order_revision(order: ReworkOrder) -> str:
    """The revision an order was issued against: the joined artifact revisions, or the id."""
    if not order.revisions:
        return f"order:{order.id}"
    return "+".join(f"{path}@{revision}" for path, revision in sorted(order.revisions.items()))


def plan_rework_order(*, budget: BudgetSnapshot, orders: Sequence[ReworkOrder]) -> ReworkDecision:
    """Decide whether the last (pending) rework order may spend a round (ADR-034 p.3).

    The orders of the phase in issue order are the loop history: each is one
    "review pass" whose blocking set is its remarks and whose SHA is the
    artifact revision it was issued against. ``plan_rework`` then applies the
    bounded loop verbatim: the run budget's rework limit, an order issued
    against the same revision as the previous one (the round produced nothing
    new) and the same remark set repeated stop the loop with a human-readable
    reason; otherwise the next round number is the budget's ``used + 1``.
    """
    if not orders:
        raise ValueError("no rework order to plan")
    passes = [
        ReviewPass(round=index, sha=_order_revision(order), blocking=_order_signatures(order))
        for index, order in enumerate(orders, start=1)
    ]
    return plan_rework(budget=budget, passes=passes)
