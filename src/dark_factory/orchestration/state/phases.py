"""Store-backed assembly of the phase projection (T098, ADR-039).

The pure projection lives in ``orchestration.phases``; this module gathers
its inputs for one change — the decisions of the change, the current revision
of every phase's artifacts (through :class:`ArtifactService`, when a
repository is bound), the architect's statement about the UI from
``design/overview.md`` and the gate view of each phase — and hands the same
facts to ``Guidance``, the wait resolution and ``GET /changes/{id}/phases``,
so the surfaces cannot disagree on which phase the change is in.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from dark_factory.changes.enums import Phase
from dark_factory.changes.findings import Decision
from dark_factory.changes.next_action import NextAction
from dark_factory.changes.run import Change, ChangeRun
from dark_factory.context.design import DESIGN_OVERVIEW_PATH, UiRequirement, ui_requirement_of
from dark_factory.orchestration.artifacts import ArtifactService
from dark_factory.orchestration.phase_gate import SPECIFICATION_PHASES, PhaseGate
from dark_factory.orchestration.phases import (
    PhasesProjection,
    current_phase,
    phase_of_path,
    project_phases,
)
from dark_factory.orchestration.state.change_store import DecisionRepository

__all__ = [
    "PhaseFacts",
    "build_phases",
    "current_change_phase",
    "load_phase_facts",
    "phase_revisions",
    "ui_requirement",
]


def phase_revisions(change: Change, artifacts: ArtifactService | None) -> dict[Phase, str | None]:
    """Current revision of each phase's artifacts, from the change branch.

    Without a repository nothing is known (empty mapping — every approval
    reads stale, nothing is green on a missing fact); without a branch every
    phase is ``None`` (no artifacts yet).
    """
    if artifacts is None:
        return {}
    tree = artifacts.tree(change)
    revisions: dict[Phase, str | None] = dict.fromkeys(SPECIFICATION_PHASES)
    revisions[Phase.PLAN] = None
    if tree.revision is None:
        return revisions
    grouped: dict[Phase, list[str]] = {}
    for node in tree.nodes:
        phase = phase_of_path(node.path)
        if phase is not None:
            grouped.setdefault(phase, []).append(node.path)
    for phase, paths in grouped.items():
        revisions[phase] = artifacts.latest_revision(change, paths) or tree.revision
    return revisions


def ui_requirement(change: Change, artifacts: ArtifactService | None) -> UiRequirement | None:
    """The architect's ``ui:`` statement from ``design/overview.md``, if the document exists."""
    if artifacts is None:
        return None
    tree = artifacts.tree(change)
    if tree.revision is None:
        return None
    for node in tree.nodes:
        if node.path.endswith(f"/{DESIGN_OVERVIEW_PATH}"):
            texts = artifacts.read_many(change, [node.path])
            return ui_requirement_of(texts.get(node.path))
    return None


@dataclass(frozen=True)
class PhaseFacts:
    """Everything the phase projection reads from the store and the repository."""

    decisions: tuple[Decision, ...]
    revisions: Mapping[Phase, str | None]
    ui_requirement: UiRequirement | None = None
    gates: Mapping[Phase, PhaseGate] = field(default_factory=dict)


def load_phase_facts(
    session: Session, change: Change, *, artifacts: ArtifactService | None
) -> PhaseFacts:
    """Decisions, phase revisions and the UI statement of one change (no gates)."""
    return PhaseFacts(
        decisions=tuple(DecisionRepository(session).list_for_change(change.id)),
        revisions=phase_revisions(change, artifacts),
        ui_requirement=ui_requirement(change, artifacts),
    )


def current_change_phase(
    session: Session,
    change: Change,
    *,
    run: ChangeRun | None,
    artifacts: ArtifactService | None,
    facts: PhaseFacts | None = None,
) -> Phase:
    """The phase the change is in now (ADR-032, ADR-039) from the store and the repository."""
    if run is None:
        # Before the first run nothing was decided: intake, without touching
        # the decisions or the repository.
        return current_phase(None)
    resolved = (
        facts if facts is not None else load_phase_facts(session, change, artifacts=artifacts)
    )
    return current_phase(run, decisions=resolved.decisions, revisions=resolved.revisions)


def build_phases(
    session: Session,
    change: Change,
    *,
    run: ChangeRun | None,
    artifacts: ArtifactService | None,
    waiting_on: NextAction | None = None,
    waiting_phase: Phase | None = None,
) -> PhasesProjection:
    """``GET /changes/{id}/phases``: the eight phases with their gate views.

    ``waiting_on``/``waiting_phase`` come from the run's latest result
    (``state.guidance.waiting_on`` / ``waiting_phase``): the continuation and
    the round it belongs to.
    """
    # Imported here: ``state.guidance`` imports this module for the current phase.
    from dark_factory.orchestration.state.guidance import build_phase_gate

    facts = load_phase_facts(session, change, artifacts=artifacts)
    gates: dict[Phase, PhaseGate] = {}
    for phase in Phase:
        if phase in {Phase.INITIATIVE, Phase.DONE}:
            continue
        gates[phase] = build_phase_gate(
            session, change, phase=phase, run=run, artifacts=artifacts, facts=facts
        )
    return project_phases(
        change_id=change.id,
        run=run,
        decisions=facts.decisions,
        revisions=facts.revisions,
        gates=gates,
        waiting_on=waiting_on,
        waiting_phase=waiting_phase,
        ui_requirement=facts.ui_requirement,
        brief_complete=change.brief is not None and change.brief.is_complete,
    )
