"""Repositories of the discussion and document records (T078-T085, ADR-034/ADR-035).

Thin, idempotent and transaction-agnostic like ``change_store``: every method
runs inside the caller's session and commits nothing. The full documents live
in ``payload`` (JSONB); the denormalized columns exist for the lists and the
gate preconditions (T087) — status, phase, artifact, blocking.

* :class:`ConversationRepository` — questions, comments, rework orders of a
  change. Adds are idempotent by id (the runner stores the agent's questions
  under deterministic ids derived from the attempt, so a replayed advance
  cannot duplicate them); saves replace the payload of an existing row.
* :class:`ArtifactDraftRepository` — the one autosave draft per artifact
  (ADR-035 p.4): ``put`` upserts, ``delete`` is the explicit save's
  bookkeeping, a missing draft reads as ``None``.
* :class:`ArtifactViewRepository` — «просмотрено» marks per revision
  (ADR-034 p.2): a set, never a decision.
"""

from collections.abc import Sequence
from typing import Final

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from dark_factory.changes.conversations import Comment, Question, ReworkOrder
from dark_factory.changes.documents import ArtifactDraft, ArtifactViewMark
from dark_factory.changes.enums import CommentStatus, Phase, QuestionStatus, ReworkOrderStatus
from dark_factory.orchestration.state.models import ArtifactDraft as ArtifactDraftRow
from dark_factory.orchestration.state.models import ArtifactView as ArtifactViewRow
from dark_factory.orchestration.state.models import Comment as CommentRow
from dark_factory.orchestration.state.models import Question as QuestionRow
from dark_factory.orchestration.state.models import ReworkOrder as ReworkOrderRow
from dark_factory.orchestration.state.repositories import StateConflictError

__all__ = [
    "ARTIFACT_DRAFT_ACTION",
    "ARTIFACT_EDIT_ACTION",
    "ARTIFACT_VIEW_ACTION",
    "COMMENT_ADD_ACTION",
    "COMMENT_UPDATE_ACTION",
    "QUESTION_ANSWER_ACTION",
    "QUESTION_ASK_ACTION",
    "REWORK_ORDER_ACTION",
    "ArtifactDraftRepository",
    "ArtifactViewRepository",
    "ConversationRepository",
]

QUESTION_ASK_ACTION: Final[str] = "question.ask"
QUESTION_ANSWER_ACTION: Final[str] = "question.answer"
COMMENT_ADD_ACTION: Final[str] = "comment.add"
COMMENT_UPDATE_ACTION: Final[str] = "comment.update"
REWORK_ORDER_ACTION: Final[str] = "rework_order.issue"
ARTIFACT_EDIT_ACTION: Final[str] = "artifact.edit"
ARTIFACT_DRAFT_ACTION: Final[str] = "artifact.draft"
ARTIFACT_VIEW_ACTION: Final[str] = "artifact.view"


def _question_from_row(row: QuestionRow) -> Question:
    return Question.model_validate(row.payload)


def _comment_from_row(row: CommentRow) -> Comment:
    return Comment.model_validate(row.payload)


def _order_from_row(row: ReworkOrderRow) -> ReworkOrder:
    return ReworkOrder.model_validate(row.payload)


