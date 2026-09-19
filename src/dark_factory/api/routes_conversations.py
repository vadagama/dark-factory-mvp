"""Discussion endpoints: questions, comments, rework orders, the phase gate (T086, ADR-034).

The operator's side of the cycle «вопрос → ответ → правка → сводка → согласовать /
на доработку» (plan §6, M2). Every mutation is version-bound where a revision
exists, audited in the request transaction (ADR-009 p.7) and replay-safe by
``Idempotency-Key`` where a repeat could create a second entity. The
operations themselves live in ``orchestration.state.conversation_ops`` and
are shared with the CLI, so the two surfaces cannot disagree (ADR-033 p.3);
this module maps their typed refusals onto HTTP statuses.

Who may do what (contract api.md):

* a question is *stated* by the agent (``changes:write``, any role — the
  specification stage posts them through the service token, and the runner
  stores the ones the agent puts in its structured block); it is *answered*
  by the operator only;
* comments, rework orders and the closing of a comment are operator decisions
  (``changes:write`` + operator role); the agent's «исправлено» (``addressed``)
  is the service role's one write — it never closes;
* a rework order records a version-bound ``rejected`` decision on the phase
  gate in the same transaction, so the history of decisions shows the
  send-back next to the approvals (ADR-009 p.7).
"""

from collections.abc import Callable, Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from sqlalchemy.orm import Session

from dark_factory.api.auth import (
    SCOPE_CHANGES_WRITE,
    ApiToken,
    ApiTokenStore,
    require_write,
)
from dark_factory.api.dto import (
    AnswerRequest,
    CommentCreateRequest,
    CommentNoteRequest,
    CommentView,
    QuestionCreateRequest,
    ReworkOrderRequest,
)
from dark_factory.changes.conversations import Comment, Question, ReworkOrder
from dark_factory.changes.enums import (
    AnchorState,
    CommentStatus,
    Phase,
    QuestionStatus,
    ReworkOrderStatus,
)
from dark_factory.changes.errors import InvalidStatusTransition
from dark_factory.changes.run import Change
from dark_factory.orchestration.artifacts import ArtifactService
from dark_factory.orchestration.conversations import anchor_state
from dark_factory.orchestration.phase_gate import PhaseGate, discussion_phase
from dark_factory.orchestration.state.change_store import (
    APPROVAL_RECORD_ACTION,
    AuditRepository,
    ChangeRepository,
)
from dark_factory.orchestration.state.conversation_ops import (
    ConversationError,
    ConversationInputError,
    ConversationNotFoundError,
    add_comment,
    answer_question,
    derived_id,
    issue_rework_order,
)
from dark_factory.orchestration.state.conversation_store import (
    COMMENT_ADD_ACTION,
    COMMENT_UPDATE_ACTION,
    QUESTION_ANSWER_ACTION,
    QUESTION_ASK_ACTION,
    REWORK_ORDER_ACTION,
    ConversationRepository,
)
from dark_factory.orchestration.state.guidance import build_phase_gate, latest_run
from dark_factory.orchestration.state.phases import current_change_phase

__all__ = ["create_conversations_router"]


def _http_error(error: ConversationError) -> HTTPException:
    """Map a typed refusal of the shared operations onto the API vocabulary."""
    if isinstance(error, ConversationNotFoundError):
        return HTTPException(status_code=404, detail=str(error))
    if isinstance(error, ConversationInputError):
        return HTTPException(status_code=422, detail=str(error))
    return HTTPException(status_code=409, detail=str(error))


