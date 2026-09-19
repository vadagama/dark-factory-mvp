"""Architecture and interface read models and «Запросить альтернативу» (M3, T093/T094).

* ``GET /changes/{id}/decisions`` — the ADR cards with their derived status
  (``orchestration.decisions``);
* ``POST /changes/{id}/decisions/{decision_id}/alternative`` — the operator's
  request for another option: a rework order of the architecture phase that
  names the decision (``decision_ids``), created through the shared
  ``issue_rework_order`` so the CLI and the API agree (ADR-033 p.3) and the
  version-bound ``rejected`` decision of the phase is recorded next to it
  (ADR-009 p.7); 404 for an unknown decision, 409 while another order of the
  phase is open, 422 for an empty instruction, replay by ``Idempotency-Key``;
* ``GET /changes/{id}/ui`` — the UI spec (``orchestration.ui_spec``).

Every read goes to git through ``ArtifactService`` (ADR-035 p.1); without a
bound repository the routes answer 503 with the same detail as the artifact
routes — the factory never shows a card it cannot read.
"""

from collections.abc import Callable, Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from sqlalchemy.orm import Session

from dark_factory.api.auth import SCOPE_CHANGES_WRITE, ApiToken, ApiTokenStore, require_write
from dark_factory.api.dto import DecisionAlternativeRequest
from dark_factory.api.routes_artifacts import REPOSITORY_UNCONFIGURED_DETAIL
from dark_factory.changes.conversations import ReworkOrder
from dark_factory.changes.enums import Phase
from dark_factory.changes.run import Change
from dark_factory.orchestration.artifacts import ArtifactService
from dark_factory.orchestration.decisions import DecisionsView
from dark_factory.orchestration.state.change_store import (
    APPROVAL_RECORD_ACTION,
    AuditRepository,
    ChangeRepository,
)
from dark_factory.orchestration.state.conversation_ops import (
    ConversationError,
    ConversationInputError,
    ConversationNotFoundError,
    issue_rework_order,
)
from dark_factory.orchestration.state.conversation_store import (
    REWORK_ORDER_ACTION,
)
from dark_factory.orchestration.state.decisions import load_decisions_view
from dark_factory.orchestration.ui_spec import UiSpecView, build_ui_spec_view

__all__ = ["create_decisions_router", "load_decisions_view"]


def _http_error(error: ConversationError) -> HTTPException:
    if isinstance(error, ConversationNotFoundError):
        return HTTPException(status_code=404, detail=str(error))
    if isinstance(error, ConversationInputError):
        return HTTPException(status_code=422, detail=str(error))
    return HTTPException(status_code=409, detail=str(error))


def create_decisions_router(
    session_dependency: Callable[..., Iterator[Session]],
    token_store: ApiTokenStore,
    artifacts: ArtifactService | None = None,
) -> APIRouter:
    """Build the decision and UI routes over ``artifacts`` (``None`` → 503 on every route)."""
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

    @router.get("/changes/{change_id}/decisions", response_model=DecisionsView)
    def get_decisions(change_id: str, session: SessionDep) -> DecisionsView:
        """The ADR cards of the change with their derived status (T093)."""
        change = _change(change_id, session)
        return load_decisions_view(session, change, artifacts=_service())

    @router.post(
        "/changes/{change_id}/decisions/{decision_id}/alternative",
        status_code=201,
        response_model=ReworkOrder,
    )
    def request_alternative(
        token: OperatorTokenDep,
        session: SessionDep,
        change_id: str,
        decision_id: str,
        body: DecisionAlternativeRequest,
        response: Response,
        idempotency_key: IdempotencyKey = None,
    ) -> ReworkOrder:
        """«Запросить альтернативу»: a rework order of the architecture phase about one ADR."""
        change = _change(change_id, session)
        service = _service()
        view = load_decisions_view(session, change, artifacts=service)
        if all(card.id != decision_id for card in view.decisions):
            raise HTTPException(
                status_code=404,
                detail=f"decision {decision_id!r} is not among the ADRs of the change",
            )
        try:
            stored, created = issue_rework_order(
                session,
                change,
                phase=Phase.ARCHITECTURE,
                comment_ids=body.comment_ids,
                question_ids=(),
                instruction=body.instruction,
                issued_by=token.actor,
                actor_role=token.role,
                idempotency_key=idempotency_key,
                artifacts=service,
                decision_ids=[decision_id],
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

    @router.get("/changes/{change_id}/ui", response_model=UiSpecView)
    def get_ui(change_id: str, session: SessionDep) -> UiSpecView:
        """The UI spec of the change: scenarios, screens, links, components (T094)."""
        change = _change(change_id, session)
        return build_ui_spec_view(change, artifacts=_service())

    return router
