"""The eight operator phases of a ChangeSet as one read model (T098, ADR-032, ADR-039).

``Phase`` is a projection over the executable ``Stage`` (ADR-032 p.1-2): it
influences no transition and cannot allow a forbidden one. This module is
where the projection is *computed*, in two parts:

* :func:`specification_phase` — the phase the ``specification`` stage is in.
  The stage runs requirements → architecture → interface as rounds of one
  stage (ADR-039); the current phase is the first one without a settled
  decision — an *approved* decision bound to the current revision of the
  phase's own artifacts, or a *waived* one (a waiver is about the phase, not a
  document, so it never goes stale). An edit of a phase's artifacts after its
  approval makes that approval stale and the phase current again — the DoD of
  T099 («правка после согласования помечает согласование неактуальным»).
* :func:`project_phases` — the left column of the ChangeSet workspace
  (ADR-037 p.4): every phase F0-F7 with its state, revision, counts and
  iteration, plus the current phase. Served as ``GET /changes/{id}/phases``
  and printed by ``factory change phases``; the Console renders it instead of
  deriving a state of its own.

Pure and deterministic. The store-backed assembly — phase revisions from the
repository, discussion counts, decisions — is ``orchestration.state.phases``.
"""

from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from dark_factory.changes.enums import (
    DecisionOutcome,
    Gate,
    Phase,
    Route,
    RunStatus,
    Stage,
    StageStatus,
)
from dark_factory.changes.findings import Decision
from dark_factory.changes.next_action import (
    NextAction,
    RequestApprovalAction,
    WaitForInputAction,
)
from dark_factory.changes.run import ChangeRun
from dark_factory.context.artifacts import ArtifactKind, classify_path
from dark_factory.context.design import UiRequirement
from dark_factory.orchestration.conversations import revision_state
from dark_factory.orchestration.phase_gate import (
    PhaseGate,
    gate_of_phase,
    phase_decisions,
    specification_phases,
)

__all__ = [
    "PHASE_LABEL",
    "PHASE_ORDER",
    "PHASE_STAGE",
    "PhaseSettlement",
    "PhaseState",
    "PhaseView",
    "PhasesProjection",
    "current_phase",
    "next_specification_phase",
    "phase_of_path",
    "phase_settlement",
    "project_phases",
    "specification_phase",
    "stage_phase",
]

PHASE_ORDER: Final[tuple[Phase, ...]] = (
    Phase.INITIATIVE,
    Phase.REQUIREMENTS,
    Phase.ARCHITECTURE,
    Phase.INTERFACE,
    Phase.PLAN,
    Phase.EXECUTION,
    Phase.DEMONSTRATION,
    Phase.DELIVERY,
)
"""F0..F7 in navigation order (ADR-032 table); ``done`` is a state, not a column."""

PHASE_STAGE: Final[dict[Phase, Stage | None]] = {
    Phase.INITIATIVE: None,
    Phase.REQUIREMENTS: Stage.SPECIFICATION,
    Phase.ARCHITECTURE: Stage.SPECIFICATION,
    Phase.INTERFACE: Stage.SPECIFICATION,
    Phase.PLAN: Stage.PLANNING,
    Phase.EXECUTION: Stage.CONSTRUCTION,
    Phase.DEMONSTRATION: Stage.REVIEW_VERIFICATION,
    Phase.DELIVERY: Stage.RELEASE,
    Phase.DONE: None,
}
"""The executable stage behind each phase (ADR-032 p.1)."""

PHASE_LABEL: Final[dict[Phase, str]] = {
    Phase.INITIATIVE: "Инициатива",
    Phase.REQUIREMENTS: "Требования",
    Phase.ARCHITECTURE: "Архитектура",
    Phase.INTERFACE: "Интерфейс",
    Phase.PLAN: "План",
    Phase.EXECUTION: "Исполнение",
    Phase.DEMONSTRATION: "Демонстрация и проверка",
    Phase.DELIVERY: "Доставка",
    Phase.DONE: "Завершено",
}

_ARTIFACT_PHASE: Final[dict[ArtifactKind, Phase | None]] = {
    ArtifactKind.SPEC: Phase.REQUIREMENTS,
    ArtifactKind.DESIGN: Phase.ARCHITECTURE,
    ArtifactKind.ADR: Phase.ARCHITECTURE,
    ArtifactKind.UI: Phase.INTERFACE,
    ArtifactKind.PLAN: Phase.PLAN,
    ArtifactKind.OTHER: None,
}

