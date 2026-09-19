"""Artifact endpoints: tree, document, versions, diff, write-through, drafts, views (T082-T086).

Git is the source of truth (ADR-035 p.1): every read goes to the change branch
of the product repository through ``ArtifactService``, and an edit becomes one
commit there (p.3). Without a bound ``RepositoryPort`` the endpoints answer 503
— the factory never shows an artifact it cannot read (the same fail-closed rule
as product validation).

The store keeps only what git must not: the autosave draft of an artifact
(``/artifact-drafts``, p.4) and the «просмотрено» marks (``/artifact-views``,
ADR-034 p.2). A successful edit deletes the path's draft and applies the
staleness rules of the discussion: open questions whose fragment disappeared
turn ``stale``, comments whose anchor is gone are reported ``detached``
(ADR-034 p.1/p.2, ADR-035 p.7); the approvals bound to the old revision read as
stale through the phase gate without any write.
"""

from collections.abc import Callable, Iterator
from typing import Annotated, Final

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from sqlalchemy.orm import Session

from dark_factory.api.auth import (
    SCOPE_CHANGES_WRITE,
    ApiToken,
    ApiTokenStore,
    require_write,
)
from dark_factory.api.dto import (
    ArtifactDocumentView,
    ArtifactDraftRequest,
    ArtifactDraftView,
    ArtifactEditRequest,
    ArtifactTreeView,
    ArtifactViewRequest,
    ArtifactWriteView,
)
from dark_factory.changes.documents import ArtifactDraft, ArtifactViewMark
from dark_factory.changes.enums import CommentStatus, QuestionStatus
from dark_factory.changes.run import Change
from dark_factory.context.artifacts import (
    ArtifactDiff,
    ArtifactRevision,
    ProtectedPropertyError,
    apply_properties,
)
from dark_factory.orchestration.artifacts import (
    ArtifactConflictError,
    ArtifactNotFoundError,
    ArtifactService,
)
from dark_factory.orchestration.conversations import apply_revision
from dark_factory.orchestration.state.change_store import AuditRepository, ChangeRepository
from dark_factory.orchestration.state.conversation_store import (
    ARTIFACT_DRAFT_ACTION,
    ARTIFACT_EDIT_ACTION,
    ARTIFACT_VIEW_ACTION,
    ArtifactDraftRepository,
    ArtifactViewRepository,
    ConversationRepository,
)

__all__ = ["REPOSITORY_UNCONFIGURED_DETAIL", "create_artifacts_router"]

REPOSITORY_UNCONFIGURED_DETAIL: Final[str] = (
    "the product repository is not configured in this contour, so artifacts cannot be"
    " read or written; configure the DARK_FACTORY_GITHUB_* variables"
)


