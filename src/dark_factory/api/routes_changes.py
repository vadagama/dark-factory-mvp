"""Change intake, card, trace and approval endpoints (T035, contract api.md, ADR-009 p.7).

Mutating endpoints require a bearer token with the matching scope; approvals
additionally require the operator role (contract api.md: agents never
approve). The state change and its audit row are written in one transaction
(the per-request session commits on success), and ``Idempotency-Key`` replays
never produce a second effect (FR-017).

M1 additions (T071/T072/T074): the intake carries a brief, a scenario and a
spend limit; ``POST /briefs/formulate`` turns free text into a brief through
the ``brief_formulator`` seam (an honest ``draft`` without a harness);
``PUT /changes/{id}/brief`` replaces the brief of a change; ``GET
/changes/{id}/guidance`` serves the operator's next step (ADR-033), computed by
the core so the CLI shows the same block; ``GET /changes?product_id=`` narrows
the list to one product.
"""

import asyncio
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from dark_factory.api.aggregates import build_run_trace
from dark_factory.api.auth import (
    SCOPE_APPROVALS_WRITE,
    SCOPE_CHANGES_WRITE,
    ApiToken,
    ApiTokenStore,
    require_write,
)
from dark_factory.api.dto import (
    ApprovalRequest,
    BriefFormulateRequest,
    ChangeCard,
    ChangeRunRef,
    ChangeTrace,
)
from dark_factory.changes.enums import DecisionOutcome, DecisionSource
from dark_factory.changes.findings import Decision
from dark_factory.changes.intake import IntakeBrief
from dark_factory.changes.run import Change
from dark_factory.orchestration.guidance import Guidance
from dark_factory.orchestration.intake import BriefFormulator
from dark_factory.orchestration.state.change_store import (
    APPROVAL_RECORD_ACTION,
    CHANGE_BRIEF_ACTION,
    CHANGE_INTAKE_ACTION,
    AuditRepository,
    ChangeRepository,
    DecisionRepository,
)
from dark_factory.orchestration.state.guidance import build_change_guidance
from dark_factory.orchestration.state.models import Change as ChangeRow
from dark_factory.orchestration.state.models import Decision as DecisionRow
from dark_factory.orchestration.state.models import Execution