class ConversationRepository:
    """Questions, comments and rework orders of a change (ADR-034)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # --- questions ---------------------------------------------------------

    def add_question(self, question: Question) -> tuple[Question, bool]:
        """Insert one question; ``(existing, False)`` when the id is already stored."""
        stmt = (
            pg_insert(QuestionRow)
            .values(
                id=question.id,
                change_id=question.change_id,
                run_id=question.run_id,
                phase=question.phase.value,
                kind=question.kind.value,
                status=question.status.value,
                blocking=question.blocking,
                artifact=question.anchor.artifact if question.anchor is not None else None,
                anchor_id=question.anchor.anchor_id if question.anchor is not None else None,
                payload=question.model_dump(mode="json"),
                asked_at=question.asked_at,
                updated_at=question.updated_at,
            )
            .on_conflict_do_nothing(index_elements=[QuestionRow.id])
            .returning(QuestionRow.id)
        )
        inserted = self._session.execute(stmt).scalar_one_or_none()
        if inserted is not None:
            return question, True
        existing = self.get_question(question.id)
        if existing is None:  # pragma: no cover - defensive
            raise StateConflictError(f"question {question.id!r} was not stored")
        return existing, False

    def get_question(self, question_id: str) -> Question | None:
        row = self._session.get(QuestionRow, question_id)
        return _question_from_row(row) if row is not None else None

    def save_question(self, question: Question) -> Question:
        """Replace the stored document of an existing question (status, answer)."""
        row = self._session.get(QuestionRow, question.id, with_for_update=True)
        if row is None:
            raise StateConflictError(f"question {question.id!r} does not exist")
        row.status = question.status.value
        row.blocking = question.blocking
        row.payload = question.model_dump(mode="json")
        row.updated_at = question.updated_at
        self._session.flush()
        return question

    def list_questions(
        self,
        change_id: str,
        *,
        phase: Phase | None = None,
        status: QuestionStatus | Sequence[QuestionStatus] | None = None,
        artifact: str | None = None,
    ) -> list[Question]:
        """Questions of a change in asking order, optionally narrowed."""
        stmt = (
            select(QuestionRow)
            .where(QuestionRow.change_id == change_id)
            .order_by(QuestionRow.asked_at, QuestionRow.id)
        )
        if phase is not None:
            stmt = stmt.where(QuestionRow.phase == phase.value)
        if status is not None:
            statuses = [status] if isinstance(status, QuestionStatus) else list(status)
            stmt = stmt.where(QuestionRow.status.in_([item.value for item in statuses]))
        if artifact is not None:
            stmt = stmt.where(QuestionRow.artifact == artifact)
        return [_question_from_row(row) for row in self._session.execute(stmt).scalars()]

    # --- comments ----------------------------------------------------------

    def add_comment(self, comment: Comment) -> tuple[Comment, bool]:
        """Insert one comment; ``(existing, False)`` when the id is already stored."""
        stmt = (
            pg_insert(CommentRow)
            .values(
                id=comment.id,
                change_id=comment.change_id,
                phase=comment.phase.value,
                status=comment.status.value,
                artifact=comment.anchor.artifact,
                anchor_id=comment.anchor.anchor_id,
                anchor_revision=comment.anchor.revision,
                rework_order_id=comment.rework_order_id,
                payload=comment.model_dump(mode="json"),
                created_at=comment.created_at,
                updated_at=comment.updated_at,
            )
            .on_conflict_do_nothing(index_elements=[CommentRow.id])
            .returning(CommentRow.id)
        )
        inserted = self._session.execute(stmt).scalar_one_or_none()
        if inserted is not None:
            return comment, True
        existing = self.get_comment(comment.id)
        if existing is None:  # pragma: no cover - defensive
            raise StateConflictError(f"comment {comment.id!r} was not stored")
        return existing, False

    def get_comment(self, comment_id: str) -> Comment | None:
        row = self._session.get(CommentRow, comment_id)
        return _comment_from_row(row) if row is not None else None

    def save_comment(self, comment: Comment) -> Comment:
        row = self._session.get(CommentRow, comment.id, with_for_update=True)
        if row is None:
            raise StateConflictError(f"comment {comment.id!r} does not exist")
        row.status = comment.status.value
        row.rework_order_id = comment.rework_order_id
        row.payload = comment.model_dump(mode="json")
        row.updated_at = comment.updated_at
        self._session.flush()
        return comment

    def list_comments(
        self,
        change_id: str,
        *,
        phase: Phase | None = None,
        status: CommentStatus | Sequence[CommentStatus] | None = None,
        artifact: str | None = None,
    ) -> list[Comment]:
        stmt = (
            select(CommentRow)
            .where(CommentRow.change_id == change_id)
            .order_by(CommentRow.created_at, CommentRow.id)
        )
        if phase is not None:
            stmt = stmt.where(CommentRow.phase == phase.value)
        if status is not None:
            statuses = [status] if isinstance(status, CommentStatus) else list(status)
            stmt = stmt.where(CommentRow.status.in_([item.value for item in statuses]))
        if artifact is not None:
            stmt = stmt.where(CommentRow.artifact == artifact)
        return [_comment_from_row(row) for row in self._session.execute(stmt).scalars()]

    # --- rework orders -----------------------------------------------------

    def add_rework_order(self, order: ReworkOrder) -> tuple[ReworkOrder, bool]:
        stmt = (
            pg_insert(ReworkOrderRow)
            .values(
                id=order.id,
                change_id=order.change_id,
                run_id=order.run_id,
                phase=order.phase.value,
                status=order.status.value,
                round=order.round,
                payload=order.model_dump(mode="json"),
                created_at=order.created_at,
                updated_at=order.updated_at,
            )
            .on_conflict_do_nothing(index_elements=[ReworkOrderRow.id])
            .returning(ReworkOrderRow.id)
        )
        inserted = self._session.execute(stmt).scalar_one_or_none()
        if inserted is not None:
            return order, True
        existing = self.get_rework_order(order.id)
        if existing is None:  # pragma: no cover - defensive
            raise StateConflictError(f"rework order {order.id!r} was not stored")
        return existing, False

    def get_rework_order(self, order_id: str) -> ReworkOrder | None:
        row = self._session.get(ReworkOrderRow, order_id)
        return _order_from_row(row) if row is not None else None

    def save_rework_order(self, order: ReworkOrder) -> ReworkOrder:
        row = self._session.get(ReworkOrderRow, order.id, with_for_update=True)
        if row is None:
            raise StateConflictError(f"rework order {order.id!r} does not exist")
        row.status = order.status.value
        row.round = order.round
        row.run_id = order.run_id
        row.payload = order.model_dump(mode="json")
        row.updated_at = order.updated_at
        self._session.flush()
        return order

    def list_rework_orders(
        self,
        change_id: str,
        *,
        phase: Phase | None = None,
        status: ReworkOrderStatus | Sequence[ReworkOrderStatus] | None = None,
    ) -> list[ReworkOrder]:
        stmt = (
            select(ReworkOrderRow)
            .where(ReworkOrderRow.change_id == change_id)
            .order_by(ReworkOrderRow.created_at, ReworkOrderRow.id)
        )
        if phase is not None:
            stmt = stmt.where(ReworkOrderRow.phase == phase.value)
        if status is not None:
            statuses = [status] if isinstance(status, ReworkOrderStatus) else list(status)
            stmt = stmt.where(ReworkOrderRow.status.in_([item.value for item in statuses]))
        return [_order_from_row(row) for row in self._session.execute(stmt).scalars()]

    def pending_rework_order(
        self, change_id: str, *, phase: Phase | None = None
    ) -> ReworkOrder | None:
        """The order waiting to be picked up by a rework round, oldest first, or ``None``."""
        orders = self.list_rework_orders(change_id, phase=phase, status=ReworkOrderStatus.PENDING)
        return orders[0] if orders else None

    def active_rework_order(
        self, change_id: str, *, phase: Phase | None = None
    ) -> ReworkOrder | None:
        """The order a running rework round is executing, or ``None``."""
        orders = self.list_rework_orders(
            change_id, phase=phase, status=ReworkOrderStatus.IN_PROGRESS
        )
        return orders[0] if orders else None


class ArtifactDraftRepository:
    """The one autosave draft per artifact of a change (T085, ADR-035 p.4)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def put(self, draft: ArtifactDraft) -> ArtifactDraft:
        """Upsert the draft of ``(change_id, artifact)``: the newest state wins."""
        stmt = pg_insert(ArtifactDraftRow).values(
            change_id=draft.change_id,
            artifact=draft.artifact,
            content=draft.content,
            base_revision=draft.base_revision,
            saved_by=draft.saved_by,
            updated_at=draft.updated_at,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[ArtifactDraftRow.change_id, ArtifactDraftRow.artifact],
            set_={
                "content": stmt.excluded.content,
                "base_revision": stmt.excluded.base_revision,
                "saved_by": stmt.excluded.saved_by,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        self._session.execute(stmt)
        return draft

    def get(self, change_id: str, artifact: str) -> ArtifactDraft | None:
        row = self._session.get(ArtifactDraftRow, (change_id, artifact))
        if row is None:
            return None
        return ArtifactDraft(
            change_id=row.change_id,
            artifact=row.artifact,
            content=row.content,
            base_revision=row.base_revision,
            saved_by=row.saved_by,
            updated_at=row.updated_at,
        )

    def delete(self, change_id: str, artifact: str) -> bool:
        """Drop the draft after an explicit save; ``False`` when there was none."""
        row = self._session.get(ArtifactDraftRow, (change_id, artifact))
        if row is None:
            return False
        self._session.delete(row)
        self._session.flush()
        return True

    def list_for_change(self, change_id: str) -> list[ArtifactDraft]:
        rows = self._session.execute(
            select(ArtifactDraftRow)
            .where(ArtifactDraftRow.change_id == change_id)
            .order_by(ArtifactDraftRow.artifact)
        ).scalars()
        return [
            ArtifactDraft(
                change_id=row.change_id,
                artifact=row.artifact,
                content=row.content,
                base_revision=row.base_revision,
                saved_by=row.saved_by,
                updated_at=row.updated_at,
            )
            for row in rows
        ]


class ArtifactViewRepository:
    """«Просмотрено» marks per artifact revision (T080, ADR-034 p.2)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def mark(self, mark: ArtifactViewMark) -> ArtifactViewMark:
        """Record that the operator viewed this revision; a repeat is inert."""
        stmt = (
            pg_insert(ArtifactViewRow)
            .values(
                change_id=mark.change_id,
                artifact=mark.artifact,
                revision=mark.revision,
                viewed_by=mark.viewed_by,
                viewed_at=mark.viewed_at,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    ArtifactViewRow.change_id,
                    ArtifactViewRow.artifact,
                    ArtifactViewRow.revision,
                ]
            )
        )
        self._session.execute(stmt)
        return mark

    def list_for_change(
        self, change_id: str, *, artifact: str | None = None
    ) -> list[ArtifactViewMark]:
        stmt = (
            select(ArtifactViewRow)
            .where(ArtifactViewRow.change_id == change_id)
            .order_by(ArtifactViewRow.viewed_at, ArtifactViewRow.artifact)
        )
        if artifact is not None:
            stmt = stmt.where(ArtifactViewRow.artifact == artifact)
        return [
            ArtifactViewMark(
                change_id=row.change_id,
                artifact=row.artifact,
                revision=row.revision,
                viewed_by=row.viewed_by,
                viewed_at=row.viewed_at,
            )
            for row in self._session.execute(stmt).scalars()
        ]

    def viewed(self, change_id: str, artifact: str, revision: str) -> bool:
        return self._session.get(ArtifactViewRow, (change_id, artifact, revision)) is not None