def create_artifacts_router(
    session_dependency: Callable[..., Iterator[Session]],
    token_store: ApiTokenStore,
    artifacts: ArtifactService | None = None,
) -> APIRouter:
    """Build the artifact routes over ``artifacts`` (``None`` → every route answers 503)."""
    SessionDep = Annotated[Session, Depends(session_dependency)]
    operator_write = require_write(token_store, SCOPE_CHANGES_WRITE, require_operator_role=True)
    OperatorTokenDep = Annotated[ApiToken, Depends(operator_write)]
    IdempotencyKey = Annotated[str | None, Header()]
    router = APIRouter()

    def _service() -> ArtifactService:
        if artifacts is None:
            raise HTTPException(status_code=503, detail=REPOSITORY_UNCONFIGURED_DETAIL)
        return artifacts

    def _change(change_id: str, session: Session) -> Change:
        change = ChangeRepository(session).get(change_id)
        if change is None:
            raise HTTPException(status_code=404, detail=f"Change {change_id!r} does not exist")
        return change

    def _audit(
        token: ApiToken,
        action: str,
        resource_id: str,
        outcome: str,
        idempotency_key: str | None,
        session: Session,
    ) -> None:
        AuditRepository(session).append(
            actor=token.actor,
            role=token.role,
            action=action,
            resource_type="artifact",
            resource_id=resource_id,
            idempotency_key=idempotency_key,
            outcome=outcome,
        )

    def _draft_view(draft: ArtifactDraft, head: str | None) -> ArtifactDraftView:
        return ArtifactDraftView(
            **draft.model_dump(),
            stale=draft.base_revision is not None
            and head is not None
            and draft.base_revision != head,
        )

    @router.get("/changes/{change_id}/artifacts", response_model=ArtifactTreeView)
    def get_tree(change_id: str, session: SessionDep) -> ArtifactTreeView:
        """The ChangeSet artifacts on the change branch; ``revision`` null = no branch yet."""
        change = _change(change_id, session)
        tree = _service().tree(change)
        drafts = ArtifactDraftRepository(session).list_for_change(change_id)
        return ArtifactTreeView(**tree.model_dump(), drafts=[draft.artifact for draft in drafts])

    @router.get("/changes/{change_id}/artifacts/{path:path}", response_model=ArtifactDocumentView)
    def get_document(
        change_id: str,
        path: str,
        session: SessionDep,
        revision: Annotated[str | None, Query(min_length=1)] = None,
    ) -> ArtifactDocumentView:
        """One document at ``revision`` (default: the head) with its draft and view state."""
        change = _change(change_id, session)
        service = _service()
        try:
            document = service.get(change, path, revision=revision)
        except ArtifactNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from None
        head = document.revision if revision is None else service.head(change)
        draft = ArtifactDraftRepository(session).get(change_id, path)
        conversation = ConversationRepository(session)
        return ArtifactDocumentView(
            **document.model_dump(),
            draft=_draft_view(draft, head) if draft is not None else None,
            viewed=ArtifactViewRepository(session).viewed(change_id, path, document.revision),
            open_comments=len(
                conversation.list_comments(
                    change_id, artifact=path, status=(CommentStatus.OPEN, CommentStatus.ADDRESSED)
                )
            ),
            open_questions=len(
                conversation.list_questions(change_id, artifact=path, status=QuestionStatus.OPEN)
            ),
        )

    @router.put("/changes/{change_id}/artifacts/{path:path}", response_model=ArtifactWriteView)
    def edit_document(
        token: OperatorTokenDep,
        session: SessionDep,
        change_id: str,
        path: str,
        body: ArtifactEditRequest,
        idempotency_key: IdempotencyKey = None,
    ) -> ArtifactWriteView:
        """Write-through: the edit becomes one commit on the change branch (ADR-035 p.3).

        409 when the file changed after ``base_revision``; 422 when ``properties``
        touch a protected key. The path's draft is dropped and the discussion's
        staleness rules are applied in the same transaction.
        """
        change = _change(change_id, session)
        service = _service()
        content = body.content
        if body.properties:
            try:
                content = apply_properties(content, body.properties)
            except ProtectedPropertyError as error:
                raise HTTPException(status_code=422, detail=str(error)) from None
        try:
            written = service.write(
                change,
                path,
                content,
                base_revision=body.base_revision,
                actor=token.actor,
                idempotency_key=idempotency_key,
                message=body.message,
            )
        except ArtifactConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        ArtifactDraftRepository(session).delete(change_id, path)
        conversation = ConversationRepository(session)
        questions = conversation.list_questions(change_id, artifact=path)
        comments = conversation.list_comments(change_id, artifact=path)
        effects = apply_revision(questions, comments, {path: content})
        for question in effects.stale_questions:
            conversation.save_question(question)
        _audit(
            token,
            ARTIFACT_EDIT_ACTION,
            f"{change_id}:{path}",
            "created" if written.revision != written.previous_revision else "replayed",
            idempotency_key,
            session,
        )
        return ArtifactWriteView(
            path=path,
            revision=written.revision,
            previous_revision=written.previous_revision,
            created_commit=written.revision != written.previous_revision,
            stale_questions=[question.id for question in effects.stale_questions],
            detached_comments=[comment.id for comment in effects.detached_comments],
        )

    @router.get(
        "/changes/{change_id}/artifact-versions/{path:path}", response_model=list[ArtifactRevision]
    )
    def list_versions(change_id: str, path: str, session: SessionDep) -> list[ArtifactRevision]:
        """Revisions of one document, newest first (revision = commit, ADR-035 p.1)."""
        change = _change(change_id, session)
        return _service().versions(change, path)

    @router.get("/changes/{change_id}/artifact-diff/{path:path}", response_model=ArtifactDiff)
    def get_diff(
        change_id: str,
        path: str,
        session: SessionDep,
        from_revision: Annotated[str, Query(min_length=1)],
        to_revision: Annotated[str, Query(min_length=1)],
    ) -> ArtifactDiff:
        """Unified diff of one document between two revisions."""
        change = _change(change_id, session)
        try:
            return _service().diff(
                change, path, from_revision=from_revision, to_revision=to_revision
            )
        except ArtifactNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from None

    # --- drafts (ADR-035 p.4) -------------------------------------------------------

    @router.get(
        "/changes/{change_id}/artifact-drafts/{path:path}", response_model=ArtifactDraftView
    )
    def get_draft(change_id: str, path: str, session: SessionDep) -> ArtifactDraftView:
        change = _change(change_id, session)
        draft = ArtifactDraftRepository(session).get(change_id, path)
        if draft is None:
            raise HTTPException(status_code=404, detail=f"no draft of {path!r}")
        head = artifacts.head(change) if artifacts is not None else None
        return _draft_view(draft, head)

    @router.put(
        "/changes/{change_id}/artifact-drafts/{path:path}", response_model=ArtifactDraftView
    )
    def put_draft(
        token: OperatorTokenDep,
        session: SessionDep,
        change_id: str,
        path: str,
        body: ArtifactDraftRequest,
        idempotency_key: IdempotencyKey = None,
    ) -> ArtifactDraftView:
        """Autosave: the unsaved state of one artifact, outside git (ADR-035 p.4)."""
        change = _change(change_id, session)
        draft = ArtifactDraftRepository(session).put(
            ArtifactDraft(
                change_id=change_id,
                artifact=path,
                content=body.content,
                base_revision=body.base_revision,
                saved_by=token.actor,
            )
        )
        _audit(
            token, ARTIFACT_DRAFT_ACTION, f"{change_id}:{path}", "created", idempotency_key, session
        )
        head = artifacts.head(change) if artifacts is not None else None
        return _draft_view(draft, head)

    @router.delete("/changes/{change_id}/artifact-drafts/{path:path}", status_code=204)
    def delete_draft(
        token: OperatorTokenDep,
        session: SessionDep,
        change_id: str,
        path: str,
        idempotency_key: IdempotencyKey = None,
    ) -> Response:
        """Discard the draft; the source of truth is untouched (ADR-035 p.4)."""
        _change(change_id, session)
        deleted = ArtifactDraftRepository(session).delete(change_id, path)
        _audit(
            token,
            ARTIFACT_DRAFT_ACTION,
            f"{change_id}:{path}",
            "deleted" if deleted else "replayed",
            idempotency_key,
            session,
        )
        return Response(status_code=204)

    # --- views (ADR-034 p.2: viewed is not approved) ------------------------------------

    @router.post(
        "/changes/{change_id}/artifact-views/{path:path}", response_model=ArtifactDocumentView
    )
    def mark_viewed(
        token: OperatorTokenDep,
        session: SessionDep,
        change_id: str,
        path: str,
        body: ArtifactViewRequest,
        idempotency_key: IdempotencyKey = None,
    ) -> ArtifactDocumentView:
        """Record the operator's view of one revision; it never counts as an approval."""
        _change(change_id, session)
        ArtifactViewRepository(session).mark(
            ArtifactViewMark(
                change_id=change_id, artifact=path, revision=body.revision, viewed_by=token.actor
            )
        )
        _audit(
            token, ARTIFACT_VIEW_ACTION, f"{change_id}:{path}", "created", idempotency_key, session
        )
        return get_document(change_id, path, session, revision=body.revision)

    return router
