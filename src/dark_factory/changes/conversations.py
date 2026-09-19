"""Conversations of a ChangeSet: questions, answers, comments, rework orders (T078/T079, ADR-034).

The discussion around an artifact is a first-class domain, not a hidden
prompt (ADR-034 p.5): an agent asks a typed :class:`Question` bound to a phase
and to a fragment of an artifact, the operator gives an :class:`Answer` (a
human decision, never an edit of the artifact), leaves :class:`Comment`\\ s
anchored to fragments and sends a :class:`ReworkOrder` when the agent should
take another round. The entities carry their own lifecycles as explicit tables
— the same convention as ``ChangeRun``/``StageRun`` — and the semantics that
protect trust in approvals live next to them:

* a comment never starts a rework round by itself — a ``ReworkOrder`` does
  (ADR-034 p.2: the explicit send-to-agent action);
* the agent's "fixed" mark is ``addressed``, ready for re-check; only the
  operator ``closed``\\ s a comment;
* an anchor is ``artifact + anchor_id + revision``; when the element disappears
  the anchor is reported *detached* and is never moved to another element;
* the rework round counter is the run budget (``used_rework_rounds``), not the
  number of comments (ADR-034 p.3).

Pure data and transition tables; persistence lives in
``orchestration.state.conversation_store``, the read-model rules
(staleness, anchor resolution) in ``orchestration.conversations``.
"""

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dark_factory.changes.clock import utc_now
from dark_factory.changes.enums import (
    AnswerKind,
    CommentStatus,
    Phase,
    QuestionStatus,
    ReworkOrderStatus,
    Role,
)
from dark_factory.changes.errors import InvalidStatusTransition

__all__ = [
    "COMMENT_STATUS_TRANSITIONS",
    "QUESTION_STATUS_TRANSITIONS",
    "REWORK_ORDER_STATUS_TRANSITIONS",
    "Answer",
    "ArtifactAnchor",
    "Comment",
    "InvalidAnswer",
    "Question",
    "QuestionDraft",
    "ReworkOrder",
    "ReworkSummary",
]

QUESTION_STATUS_TRANSITIONS: Final[dict[QuestionStatus, frozenset[QuestionStatus]]] = {
    QuestionStatus.OPEN: frozenset({QuestionStatus.ANSWERED, QuestionStatus.STALE}),
    # An answered question is resolved when the agent takes the answer into a
    # new revision; it goes stale when the fragment is gone before that.
    QuestionStatus.ANSWERED: frozenset({QuestionStatus.RESOLVED, QuestionStatus.STALE}),
    QuestionStatus.RESOLVED: frozenset(),
    QuestionStatus.STALE: frozenset(),
}
"""``open → answered → resolved | stale`` (ADR-034 p.1); terminal states have no exits."""

COMMENT_STATUS_TRANSITIONS: Final[dict[CommentStatus, frozenset[CommentStatus]]] = {
    CommentStatus.OPEN: frozenset({CommentStatus.ADDRESSED, CommentStatus.CLOSED}),
    # The operator may reopen after re-check: «исправлено» is the agent's claim,
    # not the closure (ADR-034 p.2).
    CommentStatus.ADDRESSED: frozenset({CommentStatus.OPEN, CommentStatus.CLOSED}),
    CommentStatus.CLOSED: frozenset(),
}

REWORK_ORDER_STATUS_TRANSITIONS: Final[dict[ReworkOrderStatus, frozenset[ReworkOrderStatus]]] = {
    ReworkOrderStatus.PENDING: frozenset(
        {ReworkOrderStatus.IN_PROGRESS, ReworkOrderStatus.ESCALATED}
    ),
    ReworkOrderStatus.IN_PROGRESS: frozenset({ReworkOrderStatus.DONE, ReworkOrderStatus.ESCALATED}),
    ReworkOrderStatus.DONE: frozenset(),
    ReworkOrderStatus.ESCALATED: frozenset(),
}


class InvalidAnswer(ValueError):
    """An answer does not fit the question: wrong kind, unknown choice, not a number."""


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


class ArtifactAnchor(BaseModel):
    """Where in which artifact a question or a comment points (ADR-034 p.1).

    ``artifact`` is the path of the document inside the ChangeSet tree,
    ``anchor_id`` the stable id of the element (a frontmatter ``id``, a
    requirement/criterion id such as ``REQ-001``/``AC-2``, or a heading slug)
    and ``revision`` the artifact revision the anchor was made against. Whether
    the anchor still resolves is a read-model fact computed against the current
    revision (``orchestration.conversations.anchor_state``), never stored.
    """

    model_config = ConfigDict(frozen=True)

    artifact: str = Field(min_length=1)
    anchor_id: str | None = None
    revision: str | None = None

    @field_validator("anchor_id", "revision", mode="before")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        return _clean(value)