def create_conversations_router(
    session_dependency: Callable[..., Iterator[Session]],
    token_store: ApiTokenStore,
    artifacts: ArtifactService | None = None,
) -> APIRouter:
    """Build the discussion routes; ``artifacts`` binds anchors and gates to revisions."""
    SessionDep = Annotated[Session, Depends(session_dependency)]
    changes_write = require_write(token_store, SCOPE_CHANGES_WRITE)
    operator_write = require_write(token_store, SCOPE_CHANGES_WRITE, require_operator_role=True)
    AnyTokenDep = Annotated[ApiToken, Depends(changes_write)]
    OperatorTokenDep = Annotated[ApiToken, Depends(operator_write)]
    IdempotencyKey = Annotated[str | None, Header()]
    router = APIRouter()

    def _change(change_id: str, session: Session) -> Change:
        change = ChangeRepository(session).get(change_id)
        if change is None:
            raise HTTPException(status_code=404, detail=f"Change {change_id!r} does not exist")
        return change

    def _phase(change: Change, session: Session, requested: Phase | None) -> Phase:
        if requested is not None:
            return requested
        run = latest_run(session, change.id)
        return discussion_phase(
            run, current_change_phase(session, change, run=run, artifacts=artifacts)
        )

    def _audit(
        token: ApiToken,
        action: str,
        resource_type: str,
        resource_id: str,
        outcome: str,
        idempotency_key: str | None,
        session: Session,
    ) -> None:
        AuditRepository(session).append(
            actor=token.actor,
            role=token.role,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            idempotency_key=idempotency_key,
            outcome=outcome,
        )

    def _comment_view(change: Change, comment: Comment) -> CommentView:
        state = AnchorState.ATTACHED
        if artifacts is not None and comment.status is not CommentStatus.CLOSED:
            texts = artifacts.read_many(change, [comment.anchor.artifact])
            state = anchor_state(comment.anchor, texts.get(comment.anchor.artifact))
        return CommentView(**comment.model_dump(), anchor_state=state)

    # --- questions -----------------------------------------------------------

    @router.get("/changes/{change_id}/questions", response_model=list[Question])
    def list_questions(
        change_id: str,
        session: SessionDep,
        status: Annotated[QuestionStatus | None, Query()] = None,
        phase: Annotated[Phase | None, Query()] = None,
    ) -> list[Question]:
        _change(change_id, session)
        return ConversationRepository(session).list_questions(change_id, phase=phase, status=status)

    @router.post("/changes/{change_id}/questions", status_code=201, response_model=Question)
    def ask_question(
        token: AnyTokenDep,
        session: SessionDep,
        change_id: str,
        body: QuestionCreateRequest,
        response: Response,
        idempotency_key: IdempotencyKey = None,
    ) -> Question:
        """State one question of the agent (or the operator on its behalf); replay by key."""
        change = _change(change_id, session)
        question = Question(
            id=derived_id("q_", idempotency_key),
            change_id=change.id,
            phase=_phase(change, session, body.phase),
            run_id=body.run_id,
            text=body.text,
            kind=body.kind,
            options=tuple(body.options),
            anchor=body.anchor,
            blocking=body.blocking,
        )
        stored, created = ConversationRepository(session).add_question(question)
        _audit(
            token,
            QUESTION_ASK_ACTION,
            "question",
            stored.id,
            "created" if created else "replayed",
            idempotency_key,
            session,
        )
        if not created:
            response.status_code = 200
        return stored

    @router.post("/changes/{change_id}/questions/{question_id}/answer", response_model=Question)
    def answer(
        token: OperatorTokenDep,
        session: SessionDep,
        change_id: str,
        question_id: str,
        body: AnswerRequest,
        idempotency_key: IdempotencyKey = None,
    ) -> Question:
        """Record the operator's answer (a human decision, ADR-034 p.1); 409 when not open."""
        change = _change(change_id, session)
        try:
            question, created = answer_question(
                session,
                change,
                question_id,
                value=body.value,
                comment=body.comment,
                answered_by=token.actor,
            )
        except ConversationError as error:
            raise _http_error(error) from None
        _audit(
            token,
            QUESTION_ANSWER_ACTION,
            "question",
            question.id,
            "created" if created else "replayed",
            idempotency_key,
            session,
        )
        return question

    # --- comments --------------------------------------------------------------

    @router.get("/changes/{change_id}/comments", response_model=list[CommentView])
    def list_comments(
        change_id: str,
        session: SessionDep,
        status: Annotated[CommentStatus | None, Query()] = None,
        phase: Annotated[Phase | None, Query()] = None,
        artifact: Annotated[str | None, Query(min_length=1)] = None,
    ) -> list[CommentView]:
        change = _change(change_id, session)
        comments = ConversationRepository(session).list_comments(
            change_id, phase=phase, status=status, artifact=artifact
        )
        return [_comment_view(change, comment) for comment in comments]

    @router.post("/changes/{change_id}/comments", status_code=201, response_model=CommentView)
    def create_comment(
        token: OperatorTokenDep,
        session: SessionDep,
        change_id: str,
        body: CommentCreateRequest,
        response: Response,
        idempotency_key: IdempotencyKey = None,
    ) -> CommentView:
        """Leave a remark on a fragment; a comment never starts rework by itself (ADR-034 p.2)."""
        change = _change(change_id, session)
        stored, created = add_comment(
            session,
            change,
            phase=_phase(change, session, body.phase),
            artifact=body.artifact,
            anchor_id=body.anchor_id,
            body=body.body,
            author=token.actor,
            revision=body.revision,
            idempotency_key=idempotency_key,
            artifacts=artifacts,
        )
        _audit(
            token,
            COMMENT_ADD_ACTION,
            "comment",
            stored.id,
            "created" if created else "replayed",
            idempotency_key,
            session,
        )
        if not created:
            response.status_code = 200
        return _comment_view(change, stored)

    def _transition(
        token: ApiToken,
        session: Session,
        change_id: str,
        comment_id: str,
        apply: Callable[[Comment], None],
        idempotency_key: str | None,
    ) -> CommentView:
        change = _change(change_id, session)
        repository = ConversationRepository(session)
        comment = repository.get_comment(comment_id)
        if comment is None or comment.change_id != change_id:
            raise HTTPException(status_code=404, detail=f"Comment {comment_id!r} does not exist")
        try:
            apply(comment)
        except InvalidStatusTransition as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        repository.save_comment(comment)
        _audit(
            token, COMMENT_UPDATE_ACTION, "comment", comment.id, "created", idempotency_key, session
        )
        return _comment_view(change, comment)

    @router.post("/changes/{change_id}/comments/{comment_id}/addressed", response_model=CommentView)
    def address_comment(
        token: AnyTokenDep,
        session: SessionDep,
        change_id: str,
        comment_id: str,
        body: CommentNoteRequest | None = None,
        idempotency_key: IdempotencyKey = None,
    ) -> CommentView:
        """The agent's «исправлено»: ready for re-check, not closed (ADR-034 p.2)."""
        note = body.note if body is not None else None
        return _transition(
            token, session, change_id, comment_id, lambda c: c.mark_addressed(note), idempotency_key
        )

    @router.post("/changes/{change_id}/comments/{comment_id}/close", response_model=CommentView)
    def close_comment(
        token: OperatorTokenDep,
        session: SessionDep,
        change_id: str,
        comment_id: str,
        idempotency_key: IdempotencyKey = None,
    ) -> CommentView:
        """Only the operator closes a remark (ADR-034 p.2)."""
        return _transition(
            token, session, change_id, comment_id, lambda c: c.close(), idempotency_key
        )

    @router.post("/changes/{change_id}/comments/{comment_id}/reopen", response_model=CommentView)
    def reopen_comment(
        token: OperatorTokenDep,
        session: SessionDep,
        change_id: str,
        comment_id: str,
        idempotency_key: IdempotencyKey = None,
    ) -> CommentView:
        """The operator re-checked and disagrees with «исправлено»."""
        return _transition(
            token, session, change_id, comment_id, lambda c: c.reopen(), idempotency_key
        )

    # --- rework orders ---------------------------------------------------------

    @router.get("/changes/{change_id}/rework-orders", response_model=list[ReworkOrder])
    def list_rework_orders(
        change_id: str,
        session: SessionDep,
        phase: Annotated[Phase | None, Query()] = None,
        status: Annotated[ReworkOrderStatus | None, Query()] = None,
    ) -> list[ReworkOrder]:
        _change(change_id, session)
        return ConversationRepository(session).list_rework_orders(
            change_id, phase=phase, status=status
        )

    @router.post("/changes/{change_id}/rework-orders", status_code=201, response_model=ReworkOrder)
    def create_rework_order(
        token: OperatorTokenDep,
        session: SessionDep,
        change_id: str,
        body: ReworkOrderRequest,
        response: Response,
        idempotency_key: IdempotencyKey = None,
    ) -> ReworkOrder:
        """The send-back: the explicit order that spends the next rework round (ADR-034 p.3).

        Refused with 409 while another order of the phase is pending or running
        (one round at a time) and when ``expected_revision`` is behind the head;
        404 for an unknown comment or question; 422 for an empty order. Records
        the version-bound ``rejected`` decision of the phase gate in the same
        transaction.
        """
        change = _change(change_id, session)
        phase = _phase(change, session, body.phase)
        try:
            stored, created = issue_rework_order(
                session,
                change,
                phase=phase,
                comment_ids=body.comment_ids,
                question_ids=body.question_ids,
                instruction=body.instruction,
                issued_by=token.actor,
                actor_role=token.role,
                expected_revision=body.expected_revision,
                idempotency_key=idempotency_key,
                artifacts=artifacts,
            )
        except ConversationError as error:
            raise _http_error(error) from None
        if created:
            _audit(
                token,
                APPROVAL_RECORD_ACTION,
                "change",
                change.id,
                "created",
                idempotency_key,
                session,
            )
        else:
            response.status_code = 200
        _audit(
            token,
            REWORK_ORDER_ACTION,
            "rework_order",
            stored.id,
            "created" if created else "replayed",
            idempotency_key,
            session,
        )
        return stored

    # --- phase gate --------------------------------------------------------------

    @router.get("/changes/{change_id}/phase-gate", response_model=PhaseGate)
    def get_phase_gate(
        change_id: str,
        session: SessionDep,
        phase: Annotated[Phase | None, Query()] = None,
    ) -> PhaseGate:
        """The preconditions of the phase gate (T087): why it is closed and how to open it."""
        change = _change(change_id, session)
        run = latest_run(session, change.id)
        return build_phase_gate(
            session, change, phase=_phase(change, session, phase), run=run, artifacts=artifacts
        )

    return router
