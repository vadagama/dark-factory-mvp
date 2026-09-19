"""Operator operations of the discussion, shared by the API and the CLI (T086, ADR-033 p.3).

The two surfaces must not disagree on what an answer, a comment, a rework
order or a phase approval does, so the store-backed operation lives here once
and both call it inside their own transaction. Every function raises a typed
:class:`ConversationError` the surface maps onto its own vocabulary (HTTP 404/
409/422 or CLI exit 2) instead of guessing; nothing here commits.

* :func:`answer_question` — the operator's answer, validated against the
  question's kind (ADR-034 p.1); an identical repeat is inert;
* :func:`add_comment` — a remark anchored to a fragment, bound to the current
  head when a repository is bound (ADR-034 p.1);
* :func:`issue_rework_order` — the explicit send-back (the rework order): one
  order per phase at a time, the named comments and questions must exist, the
  comments learn their order, and the version-bound ``rejected`` decision of
  the phase gate is recorded next to it (ADR-034 p.2/p.3, ADR-009 p.7);
* :func:`record_phase_decision` — «Согласовать» / «Пропустить»: the phase gate
  preconditions (T087) and the version binding apply before anything is
  written; ``waived`` needs a reason (ADR-032 p.5).
"""

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from dark_factory.changes.conversations import (
    ArtifactAnchor,
    Comment,
    InvalidAnswer,
    Question,
    ReworkOrder,
)
from dark_factory.changes.enums import DecisionOutcome, DecisionSource, Phase, QuestionStatus
from dark_factory.changes.findings import Decision
from dark_factory.changes.run import Change
from dark_factory.orchestration.artifacts import ArtifactService
from dark_factory.orchestration.phase_gate import PhaseGate, gate_of_phase
from dark_factory.orchestration.state.change_store import DecisionRepository
from dark_factory.orchestration.state.conversation_store import ConversationRepository
from dark_factory.orchestration.state.guidance import build_phase_gate, latest_run

__all__ = [
    "ConversationConflictError",
    "ConversationError",
    "ConversationInputError",
    "ConversationNotFoundError",
    "add_comment",
    "answer_question",
    "derived_id",
    "issue_rework_order",
    "record_phase_decision",
]


class ConversationError(RuntimeError):
    """Base of the typed refusals of the discussion operations."""


class ConversationNotFoundError(ConversationError):
    """The named question, comment or order does not belong to the change (404 / exit 2)."""


class ConversationConflictError(ConversationError):
    """The operation is refused by the current state (409 / exit 2): nothing was written."""


class ConversationInputError(ConversationError):
    """The input does not fit (422 / exit 2): a bad answer, an empty order, a missing reason."""


def derived_id(prefix: str, idempotency_key: str | None) -> str:
    """A stable id from the caller's key, or a fresh one: a retried create creates once."""
    if idempotency_key:
        return f"{prefix}{hashlib.sha256(idempotency_key.encode()).hexdigest()[:16]}"
    return f"{prefix}{uuid4().hex[:16]}"


def _head(change: Change, artifacts: ArtifactService | None) -> str | None:
    return artifacts.head(change) if artifacts is not None else None


def answer_question(
    session: Session,
    change: Change,
    question_id: str,
    *,
    value: str,
    comment: str | None,
    answered_by: str,
) -> tuple[Question, bool]:
    """Record the operator's answer; ``(question, False)`` when the same answer already stands."""
    repository = ConversationRepository(session)
    question = repository.get_question(question_id)
    if question is None or question.change_id != change.id:
        raise ConversationNotFoundError(f"question {question_id!r} does not exist")
    if question.status is not QuestionStatus.OPEN:
        if (
            question.status is QuestionStatus.ANSWERED
            and question.answer is not None
            and question.answer.value == value.strip()
        ):
            return question, False
        raise ConversationConflictError(
            f"question {question_id!r} is {question.status.value}, not open"
        )
    try:
        question.answer_with(value, answered_by=answered_by, comment=comment)
    except InvalidAnswer as error:
        raise ConversationInputError(str(error)) from error
    repository.save_question(question)
    return question, True


def add_comment(
    session: Session,
    change: Change,
    *,
    phase: Phase,
    artifact: str,
    anchor_id: str | None,
    body: str,
    author: str,
    revision: str | None = None,
    idempotency_key: str | None = None,
    artifacts: ArtifactService | None = None,
) -> tuple[Comment, bool]:
    """Leave a remark on a fragment, bound to ``revision`` or the current head (ADR-034 p.1)."""
    bound = revision or _head(change, artifacts)
    comment = Comment(
        id=derived_id("cmt_", idempotency_key),
        change_id=change.id,
        phase=phase,
        anchor=ArtifactAnchor(artifact=artifact, anchor_id=anchor_id, revision=bound),
        body=body,
        author=author,
    )
    return ConversationRepository(session).add_comment(comment)