_ACTIVE_STAGE_STATUSES: Final[frozenset[StageStatus]] = frozenset(
    {StageStatus.PENDING, StageStatus.IN_PROGRESS, StageStatus.WAITING, StageStatus.BLOCKED}
)


def stage_phase(stage: Stage) -> Phase:
    """The first phase of a stage — the M1/M2 projection (``guidance.STAGE_PHASE``)."""
    for phase in PHASE_ORDER:
        if PHASE_STAGE[phase] is stage:
            return phase
    raise KeyError(stage)  # pragma: no cover - PHASE_STAGE covers every Stage


def phase_of_path(path: str) -> Phase | None:
    """The phase whose artifacts a ChangeSet path belongs to, or ``None`` for ``other``."""
    return _ARTIFACT_PHASE[classify_path(path)]


class PhaseSettlement(StrEnum):
    """How a phase's gate stands against the current revision of its artifacts."""

    OPEN = "open"
    """No current decision: the phase is not done (never decided, or its approval went stale)."""
    APPROVED = "approved"
    WAIVED = "waived"


def phase_settlement(
    decisions: Sequence[Decision], phase: Phase, revision: str | None
) -> tuple[PhaseSettlement, Decision | None]:
    """The settling decision of ``phase`` at ``revision``, if any (ADR-009 p.7, ADR-035 p.7).

    The newest decision wins: an approval is *current* only when bound to the
    phase revision; a waiver settles regardless of revision; a rejection (the
    send-back) settles nothing.
    """
    for decision in reversed(phase_decisions(decisions, phase)):
        if decision.outcome is DecisionOutcome.WAIVED:
            return PhaseSettlement.WAIVED, decision
        if (
            decision.outcome is DecisionOutcome.APPROVED
            and revision_state(decision.commit_sha, revision) == "current"
        ):
            return PhaseSettlement.APPROVED, decision
    return PhaseSettlement.OPEN, None


def specification_phase(
    route: Route,
    decisions: Sequence[Decision],
    revisions: Mapping[Phase, str | None],
) -> Phase:
    """The phase the specification stage is in: the first one not settled (ADR-039).

    ``revisions`` maps each phase to the current revision of its artifacts
    (``None`` when none exist yet). With every phase settled the last required
    phase is returned — the stage is complete and the flow leaves it.
    """
    phases = specification_phases(route)
    for phase in phases:
        state, _ = phase_settlement(decisions, phase, revisions.get(phase))
        if state is PhaseSettlement.OPEN:
            return phase
    return phases[-1]


def next_specification_phase(route: Route, phase: Phase) -> Phase | None:
    """The round that follows ``phase`` on ``route``, or ``None`` when it is the last one."""
    phases = specification_phases(route)
    if phase not in phases:
        return None
    index = phases.index(phase)
    return phases[index + 1] if index + 1 < len(phases) else None


def current_phase(
    run: ChangeRun | None,
    *,
    decisions: Sequence[Decision] = (),
    revisions: Mapping[Phase, str | None] | None = None,
) -> Phase:
    """The ADR-032 phase of a change: intake before the run, ``done`` after it.

    For an active ``specification`` stage the phase is :func:`specification_phase`
    over the decisions and the phase revisions; every other stage maps to its
    single phase.
    """
    if run is None:
        return Phase.INITIATIVE
    if run.status is RunStatus.SUCCEEDED:
        return Phase.DONE
    stage: Stage | None = None
    for stage_run in run.stages:
        if stage_run.status in _ACTIVE_STAGE_STATUSES:
            stage = stage_run.stage
            break
    if stage is None and run.stages:
        stage = run.stages[-1].stage
    if stage is None:
        return Phase.INITIATIVE
    if stage is Stage.SPECIFICATION:
        return specification_phase(run.route, decisions, revisions or {})
    return stage_phase(stage)


class PhaseState(StrEnum):
    """State of one phase in the left column (ADR-037 p.4/p.6: states, never a percentage)."""

    PENDING = "pending"
    ACTIVE = "active"
    NEEDS_DECISION = "needs_decision"
    APPROVED = "approved"
    WAIVED = "waived"
    NOT_REQUIRED = "not_required"
    STALE = "stale"
    DONE = "done"
    BLOCKED = "blocked"