class Answer(BaseModel):
    """The operator's answer to a question: a recorded human decision (ADR-034 p.1).

    ``value`` is the wire form of the answer (the chosen option, the text or the
    number as text); the kind is validated against the question by
    :meth:`Question.answer`.
    """

    model_config = ConfigDict(frozen=True)

    value: str = Field(min_length=1)
    comment: str | None = None
    answered_by: str = Field(min_length=1)
    answered_at: datetime = Field(default_factory=utc_now)


class QuestionDraft(BaseModel):
    """A question as the agent states it, before it is a stored entity (T078).

    The executor parses drafts out of the agent's structured output
    (``orchestration.conversations.parse_agent_conversation_output``) and the
    runner stores them as :class:`Question`\\ s bound to the change, the run and
    the phase of the stage. Additive to ``StageResult`` (schema version stays 1).
    """

    model_config = ConfigDict(frozen=True)

    text: str = Field(min_length=1)
    kind: AnswerKind = AnswerKind.TEXT
    options: tuple[str, ...] = ()
    anchor: ArtifactAnchor | None = None
    blocking: bool = True
    """Whether the phase gate is unavailable while the question is open (T087)."""

    @field_validator("text", mode="before")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else value

    @field_validator("options", mode="before")
    @classmethod
    def _clean_options(cls, value: Sequence[str]) -> tuple[str, ...]:
        return tuple(item.strip() for item in value if item and item.strip())

    @model_validator(mode="after")
    def _choice_has_options(self) -> "QuestionDraft":
        if self.kind is AnswerKind.CHOICE and len(self.options) < 2:
            raise ValueError("a choice question needs at least two options")
        if self.kind is not AnswerKind.CHOICE and self.options:
            raise ValueError("options are only allowed on a choice question")
        return self


class Question(QuestionDraft):
    """An agent question bound to a change, a phase and (optionally) an artifact fragment.

    Mutable like the run entities: ``status`` moves through :meth:`apply_status`
    against :data:`QUESTION_STATUS_TRANSITIONS`; :meth:`answer` validates the
    operator's answer against ``kind``/``options`` and moves to ``answered``.
    """

    model_config = ConfigDict(frozen=False)

    id: str = Field(min_length=1)
    change_id: str = Field(min_length=1)
    phase: Phase
    run_id: str | None = None
    asked_by: Role | None = None
    status: QuestionStatus = QuestionStatus.OPEN
    answer: Answer | None = None
    asked_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @property
    def is_open(self) -> bool:
        return self.status is QuestionStatus.OPEN

    @property
    def blocks_gate(self) -> bool:
        """An open blocking question makes the phase gate unavailable (T087)."""
        return self.blocking and self.status is QuestionStatus.OPEN

    def apply_status(self, target: QuestionStatus) -> None:
        """Move to ``target``; raises InvalidStatusTransition outside the table."""
        if target not in QUESTION_STATUS_TRANSITIONS[self.status]:
            raise InvalidStatusTransition(
                f"Question transition {self.status.value} -> {target.value} is not allowed"
            )
        self.status = target
        self.updated_at = utc_now()

    def validate_answer(self, value: str) -> str:
        """Normalize ``value`` for this question's kind; raises :class:`InvalidAnswer`."""
        cleaned = value.strip()
        if not cleaned:
            raise InvalidAnswer("an answer cannot be blank")
        match self.kind:
            case AnswerKind.CHOICE:
                if cleaned not in self.options:
                    raise InvalidAnswer(
                        f"answer {cleaned!r} is not one of the options: " + ", ".join(self.options)
                    )
                return cleaned
            case AnswerKind.NUMBER:
                try:
                    Decimal(cleaned)
                except InvalidOperation as error:
                    raise InvalidAnswer(f"answer {cleaned!r} is not a number") from error
                return cleaned
            case AnswerKind.TEXT:
                return cleaned

    def answer_with(self, value: str, *, answered_by: str, comment: str | None = None) -> Answer:
        """Record the operator's answer and move to ``answered`` (one validation point)."""
        normalized = self.validate_answer(value)
        self.apply_status(QuestionStatus.ANSWERED)
        self.answer = Answer(value=normalized, comment=_clean(comment), answered_by=answered_by)
        return self.answer


