"""Preconditions of a phase gate: what must be true before the operator may approve (T087).

ADR-032 p.4 makes every phase declare the preconditions of its gate; ADR-033
p.4 forbids dead ends — an unavailable gate explains itself and names the
action that unblocks it; ADR-034/ADR-035 bind approvals to revisions and make
the earlier ones *stale* after an edit. This module computes that view as a
pure read model, :class:`PhaseGate`, from the discussion facts of the phase,
the decisions of the change and the current revision of its artifacts:

* the gate is **unavailable** while an open *blocking* question waits for the
  operator, while a rework round is pending or running, and while the phase
  has no artifact revision yet; each reason carries the action that removes
  it (``factory change answer`` / ``factory run advance`` / ...);
* open comments do **not** block by themselves (the operator decides whether a
  remark is worth a round — ADR-034 p.2), but they are counted so the decision
  panel shows them;
* every recorded approval of the gate is reported with its revision state
  (``current`` / ``stale`` / ``unbound``, ADR-035 p.7); ``approved`` is true
  only for a *current* approval, never for a view mark and never for a stale
  one;
* a phase may be **skipped** with a stated reason (``waived`` + comment,
  ADR-032 p.5) — the view says so, the API enforces the reason.

``POST /changes/{id}/approvals`` consults the same function before recording
an ``approved`` decision, and ``Guidance`` renders it, so the CLI, the API and
the Console cannot disagree on why a gate is closed.
"""

from collections.abc import Sequence
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from dark_factory.changes.conversations import Comment, Question, ReworkOrder
from dark_factory.changes.enums import (
    CommentStatus,
    DecisionOutcome,
    Gate,
    Phase,
    QuestionStatus,
    ReworkOrderStatus,
)
from dark_factory.changes.findings import Decision
from dark_factory.changes.usage import BudgetSnapshot
from dark_factory.orchestration.conversations import RevisionState, revision_state

__all__ = [
    "GATE_PHASE",
    "PHASE_GATE",
    "ApprovalView",
    "GateReason",
    "PhaseGate",
    "discussion_phase",
    "gate_of_phase",
    "phase_gate",
    "phase_of_gate",
]


def discussion_phase(run: object | None, phase: Phase) -> Phase:
    """The phase a discussion binds to by default: never the intake (F0).

    Questions, comments and rework orders belong to a gated phase; before the
    first run the requirements phase is the one being prepared, so the
    default of a change without a run is ``requirements``, not ``initiative``.
    """
    if run is None and phase in {Phase.INITIATIVE, Phase.DONE}:
        return Phase.REQUIREMENTS
    return phase


PHASE_GATE: Final[dict[Phase, Gate | None]] = {
    Phase.INITIATIVE: None,
    Phase.REQUIREMENTS: Gate.SPECIFICATION,
    Phase.ARCHITECTURE: Gate.SPECIFICATION,
    Phase.INTERFACE: Gate.UI,
    Phase.PLAN: Gate.PLANNING,
    Phase.EXECUTION: Gate.CODE,
    Phase.DEMONSTRATION: Gate.VERIFICATION,
    Phase.DELIVERY: Gate.RELEASE,
    Phase.DONE: None,
}
"""The gate a phase approval records against (ADR-032 table; ADR-023 control points).

Architecture shares the ``specification`` gate with requirements in M2: the
``solution`` control point splits it in M3 (T098), the table is total so a
lookup never fails.
"""


def gate_of_phase(phase: Phase) -> Gate | None:
    """The gate of ``phase``, or ``None`` for a phase without an approval (F0, done)."""
    return PHASE_GATE[phase]


GATE_PHASE: Final[dict[Gate, Phase]] = {
    Gate.SPECIFICATION: Phase.REQUIREMENTS,
    Gate.UI: Phase.INTERFACE,
    Gate.PLANNING: Phase.PLAN,
    Gate.CODE: Phase.EXECUTION,
    Gate.REVIEW: Phase.DEMONSTRATION,
    Gate.VERIFICATION: Phase.DEMONSTRATION,
    Gate.RELEASE: Phase.DELIVERY,
}
"""The phase an approval of a gate belongs to (the inverse of ``PHASE_GATE``, total over Gate)."""


def phase_of_gate(gate: Gate) -> Phase:
    """The phase whose preconditions guard an approval of ``gate``."""
    return GATE_PHASE[gate]


class GateReason(BaseModel):
    """Why the gate is unavailable, and the action that removes the reason (ADR-033 p.4)."""

    model_config = ConfigDict(frozen=True)

    what: str
    how: str


class ApprovalView(BaseModel):
    """One recorded decision of the gate with its state against the current revision."""

    model_config = ConfigDict(frozen=True)

    decision_id: str
    outcome: DecisionOutcome
    revision: str | None
    state: RevisionState
    comment: str | None = None


