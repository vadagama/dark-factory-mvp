"""Decision cards of the architecture phase over git and the store (M3, T093, ADR-039).

``GET /changes/{id}/decisions`` and ``factory change decisions`` show every
ADR of the change as a card whose *status is derived*, never written back to
the document (the agent writes only ``proposed``):

* ``superseded`` — the document says so (``status: superseded``);
* ``needs_revision`` — an open (pending / in-progress) rework order names the
  decision in ``decision_ids`` («Запросить альтернативу», T093), or the
  architecture approval went stale and *this* ADR changed after the revision
  it was approved at (ADR-035 p.7: an edit after the approval marks it stale);
* ``accepted`` — the architecture phase is currently approved (a current
  ``approved`` decision bound to the phase revision) and no open order names
  the decision;
* ``proposed`` — otherwise.

``pending_alternative`` is the open order that asked for another option;
``affected_artifacts`` lists the design/ADR/UI/spec artifacts that changed
since the last *done* order about the decision — what the operator re-checks
after the agent's revision. Every revision fact comes from
:class:`ArtifactService` (git is the source of truth, ADR-035 p.1); without a
bound repository the view is empty and says why — nothing is shown that the
factory cannot read.
"""

from collections.abc import Sequence
from typing import Final

from pydantic import BaseModel, ConfigDict

from dark_factory.changes.conversations import ReworkOrder
from dark_factory.changes.enums import DecisionOutcome, Phase, ReworkOrderStatus
from dark_factory.changes.findings import Decision
from dark_factory.changes.run import Change
from dark_factory.context.artifacts import ArtifactKind
from dark_factory.context.decisions import DecisionCard, DecisionStatus, parse_decision_card
from dark_factory.orchestration.artifacts import ArtifactNotFoundError, ArtifactService
from dark_factory.orchestration.phase_gate import phase_decisions
from dark_factory.orchestration.phases import PhaseSettlement, phase_of_path, phase_settlement

__all__ = ["REPOSITORY_NOT_BOUND", "DecisionsView", "build_decisions_view"]

REPOSITORY_NOT_BOUND: Final[str] = (
    "the product repository is not bound in this contour, so the decisions cannot be read"
)

_OPEN: Final[frozenset[ReworkOrderStatus]] = frozenset(
    {ReworkOrderStatus.PENDING, ReworkOrderStatus.IN_PROGRESS}
)
_AFFECTED_KINDS: Final[frozenset[ArtifactKind]] = frozenset(
    {ArtifactKind.DESIGN, ArtifactKind.ADR, ArtifactKind.UI, ArtifactKind.SPEC}
)


class DecisionsView(BaseModel):
    """``GET /changes/{id}/decisions``: the cards, the phase revision and whether it is approved."""

    model_config = ConfigDict(frozen=True)

    change_id: str
    revision: str | None = None
    """Current revision of the architecture phase's artifacts."""
    approved: bool = False
    """The architecture phase has a *current* approval (stale ones do not count)."""
    decisions: tuple[DecisionCard, ...] = ()
    errors: tuple[str, ...] = ()


def _changed_between(
    artifacts: ArtifactService, change: Change, path: str, from_revision: str, to_revision: str
) -> bool:
    """Whether ``path`` differs between two revisions (an absent side counts as a change)."""
    if from_revision == to_revision:
        return False
    try:
        return not artifacts.diff(
            change, path, from_revision=from_revision, to_revision=to_revision
        ).is_empty
    except ArtifactNotFoundError:
        return False


def _derive_status(
    card: DecisionCard,
    *,
    open_order: ReworkOrder | None,
    settlement: PhaseSettlement,
    stale_approval: Decision | None,
    changed_since_approval: bool,
) -> DecisionStatus:
    if (card.document_status or "").strip().lower() == "superseded":
        return "superseded"
    if open_order is not None:
        return "needs_revision"
    if stale_approval is not None and changed_since_approval:
        return "needs_revision"
    if settlement is PhaseSettlement.APPROVED:
        return "accepted"
    return "proposed"


def build_decisions_view(
    change: Change,
    *,
    artifacts: ArtifactService | None,
    decisions: Sequence[Decision],
    rework_orders: Sequence[ReworkOrder],
    architecture_revision: str | None = None,
) -> DecisionsView:
    """The decision cards of ``change`` with their derived status (contract m3 §2).

    ``architecture_revision`` is the current revision of the architecture
    phase's artifacts as ``state.phases.phase_revisions`` computes it; when
    the caller has not computed it, it is derived here from the design and
    ADR nodes of the tree.
    """
    if artifacts is None:
        return DecisionsView(change_id=change.id, errors=(REPOSITORY_NOT_BOUND,))
    tree = artifacts.tree(change)
    if tree.revision is None:
        return DecisionsView(change_id=change.id)
    if architecture_revision is None:
        phase_paths = [n.path for n in tree.nodes if phase_of_path(n.path) is Phase.ARCHITECTURE]
        architecture_revision = artifacts.latest_revision(change, phase_paths) or tree.revision

    adr_paths = [node.path for node in tree.nodes if node.kind is ArtifactKind.ADR]
    texts = artifacts.read_many(change, adr_paths)
    latest: dict[str, str] = {}

    def latest_of(path: str) -> str:
        if path not in latest:
            versions = artifacts.versions(change, path)
            latest[path] = versions[0].revision if versions else str(tree.revision)
        return latest[path]

    settlement, _settled_by = phase_settlement(decisions, Phase.ARCHITECTURE, architecture_revision)
    approvals = [
        d
        for d in phase_decisions(decisions, Phase.ARCHITECTURE)
        if d.outcome is DecisionOutcome.APPROVED
    ]
    stale_approval: Decision | None = None
    if approvals and settlement is PhaseSettlement.OPEN:
        stale_approval = approvals[-1]

    errors: list[str] = []
    cards: list[DecisionCard] = []
    for path in adr_paths:
        content = texts.get(path)
        if content is None:
            errors.append(f"{path}: the document is absent at revision {tree.revision}")
            continue
        parsed = parse_decision_card(path, content, revision=latest_of(path))
        errors.extend(f"{path}: {error}" for error in parsed.errors)
        about = [o for o in rework_orders if parsed.id in o.decision_ids]
        open_order = next((o for o in about if o.status in _OPEN), None)
        done = [o for o in about if o.status is ReworkOrderStatus.DONE]
        changed_since_approval = (
            stale_approval is not None
            and stale_approval.commit_sha is not None
            and _changed_between(
                artifacts, change, path, stale_approval.commit_sha, latest_of(path)
            )
        )
        affected: list[str] = []
        if done:
            recorded = done[-1].revisions
            for node in tree.nodes:
                if node.kind not in _AFFECTED_KINDS:
                    continue
                before = recorded.get(node.path)
                if before is None or _changed_between(
                    artifacts, change, node.path, before, latest_of(node.path)
                ):
                    affected.append(node.path)
        cards.append(
            parsed.model_copy(
                update={
                    "status": _derive_status(
                        parsed,
                        open_order=open_order,
                        settlement=settlement,
                        stale_approval=stale_approval,
                        changed_since_approval=changed_since_approval,
                    ),
                    "pending_alternative": open_order,
                    "affected_artifacts": tuple(affected),
                }
            )
        )
    return DecisionsView(
        change_id=change.id,
        revision=architecture_revision,
        approved=settlement is PhaseSettlement.APPROVED,
        decisions=tuple(cards),
        errors=tuple(errors),
    )