def create_changes_router(
    session_dependency: Callable[..., Iterator[Session]],
    token_store: ApiTokenStore,
    brief_formulator: BriefFormulator | None = None,
) -> APIRouter:
    """Build the ``/changes`` router with its auth dependencies.

    ``brief_formulator`` is the harness-backed «Помоги сформулировать» seam
    (T072); ``None`` (no ``DARK_FACTORY_LLM_*``) keeps the endpoint honest —
    it answers a ``draft`` brief whose ``error`` says the harness is absent.
    """
    SessionDep = Annotated[Session, Depends(session_dependency)]
    changes_write = require_write(token_store, SCOPE_CHANGES_WRITE)
    approvals_write = require_write(token_store, SCOPE_APPROVALS_WRITE, require_operator_role=True)
    IntakeTokenDep = Annotated[ApiToken, Depends(changes_write)]
    ApprovalTokenDep = Annotated[ApiToken, Depends(approvals_write)]
    IdempotencyKey = Annotated[str | None, Header()]
    router = APIRouter()

    def _require_change_row(change_id: str, session: Session) -> ChangeRow:
        row = ChangeRepository(session).get_raw(change_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"Change {change_id!r} does not exist")
        return row

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
            resource_type="change",
            resource_id=resource_id,
            idempotency_key=idempotency_key,
            outcome=outcome,
        )

    @router.post("/changes", status_code=201, response_model=Change)
    def create_change(
        token: IntakeTokenDep,
        session: SessionDep,
        body: Change,
        response: Response,
        idempotency_key: IdempotencyKey = None,
    ) -> Change:
        """Intake of one change (FR-001): create, or replay an already-known one (FR-017)."""
        repository = ChangeRepository(session)
        existing = repository.get(body.id)
        if existing is not None:
            _audit(token, CHANGE_INTAKE_ACTION, existing.id, "replayed", idempotency_key, session)
            response.status_code = 200
            return existing
        if body.external_ref is not None:
            same_ref = repository.find_by_external_ref(body.external_ref)
            if same_ref is not None:
                _audit(
                    token, CHANGE_INTAKE_ACTION, same_ref.id, "replayed", idempotency_key, session
                )
                response.status_code = 200
                return same_ref
        change, _ = repository.create(body)
        _audit(token, CHANGE_INTAKE_ACTION, change.id, "created", idempotency_key, session)
        return change

    @router.get("/changes", response_model=list[Change])
    def list_changes(
        session: SessionDep,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
        product_id: Annotated[str | None, Query(min_length=1)] = None,
    ) -> list[Change]:
        return ChangeRepository(session).list(limit=limit, offset=offset, product_id=product_id)

    @router.post("/briefs/formulate", response_model=IntakeBrief)
    def formulate_brief(token: IntakeTokenDep, body: BriefFormulateRequest) -> IntakeBrief:
        """Free text → structured brief through the agent (T072); stateless.

        The result is always a brief: a ``draft`` with an observable ``error``
        when the harness is not configured, refuses or answers something that
        is not a brief. The operator's text travels back in ``source_text`` so
        the created change keeps it.
        """
        if brief_formulator is None:
            return IntakeBrief(
                source_text=body.source_text,
                error="the agent harness is not configured in this contour"
                " (DARK_FACTORY_LLM_*): fill the brief by hand",
            )
        return asyncio.run(brief_formulator.formulate(body.source_text))

    @router.put("/changes/{change_id}/brief", response_model=Change)
    def update_brief(
        token: IntakeTokenDep,
        session: SessionDep,
        change_id: str,
        body: IntakeBrief,
        idempotency_key: IdempotencyKey = None,
    ) -> Change:
        """Replace the brief of a change (T071/T072); the status is derived from the fields."""
        repository = ChangeRepository(session)
        if repository.get(change_id) is None:
            raise HTTPException(status_code=404, detail=f"Change {change_id!r} does not exist")
        change = repository.update_brief(change_id, body)
        _audit(token, CHANGE_BRIEF_ACTION, change.id, "created", idempotency_key, session)
        return change

    @router.get("/changes/{change_id}/guidance", response_model=Guidance)
    def get_change_guidance(change_id: str, session: SessionDep) -> Guidance:
        """The operator's next step for the change (T074, ADR-033) — a read model."""
        change = ChangeRepository(session).get(change_id)
        if change is None:
            raise HTTPException(status_code=404, detail=f"Change {change_id!r} does not exist")
        return build_change_guidance(session, change)

    @router.get("/changes/{change_id}", response_model=ChangeCard)
    def get_change(change_id: str, session: SessionDep) -> ChangeCard:
        change = ChangeRepository(session).get(change_id)
        if change is None:
            raise HTTPException(status_code=404, detail=f"Change {change_id!r} does not exist")
        run_rows = session.execute(
            select(Execution.id, Execution.status)
            .where(Execution.change_id == change_id)
            .order_by(Execution.created_at, Execution.id)
        ).all()
        decisions_count = session.execute(
            select(func.count()).select_from(DecisionRow).where(DecisionRow.change_id == change_id)
        ).scalar_one()
        return ChangeCard(
            **change.model_dump(),
            runs=[ChangeRunRef(run_id=row[0], status=row[1]) for row in run_rows],
            decisions_count=int(decisions_count),
        )

    @router.get("/changes/{change_id}/trace", response_model=ChangeTrace)
    def get_change_trace(change_id: str, session: SessionDep) -> ChangeTrace:
        _require_change_row(change_id, session)
        runs = session.execute(
            select(Execution)
            .where(Execution.change_id == change_id)
            .order_by(Execution.created_at, Execution.id)
        ).scalars()
        return ChangeTrace(
            change_id=change_id,
            runs=[build_run_trace(session, run) for run in runs],
        )

    @router.get("/changes/{change_id}/approvals", response_model=list[Decision])
    def list_approvals(change_id: str, session: SessionDep) -> list[Decision]:
        _require_change_row(change_id, session)
        return DecisionRepository(session).list_for_change(change_id)

    @router.post("/changes/{change_id}/approvals", status_code=201, response_model=Decision)
    def record_approval(
        token: ApprovalTokenDep,
        session: SessionDep,
        change_id: str,
        body: ApprovalRequest,
        response: Response,
        idempotency_key: IdempotencyKey = None,
    ) -> Decision:
        """Record one version-bound operator decision (ADR-009 p.7, FR-003)."""
        change_row = _require_change_row(change_id, session)
        decisions = DecisionRepository(session)
        if idempotency_key is not None:
            replayed = decisions.find_by_idempotency_key(idempotency_key)
            if replayed is not None:
                _audit(
                    token, APPROVAL_RECORD_ACTION, change_id, "replayed", idempotency_key, session
                )
                response.status_code = 200
                return replayed
        if (
            body.expected_state_revision is not None
            and body.expected_state_revision != change_row.state_revision
        ):
            raise HTTPException(status_code=409, detail="state_revision mismatch")
        decision = Decision(
            id=f"dec_{uuid4().hex}",
            gate=body.gate,
            outcome=DecisionOutcome(body.outcome),
            decided_by=DecisionSource.HUMAN,
            decided_at=datetime.now(UTC),
            commit_sha=body.subject_revision,
            comment=body.comment,
        )
        stored, _ = decisions.record(
            decision,
            change_id=change_id,
            idempotency_key=idempotency_key,
            actor_role=token.role,
        )
        change_row.state_revision += 1
        _audit(token, APPROVAL_RECORD_ACTION, change_id, "created", idempotency_key, session)
        return stored

    return router