class PhaseGate(BaseModel):
    """The gate of one phase as the operator must see it (T087)."""

    model_config = ConfigDict(frozen=True)

    change_id: str
    phase: Phase
    gate: Gate | None
    available: bool
    reasons: tuple[GateReason, ...] = ()
    current_revision: str | None = None
    approved: bool = False
    """A *current* approval of the gate exists (ADR-009 p.7); stale ones do not count."""
    skippable: bool = True
    """The phase may be waived with a stated reason (ADR-032 p.5)."""
    approvals: tuple[ApprovalView, ...] = ()
    blocking_questions: int = 0
    open_questions: int = 0
    answered_questions: int = 0
    open_comments: int = 0
    addressed_comments: int = 0
    detached_comments: int = 0
    rework_pending: bool = False
    rework_in_progress: bool = False
    rework_rounds_used: int = 0
    rework_rounds_max: int = Field(default=3, ge=0)

    @property
    def rework_exhausted(self) -> bool:
        return self.rework_rounds_used >= self.rework_rounds_max


def phase_gate(
    *,
    change_id: str,
    phase: Phase,
    questions: Sequence[Question] = (),
    comments: Sequence[Comment] = (),
    rework_orders: Sequence[ReworkOrder] = (),
    decisions: Sequence[Decision] = (),
    current_revision: str | None,
    budget: BudgetSnapshot | None = None,
    detached_comment_ids: Sequence[str] = (),
    revision_observable: bool = True,
) -> PhaseGate:
    """Compute the gate view of ``phase`` from the facts of the change.

    ``questions``/``comments``/``rework_orders`` are the discussion of the
    phase, ``decisions`` every decision of the change (filtered by the phase's
    gate here), ``current_revision`` the head of the change branch (``None``
    before any artifact exists) and ``budget`` the run budget that owns the
    rework counter. ``detached_comment_ids`` are the open comments whose anchor
    no longer resolves (computed by the caller against the current text).
    ``revision_observable`` is ``False`` when no repository is bound: the
    revision is then *unknown* rather than *absent*, so the gate does not claim
    the artifacts are missing and a decision binds to the revision the operator
    names — approvals still read ``stale`` against an unknown head (nothing is
    green on a missing fact, ADR-035 p.7).
    """
    gate = gate_of_phase(phase)
    blocking = [q for q in questions if q.blocks_gate]
    open_questions = [q for q in questions if q.status is QuestionStatus.OPEN]
    answered = [q for q in questions if q.status is QuestionStatus.ANSWERED]
    open_comments = [c for c in comments if c.status is CommentStatus.OPEN]
    addressed = [c for c in comments if c.status is CommentStatus.ADDRESSED]
    pending = any(o.status is ReworkOrderStatus.PENDING for o in rework_orders)
    in_progress = any(o.status is ReworkOrderStatus.IN_PROGRESS for o in rework_orders)
    used = budget.used_rework_rounds if budget is not None else 0
    maximum = budget.max_rework_rounds if budget is not None else 3

    approvals = tuple(
        ApprovalView(
            decision_id=decision.id,
            outcome=decision.outcome,
            revision=decision.commit_sha,
            state=revision_state(decision.commit_sha, current_revision),
            comment=decision.comment,
        )
        for decision in decisions
        if gate is not None and decision.gate is gate
    )
    approved = any(
        view.outcome is DecisionOutcome.APPROVED and view.state == "current" for view in approvals
    )

    reasons: list[GateReason] = []
    if gate is None:
        reasons.append(
            GateReason(
                what=f"Фаза «{phase.value}» не имеет гейта согласования",
                how="Перейдите к следующей фазе; решение здесь не требуется.",
            )
        )
    if current_revision is None and revision_observable:
        reasons.append(
            GateReason(
                what="Артефактов фазы ещё нет: агент не создал ревизию",
                how=f"Запустите фазу: factory run advance --change-id {change_id}.",
            )
        )
    if blocking:
        ids = ", ".join(q.id for q in blocking[:3]) + (" …" if len(blocking) > 3 else "")
        reasons.append(
            GateReason(
                what=f"Блокирующих вопросов без ответа: {len(blocking)} ({ids})",
                how=f"Ответьте: factory change answer --id {change_id} --question <id> --value …"
                f" или POST /changes/{change_id}/questions/{{id}}/answer.",
            )
        )
    if pending:
        reasons.append(
            GateReason(
                what="Отправлено на доработку: раунд ещё не запущен",
                how=f"Запустите раунд: factory run advance --change-id {change_id}.",
            )
        )
    elif in_progress:
        reasons.append(
            GateReason(
                what=f"Идёт доработка (раунд {used} из {maximum})",
                how="Дождитесь результата раунда; затем проверьте сводку агента.",
            )
        )
    return PhaseGate(
        change_id=change_id,
        phase=phase,
        gate=gate,
        available=not reasons,
        reasons=tuple(reasons),
        current_revision=current_revision,
        approved=approved,
        skippable=gate is not None,
        approvals=approvals,
        blocking_questions=len(blocking),
        open_questions=len(open_questions),
        answered_questions=len(answered),
        open_comments=len(open_comments),
        addressed_comments=len(addressed),
        detached_comments=len(set(detached_comment_ids)),
        rework_pending=pending,
        rework_in_progress=in_progress,
        rework_rounds_used=used,
        rework_rounds_max=maximum,
    )
