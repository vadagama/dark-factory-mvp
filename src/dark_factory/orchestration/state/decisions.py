"""Store-backed assembly of the decision cards (T093, ADR-039 p.8-9).

The pure derivation lives in ``orchestration.decisions``; this module gathers
its inputs for one change — the decisions of the change, its rework orders
and the current revision of the architecture phase — so the API
(``GET /changes/{id}/decisions``) and the CLI (``factory change decisions``)
build the very same view (ADR-033 p.3).
"""

from sqlalchemy.orm import Session

from dark_factory.changes.enums import Phase
from dark_factory.changes.run import Change
from dark_factory.orchestration.artifacts import ArtifactService
from dark_factory.orchestration.decisions import DecisionsView, build_decisions_view
from dark_factory.orchestration.state.change_store import DecisionRepository
from dark_factory.orchestration.state.conversation_store import ConversationRepository
from dark_factory.orchestration.state.phases import phase_revisions

__all__ = ["load_decisions_view"]


def load_decisions_view(
    session: Session, change: Change, *, artifacts: ArtifactService | None
) -> DecisionsView:
    """The decision cards of ``change`` from the store and the repository."""
    return build_decisions_view(
        change,
        artifacts=artifacts,
        decisions=DecisionRepository(session).list_for_change(change.id),
        rework_orders=ConversationRepository(session).list_rework_orders(change.id),
        architecture_revision=phase_revisions(change, artifacts).get(Phase.ARCHITECTURE),
    )