def issue_rework_order(
    session: Session,
    change: Change,
    *,
    phase: Phase,
    comment_ids: Sequence[str],
    question_ids: Sequence[str],
    instruction: str | None,
    issued_by: str,
    actor_role: str | None,
    expected_revision: str | None = None,
    idempotency_key: str | None = None,
    artifacts: ArtifactService | None = None,
    decision_ids: Sequence[str] = (),
) -> tuple[ReworkOrder, bool]:
    """The explicit send-back (ADR-034 p.2/p.3); ``(order, False)`` replays the same key.

    ``decision_ids`` (T093, ADR-039) names the architecture decisions the
    order asks to reconsider — «Запросить альтернативу»: the refusal is about
    those decisions, and the recorded ``rejected`` decision carries the phase.

    Refused while another order of the phase is pending or running (one round
    at a time), when a named comment or question is unknown, when the order is
    empty, and when ``expected_revision`` is behind the head. Records the
    version-bound ``rejected`` decision of the phase gate in the same
    transaction so the decision history shows the send-back (ADR-009 p.7).
    """
    repository = ConversationRepository(session)
    head = _head(change, artifacts)
    if expected_revision is not None and head is not None and expected_revision != head:
        raise ConversationConflictError("expected_revision is behind the branch head")
    order_id = derived_id("rw_", idempotency_key)
    existing = repository.get_rework_order(order_id)
    if existing is not None:
        return existing, False
    active = repository.pending_rework_order(
        change.id, phase=phase
    ) or repository.active_rework_order(change.id, phase=phase)
    if active is not None:
        raise ConversationConflictError(
            f"rework order {active.id!r} of phase {phase.value} is {active.status.value}"
        )
    comments: list[Comment] = []
    for comment_id in comment_ids:
        comment = repository.get_comment(comment_id)
        if comment is None or comment.change_id != change.id:
            raise ConversationNotFoundError(f"unknown comment {comment_id!r}")
        comments.append(comment)
    for question_id in question_ids:
        question = repository.get_question(question_id)
        if question is None or question.change_id != change.id:
            raise ConversationNotFoundError(f"unknown question {question_id!r}")
    revisions: dict[str, str] = {}
    for comment in comments:
        if comment.anchor.revision is not None:
            revisions.setdefault(comment.anchor.artifact, comment.anchor.revision)
    if head is not None and artifacts is not None:
        for node in artifacts.tree(change).nodes:
            revisions.setdefault(node.path, head)
    try:
        order = ReworkOrder(
            id=order_id,
            change_id=change.id,
            phase=phase,
            revisions=revisions,
            comment_ids=tuple(comment_ids),
            question_ids=tuple(question_ids),
            instruction=instruction,
            decision_ids=tuple(decision_ids),
            issued_by=issued_by,
        )
    except ValueError as error:
        raise ConversationInputError(str(error)) from error
    stored, created = repository.add_rework_order(order)
    for comment in comments:
        comment.rework_order_id = stored.id
        repository.save_comment(comment)
    gate = gate_of_phase(phase)
    if gate is not None:
        DecisionRepository(session).record(
            Decision(
                id=f"dec_{uuid4().hex}",
                gate=gate,
                outcome=DecisionOutcome.REJECTED,
                decided_by=DecisionSource.HUMAN,
                decided_at=datetime.now(UTC),
                commit_sha=head,
                comment=instruction or f"rework order {stored.id}",
                phase=phase,
            ),
            change_id=change.id,
            idempotency_key=f"rework:{stored.id}",
            actor_role=actor_role,
        )
    return stored, created


def record_phase_decision(
    session: Session,
    change: Change,
    *,
    phase: Phase,
    outcome: DecisionOutcome,
    subject_revision: str | None,
    comment: str | None,
    actor_role: str | None,
    idempotency_key: str | None = None,
    artifacts: ArtifactService | None = None,
) -> tuple[Decision, PhaseGate]:
    """Approve or waive a phase under its gate preconditions (T087, ADR-032 p.4/p.5).

    ``approved`` needs an available gate and a ``subject_revision`` equal to
    the current head (a stale revision authorizes nothing, ADR-009 p.7);
    ``waived`` needs a stated reason; ``rejected`` is always allowed — the
    send-back is never blocked (use :func:`issue_rework_order` to also spend
    the round). Returns the recorded decision and the gate view *after* it.
    """
    gate = build_phase_gate(
        session, change, phase=phase, run=latest_run(session, change.id), artifacts=artifacts
    )
    if gate.gate is None:
        raise ConversationConflictError(f"phase {phase.value} has no approval gate")
    if outcome is DecisionOutcome.WAIVED and not (comment or "").strip():
        raise ConversationInputError("a waived phase needs a reason: set comment (ADR-032 p.5)")
    revision = subject_revision or gate.current_revision
    if outcome is DecisionOutcome.APPROVED:
        if not gate.available:
            reasons = "; ".join(f"{reason.what} — {reason.how}" for reason in gate.reasons)
            raise ConversationConflictError(f"the {phase.value} gate is closed: {reasons}")
        if gate.current_revision is not None and revision != gate.current_revision:
            raise ConversationConflictError(
                f"subject_revision {revision} is not the current revision"
                f" {gate.current_revision}: reload and decide on what you see"
            )
    if revision is None and outcome is not DecisionOutcome.WAIVED:
        # A waiver is about the phase, not a document (ADR-032 p.5): a UI-free
        # change has no interface artifacts to bind to, so it stays unbound.
        raise ConversationInputError(
            "the decision needs a revision to bind to: set subject_revision"
            " (no repository is bound to read the current one)"
        )
    decision = Decision(
        id=f"dec_{uuid4().hex}",
        gate=gate.gate,
        outcome=outcome,
        decided_by=DecisionSource.HUMAN,
        decided_at=datetime.now(UTC),
        commit_sha=revision,
        comment=comment,
        phase=phase,
    )
    stored, _ = DecisionRepository(session).record(
        decision, change_id=change.id, idempotency_key=idempotency_key, actor_role=actor_role
    )
    after = build_phase_gate(
        session, change, phase=phase, run=latest_run(session, change.id), artifacts=artifacts
    )
    return stored, after
