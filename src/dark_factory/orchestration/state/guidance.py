"""Assemble the inputs of ``Guidance`` from the state store (T074/T087, ADR-033).

Shared by the API (``GET /changes/{id}/guidance``, ``GET /products/{id}/guidance``)
and the CLI (``factory change status``): both surfaces read the same facts and
call the same pure projection.

The projection itself lives in ``orchestration.guidance`` and is pure; this
module only gathers what it needs for one request — the owning product, the
latest run of the change, the technical continuation of that run's latest
stage result and, since M2, the gate view of the current phase (T087): the
discussion of the phase from the store, the decisions of the change and the
current revision of its artifacts from the repository (when a
``RepositoryPort`` is bound; without one the revision is unknown and every
approval reads as stale — nothing is green on a missing fact).
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from dark_factory.changes.conversations import Comment
from dark_factory.changes.enums import CommentStatus, Phase
from dark_factory.changes.next_action import NextAction
from dark_factory.changes.product import Product
from dark_factory.changes.run import Change, ChangeRun
from dark_factory.orchestration.artifacts import ArtifactService
from dark_factory.orchestration.conversations import anchor_state
from dark_factory.orchestration.guidance import (
    Guidance,
    change_guidance,
    phase_of_run,
    product_guidance,
)
from dark_factory.orchestration.phase_gate import PhaseGate, phase_gate
from dark_factory.orchestration.state.change_store import (
    ChangeRepository,
    DecisionRepository,
    ProductRepository,
)
from dark_factory.orchestration.state.conversation_store import ConversationRepository
from dark_factory.orchestration.state.models import Execution
from dark_factory.orchestration.state.run_store import RunStore

__all__ = [
    "build_change_guidance",
    "build_phase_gate",
    "build_product_guidance",
    "current_revision",
    "detached_comment_ids",
    "latest_run",
]


def latest_run(session: Session, change_id: str) -> ChangeRun | None:
    """The most recently created run of the change, or ``None`` before the first one."""
    run_id = session.execute(
        select(Execution.id)
        .where(Execution.change_id == change_id)
        .order_by(Execution.created_at.desc(), Execution.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    return RunStore(session).load(run_id) if run_id is not None else None


def _waiting_on(session: Session, run: ChangeRun | None) -> NextAction | None:
    if run is None:
        return None
    history = RunStore(session).load_history(run.id)
    return history[-1].next_action if history else None


def current_revision(change: Change, artifacts: ArtifactService | None) -> str | None:
    """Head of the change branch, or ``None`` without a repository or before the branch exists."""
    if artifacts is None:
        return None
    return artifacts.head(change)


def detached_comment_ids(
    change: Change, comments: list[Comment], artifacts: ArtifactService | None
) -> tuple[str, ...]:
    """Ids of the open comments whose anchor no longer resolves at the current head."""
    if artifacts is None:
        return ()
    open_comments = [c for c in comments if c.status is not CommentStatus.CLOSED]
    if not open_comments:
        return ()
    texts = artifacts.read_many(change, sorted({c.anchor.artifact for c in open_comments}))
    return tuple(
        comment.id
        for comment in open_comments
        if anchor_state(comment.anchor, texts.get(comment.anchor.artifact)) == "detached"
    )


def build_phase_gate(
    session: Session,
    change: Change,
    *,
    phase: Phase,
    run: ChangeRun | None,
    artifacts: ArtifactService | None,
) -> PhaseGate:
    """The gate view of ``phase`` from the store and the repository (T087)."""
    repository = ConversationRepository(session)
    comments = repository.list_comments(change.id, phase=phase)
    return phase_gate(
        change_id=change.id,
        phase=phase,
        questions=repository.list_questions(change.id, phase=phase),
        comments=comments,
        rework_orders=repository.list_rework_orders(change.id, phase=phase),
        decisions=DecisionRepository(session).list_for_change(change.id),
        current_revision=current_revision(change, artifacts),
        budget=run.budget if run is not None else None,
        detached_comment_ids=detached_comment_ids(change, comments, artifacts),
        revision_observable=artifacts is not None,
    )


def build_change_guidance(
    session: Session, change: Change, *, artifacts: ArtifactService | None = None
) -> Guidance:
    """Next step of one change from the store: product, latest run, its wait, its gate."""
    product: Product | None = (
        ProductRepository(session).get(change.product_id) if change.product_id else None
    )
    run = latest_run(session, change.id)
    gate: PhaseGate | None = None
    if run is not None:
        phase = phase_of_run(run)
        if phase not in {Phase.INITIATIVE, Phase.DONE}:
            gate = build_phase_gate(session, change, phase=phase, run=run, artifacts=artifacts)
    return change_guidance(
        change,
        product=product,
        run=run,
        waiting_on=_waiting_on(session, run),
        phase_gate=gate,
    )


def build_product_guidance(session: Session, product: Product) -> Guidance:
    """Next step of one product; the count of its changes feeds the ``why``."""
    changes = ChangeRepository(session).list(limit=200, product_id=product.id)
    return product_guidance(product, open_changes=len(changes))