class Comment(BaseModel):
    """An operator remark anchored to an artifact fragment (ADR-034 p.1/p.2).

    A comment never triggers rework by itself; ``rework_order_id`` records the
    order that carried it to the agent, if any. ``addressed`` is the agent's
    claim, ``closed`` the operator's decision.
    """

    id: str = Field(min_length=1)
    change_id: str = Field(min_length=1)
    phase: Phase
    anchor: ArtifactAnchor
    body: str = Field(min_length=1)
    author: str = Field(min_length=1)
    status: CommentStatus = CommentStatus.OPEN
    rework_order_id: str | None = None
    addressed_note: str | None = None
    """The agent's note on what it changed for this comment (set with ``addressed``)."""
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("body", mode="before")
    @classmethod
    def _strip_body(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else value

    @property
    def is_open(self) -> bool:
        """Open or addressed-but-not-closed: the operator still owes a re-check."""
        return self.status is not CommentStatus.CLOSED

    def apply_status(self, target: CommentStatus) -> None:
        """Move to ``target``; raises InvalidStatusTransition outside the table."""
        if target not in COMMENT_STATUS_TRANSITIONS[self.status]:
            raise InvalidStatusTransition(
                f"Comment transition {self.status.value} -> {target.value} is not allowed"
            )
        self.status = target
        self.updated_at = utc_now()

    def mark_addressed(self, note: str | None = None) -> None:
        """The agent's "fixed" mark: ready for re-check, not closed (ADR-034 p.2)."""
        self.apply_status(CommentStatus.ADDRESSED)
        self.addressed_note = _clean(note)

    def close(self) -> None:
        """The operator closes the remark — the only way to ``closed``."""
        self.apply_status(CommentStatus.CLOSED)

    def reopen(self) -> None:
        """The operator re-checked and disagrees with the agent's "fixed" mark."""
        self.apply_status(CommentStatus.OPEN)


class ReworkSummary(BaseModel):
    """The agent's report after a rework round: what changed, what remains (ADR-034).

    ``addressed_comment_ids`` names the comments the agent claims to have
    handled; the store moves those to ``addressed`` — never to ``closed``.
    """

    model_config = ConfigDict(frozen=True)

    changed: tuple[str, ...] = ()
    remaining: tuple[str, ...] = ()
    addressed_comment_ids: tuple[str, ...] = ()

    @field_validator("changed", "remaining", "addressed_comment_ids", mode="before")
    @classmethod
    def _clean_lines(cls, value: Sequence[str]) -> tuple[str, ...]:
        return tuple(item.strip() for item in value if item and item.strip())


class ReworkOrder(BaseModel):
    """An explicit send-back to the agent (the operator's "rework" action, ADR-034 p.1/p.3).

    Bound to the artifact revisions it was issued against (``revisions``) and
    to the comments and answered questions it carries (``comment_ids``,
    ``question_ids``). ``round`` is the rework round of the run the order was
    executed in — filled when the round starts, not on creation, because the
    counter is the run budget (ADR-034 p.3).
    """

    id: str = Field(min_length=1)
    change_id: str = Field(min_length=1)
    phase: Phase
    revisions: dict[str, str] = Field(default_factory=dict)
    """Artifact path → revision the order was issued against."""
    comment_ids: tuple[str, ...] = ()
    question_ids: tuple[str, ...] = ()
    instruction: str | None = None
    """The operator's free-text instruction in addition to the comments."""
    decision_ids: tuple[str, ...] = ()
    """Architecture decisions (ADR ids) the order asks to reconsider — «Запросить
    альтернативу» (T093, ADR-039). The refusal is about *these* decisions, not the phase;
    the derived status of each named ADR becomes ``needs_revision`` while the order is open."""
    issued_by: str = Field(min_length=1)
    status: ReworkOrderStatus = ReworkOrderStatus.PENDING
    round: int | None = Field(default=None, ge=1)
    run_id: str | None = None
    summary: ReworkSummary | None = None
    escalation_reason: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("instruction", mode="before")
    @classmethod
    def _strip_instruction(cls, value: str | None) -> str | None:
        return _clean(value)

    @model_validator(mode="after")
    def _has_content(self) -> "ReworkOrder":
        if not self.comment_ids and not self.question_ids and self.instruction is None:
            raise ValueError(
                "a rework order needs at least one comment, one answered question or an instruction"
            )
        return self

    @property
    def is_pending(self) -> bool:
        return self.status is ReworkOrderStatus.PENDING

    def apply_status(self, target: ReworkOrderStatus) -> None:
        """Move to ``target``; raises InvalidStatusTransition outside the table."""
        if target not in REWORK_ORDER_STATUS_TRANSITIONS[self.status]:
            raise InvalidStatusTransition(
                f"ReworkOrder transition {self.status.value} -> {target.value} is not allowed"
            )
        self.status = target
        self.updated_at = utc_now()

    def start(self, *, round: int, run_id: str) -> None:
        """A rework round of the run picked the order up (counter = the run budget)."""
        self.apply_status(ReworkOrderStatus.IN_PROGRESS)
        self.round = round
        self.run_id = run_id

    def finish(self, summary: ReworkSummary) -> None:
        """The agent reported its summary; the order is done, the comments are only addressed."""
        self.apply_status(ReworkOrderStatus.DONE)
        self.summary = summary

    def escalate(self, reason: str) -> None:
        """The loop stopped before the order was done (limit or stop condition, ADR-018 p.5)."""
        self.apply_status(ReworkOrderStatus.ESCALATED)
        self.escalation_reason = reason