class PhaseView(BaseModel):
    """One phase as the operator navigates it (T098)."""

    model_config = ConfigDict(frozen=True)

    phase: Phase
    index: int = Field(ge=0, le=7)
    label: str
    stage: Stage | None
    gate: Gate | None
    state: PhaseState
    state_reason: str | None = None
    revision: str | None = None
    """Current revision of the phase's artifacts (the last commit that touched them)."""
    approved_revision: str | None = None
    """Revision the settling approval binds to, when the phase is approved."""
    open_questions: int = 0
    blocking_questions: int = 0
    open_comments: int = 0
    iteration: int = 0
    """Rework rounds spent in the phase (orders in progress, done or escalated)."""


class PhasesProjection(BaseModel):
    """``GET /changes/{id}/phases``: the eight phases and the current one."""

    model_config = ConfigDict(frozen=True)

    change_id: str
    current: Phase
    phases: tuple[PhaseView, ...]


def _stage_state(run: ChangeRun, stage: Stage, waiting_on: NextAction | None) -> PhaseState:
    """State of a single-phase stage from its stage runs (plan, execution, …)."""
    runs = [item for item in run.stages if item.stage is stage]
    if not runs:
        return PhaseState.PENDING
    latest = runs[-1]
    match latest.status:
        case StageStatus.SUCCEEDED:
            return PhaseState.DONE
        case StageStatus.BLOCKED:
            return PhaseState.BLOCKED
        case StageStatus.WAITING:
            if isinstance(waiting_on, RequestApprovalAction | WaitForInputAction):
                return PhaseState.NEEDS_DECISION
            return PhaseState.ACTIVE
        case StageStatus.SKIPPED:
            return PhaseState.NOT_REQUIRED
        case _:
            return PhaseState.ACTIVE


def _specification_state(
    *,
    phase: Phase,
    current: Phase,
    run: ChangeRun,
    settlement: PhaseSettlement,
    settled_by: Decision | None,
    gate: PhaseGate | None,
    waiting_on: NextAction | None,
    ui_requirement: UiRequirement | None,
    waiting_phase: Phase | None = None,
) -> tuple[PhaseState, str | None]:
    """State and its human-readable reason for a phase of the specification stage.

    ``waiting_phase`` is the round the run's waiting checkpoint belongs to
    (M3): when it is an earlier, already settled phase, the current phase is
    not waiting for a decision — its round has not started, the advance
    starts it.
    """
    if phase not in specification_phases(run.route):
        return PhaseState.NOT_REQUIRED, "Маршрут задачи не требует UI: фаза пропущена политикой"
    if settlement is PhaseSettlement.APPROVED and settled_by is not None:
        return PhaseState.APPROVED, f"Согласовано на ревизии {settled_by.commit_sha}"
    if settlement is PhaseSettlement.WAIVED and settled_by is not None:
        return PhaseState.WAIVED, settled_by.comment or "Пропущено с основанием"
    stale = gate is not None and any(view.state == "stale" for view in gate.approvals)
    if phase is not current:
        order = specification_phases(run.route)
        # ``current`` past the specification stage (plan, execution, ...) means
        # every unsettled design phase is behind us: its approval went stale
        # after an edit (found on the M3 live run: an ADR edited after the
        # stage completed crashed the projection on ``order.index``).
        behind = current not in order or order.index(phase) < order.index(current)
        if behind or stale:
            return PhaseState.STALE, "Согласование неактуально: артефакты фазы изменились"
        return PhaseState.PENDING, None
    if gate is not None and (gate.rework_pending or gate.rework_in_progress):
        return PhaseState.ACTIVE, "Идёт доработка по поручению оператора"
    if run.status is RunStatus.BLOCKED:
        return PhaseState.BLOCKED, "Запуск заблокирован: смотрите причину в состоянии"
    if run.status is RunStatus.WAITING and isinstance(
        waiting_on, RequestApprovalAction | WaitForInputAction
    ):
        if waiting_phase is not None and waiting_phase is not phase:
            return (
                PhaseState.ACTIVE,
                f"Фаза «{PHASE_LABEL[waiting_phase]}» согласована: запустите раунд"
                " (factory run advance)",
            )
        if phase is Phase.INTERFACE and ui_requirement is not None and not ui_requirement.required:
            return (
                PhaseState.NEEDS_DECISION,
                f"UI не требуется по предложению агента: {ui_requirement.reason or '—'};"
                " подтвердите пропуск",
            )
        if stale:
            return PhaseState.STALE, "Согласование неактуально: артефакты фазы изменились"
        return PhaseState.NEEDS_DECISION, "Готово к решению оператора"
    return PhaseState.ACTIVE, "Агент готовит результат фазы"


def project_phases(
    *,
    change_id: str,
    run: ChangeRun | None,
    decisions: Sequence[Decision] = (),
    revisions: Mapping[Phase, str | None] | None = None,
    gates: Mapping[Phase, PhaseGate] | None = None,
    waiting_on: NextAction | None = None,
    ui_requirement: UiRequirement | None = None,
    brief_complete: bool = False,
    waiting_phase: Phase | None = None,
) -> PhasesProjection:
    """The left column of the workspace (ADR-037 p.4): eight phases and the current one.

    ``revisions`` are the current revisions of each phase's artifacts,
    ``gates`` the precondition views the store computed (counts, rework
    state, stale approvals) and ``waiting_on`` the technical continuation of
    the latest stage result — the same inputs ``Guidance`` reads, so the two
    projections cannot disagree. ``ui_requirement`` is the architect's
    statement about the UI (T097); ``waiting_phase`` the round of the waiting
    checkpoint (M3) — a settled earlier round leaves the current phase
    *active*, waiting for its advance, not for a decision.
    """
    revisions = revisions or {}
    gates = gates or {}
    current = current_phase(run, decisions=decisions, revisions=revisions)
    views: list[PhaseView] = []
    for index, phase in enumerate(PHASE_ORDER):
        stage = PHASE_STAGE[phase]
        gate_view = gates.get(phase)
        settlement, settled_by = phase_settlement(decisions, phase, revisions.get(phase))
        reason: str | None = None
        if phase is Phase.INITIATIVE:
            if run is not None:
                state = PhaseState.DONE
            elif brief_complete:
                state, reason = PhaseState.NEEDS_DECISION, "Бриф готов: запустите требования"
            else:
                state, reason = PhaseState.ACTIVE, "Заполните бриф задачи"
        elif run is None:
            state = PhaseState.PENDING
        elif current is Phase.DONE:
            state = PhaseState.DONE
            if settlement is PhaseSettlement.WAIVED:
                state = PhaseState.WAIVED
        elif stage is Stage.SPECIFICATION:
            state, reason = _specification_state(
                phase=phase,
                current=current,
                run=run,
                settlement=settlement,
                settled_by=settled_by,
                gate=gate_view,
                waiting_on=waiting_on,
                ui_requirement=ui_requirement,
                waiting_phase=waiting_phase,
            )
        elif stage is not None:
            state = _stage_state(run, stage, waiting_on)
            if settlement is PhaseSettlement.APPROVED and settled_by is not None:
                state, reason = (
                    PhaseState.APPROVED,
                    f"Согласовано на ревизии {settled_by.commit_sha}",
                )
        else:  # pragma: no cover - every non-initiative phase has a stage
            state = PhaseState.PENDING
        views.append(
            PhaseView(
                phase=phase,
                index=index,
                label=PHASE_LABEL[phase],
                stage=stage,
                gate=gate_of_phase(phase),
                state=state,
                state_reason=reason,
                revision=revisions.get(phase),
                approved_revision=(
                    settled_by.commit_sha
                    if settlement is PhaseSettlement.APPROVED and settled_by is not None
                    else None
                ),
                open_questions=gate_view.open_questions if gate_view is not None else 0,
                blocking_questions=gate_view.blocking_questions if gate_view is not None else 0,
                open_comments=gate_view.open_comments if gate_view is not None else 0,
                iteration=_iteration(gate_view),
            )
        )
    return PhasesProjection(change_id=change_id, current=current, phases=tuple(views))


def _iteration(gate: PhaseGate | None) -> int:
    """Rework rounds spent in *this* phase: its orders that reached a round.

    The run budget (``rework_rounds_used``) counts rounds across phases — on
    the M3 live run every phase showed the same «iter=2» after two rounds of
    the architecture phase; the phase's own orders are the honest count.
    """
    if gate is None:
        return 0
    return gate.rework_orders_spent
