"""Store-backed assembly of the discussion for the driver, the API and the CLI (T080/T081).

The pure semantics live in ``orchestration.conversations``; this module reads
and writes the store around one advance or one request:

* :func:`phase_of_stage` — the ADR-032 projection the discussion binds to;
* :func:`load_conversation_inputs` — the typed inputs of the next attempt of a
  stage (answers not yet applied, open questions, open comments, the pending
  or running rework order);
* :func:`load_rework_orders` — the loop history of a phase for the wait
  resolution;
* :func:`with_store_facts` — folds the store's version-bound decisions and the
  rework orders into the observed provider facts, so an approval recorded
  through the API resolves the wait exactly like a review on the provider;
* :func:`record_stage_outcome` — after an advance, in the same transaction:
  the agent's questions become ``Question`` entities (deterministic ids, so a
  replayed advance cannot duplicate them), answered questions become
  ``resolved`` once a round took them in, the rework order moves
  ``pending → in_progress → done | escalated`` with the agent's summary, and
  the comments the summary claims become ``addressed`` — never ``closed``
  (ADR-034 p.2).
"""

import hashlib
from typing import Final

from sqlalchemy.orm import Session

from dark_factory.changes.conversations import Question, ReworkOrder, ReworkSummary
from dark_factory.changes.enums import (
    CommentStatus,
    DecisionOutcome,
    Phase,
    QuestionStatus,
    ReworkOrderStatus,
    Stage,
    StageStatus,
)
from dark_factory.changes.findings import Decision
from dark_factory.changes.keys import attempt_id as compose_attempt_id
from dark_factory.changes.keys import operation_key as compose_operation_key
from dark_factory.changes.next_action import (
    RequestApprovalAction,
    ReworkAction,
    StopAction,
    WaitForInputAction,
)
from dark_factory.changes.run import Change, ChangeRun, StageResult
from dark_factory.orchestration.artifacts import ArtifactService
from dark_factory.orchestration.conversations import ConversationInputs
from dark_factory.orchestration.phase_gate import SPECIFICATION_PHASES, decision_phase
from dark_factory.orchestration.phases import (
    next_specification_phase,
    specification_phase,
    stage_phase,
)
from dark_factory.orchestration.runner import FactsProvider, RunAdvance, RunAdvanceOutcome
from dark_factory.orchestration.stages.agent import role_of
from dark_factory.orchestration.stages.gates import GateObservation
from dark_factory.orchestration.state.conversation_store import ConversationRepository
from dark_factory.orchestration.state.phases import load_phase_facts
from dark_factory.orchestration.state.run_store import RunStore

__all__ = [
    "load_conversation_inputs",
    "load_rework_orders",
    "phase_of_stage",
    "question_id_for",
    "record_stage_outcome",
    "waiting_phase_of",
    "with_store_facts",
]

QUESTION_ID_PREFIX: Final[str] = "q_"


def phase_of_stage(stage: Stage) -> Phase:
    """The first operator phase of a stage (ADR-032 projection).

    The specification stage has three (ADR-039); callers that know the round
    pass it explicitly — this is the fallback for a stage without rounds and
    for records written before M3.
    """
    return stage_phase(stage)


def question_id_for(result: StageResult, index: int) -> str:
    """Deterministic id of the ``index``-th question of an attempt result.

    Derived from the attempt id, so storing the questions of a replayed
    advance is inert (``add_question`` dedups by id) and two attempts never
    share a question.
    """
    operation = compose_operation_key(result.run_id, result.stage, result.input_revision or "")
    attempt = compose_attempt_id(operation, result.attempt_number)
    digest = hashlib.sha256(f"{attempt}:{index}".encode()).hexdigest()[:16]
    return f"{QUESTION_ID_PREFIX}{digest}"


def waiting_phase_of(session: Session, run: ChangeRun, stage: Stage) -> Phase | None:
    """The phase of the ``waiting`` checkpoint of ``stage``, if the run has one (M3).

    The latest result of the stage in the run history, when it is a waiting
    checkpoint that names its round; ``None`` for a stage without rounds, a
    pre-M3 record or a run whose latest result of the stage is final.
    """
    history = RunStore(session).load_history(run.id)
    for result in reversed(history):
        if result.stage is stage:
            return result.phase if result.status is StageStatus.WAITING else None
    return None


def load_rework_orders(session: Session, change_id: str, phase: Phase) -> list[ReworkOrder]:
    """The rework orders of a phase in issue order — the loop history (T081)."""
    return ConversationRepository(session).list_rework_orders(change_id, phase=phase)


def load_conversation_inputs(
    session: Session, change_id: str, stage: Stage, *, phase: Phase | None = None
) -> ConversationInputs:
    """The discussion the next attempt of ``stage`` must take into account (ADR-034 p.5).

    ``phase`` is the round of the specification stage (M3); without it the
    stage's first phase is read.
    """
    phase = phase if phase is not None else phase_of_stage(stage)
    repository = ConversationRepository(session)
    order = repository.active_rework_order(
        change_id, phase=phase
    ) or repository.pending_rework_order(change_id, phase=phase)
    return ConversationInputs(
        answered_questions=tuple(
            repository.list_questions(change_id, phase=phase, status=QuestionStatus.ANSWERED)
        ),
        open_questions=tuple(
            repository.list_questions(change_id, phase=phase, status=QuestionStatus.OPEN)
        ),
        open_comments=tuple(
            repository.list_comments(
                change_id, phase=phase, status=(CommentStatus.OPEN, CommentStatus.ADDRESSED)
            )
        ),
        rework_order=order,
    )


def with_store_facts(
    gate_facts: FactsProvider | None,
    session: Session,
    change_id: str,
    *,
    artifacts: ArtifactService | None = None,
) -> FactsProvider | None:
    """A facts provider that adds the store's decisions and rework orders to the observation.

    The provider's own observation (head, merge state, reviews) stays the base;
    the decisions recorded through the API (``POST /changes/{id}/approvals``)
    are folded in **only when bound to the current revision** — a decision
    about another revision authorizes nothing here (ADR-009 p.7) — and the
    rework orders of the stage's phase are attached in issue order. Without a
    provider nothing is observed and nothing is added: the wait stays parked.

    On the specification stage (M3, ADR-039) the current revision of a
    decision is the revision of *its phase's* artifacts — read through
    ``artifacts`` — so the architecture round does not stale the requirements
    approval; a ``waived`` decision is about the phase and binds to no
    revision. The observation also names the phase the stage is in and the
    round that follows it, so the resolution knows whether the stage ends or
    re-enters for the next round.
    """
    if gate_facts is None:
        return None

    def observe(run: ChangeRun, stage: Stage, change: Change) -> GateObservation | None:
        observation = gate_facts(run, stage, change)
        if observation is None:
            return None
        facts = load_phase_facts(session, change, artifacts=artifacts)
        phase: Phase | None = None
        next_phase: Phase | None = None
        if stage is Stage.SPECIFICATION:
            # The round the parked attempt belongs to is the one its decision
            # settles: the checkpoint names it (``StageResult.phase``). Only
            # a checkpoint without a phase (pre-M3) falls back to the first
            # unsettled phase — reading the decisions alone here made a
            # requirements approval recorded *after* the round look like the
            # architecture phase's business, and the wait never resolved
            # (found on the M3 live run).
            phase = waiting_phase_of(session, run, stage) or specification_phase(
                run.route, facts.decisions, facts.revisions
            )
            next_phase = next_specification_phase(run.route, phase)

        def current_revision_of(decision: Decision) -> str | None:
            decided = decision_phase(decision)
            if stage is Stage.SPECIFICATION and decided in SPECIFICATION_PHASES:
                known = facts.revisions.get(decided)
                if known is not None or artifacts is not None:
                    return known
            return observation.head_sha

        bound = tuple(
            decision
            for decision in facts.decisions
            if decision.outcome is DecisionOutcome.WAIVED
            or (
                decision.commit_sha is not None
                and decision.commit_sha == current_revision_of(decision)
            )
        )
        orders = load_rework_orders(
            session, change_id, phase if phase is not None else phase_of_stage(stage)
        )
        return GateObservation(
            head_sha=observation.head_sha,
            merged=observation.merged,
            pipeline_status=observation.pipeline_status,
            approvals=(*observation.approvals, *bound),
            rework_orders=tuple(orders),
            phase=phase,
            next_phase=next_phase,
        )

    return observe


def _store_questions(
    repository: ConversationRepository, result: StageResult, change_id: str, phase: Phase
) -> list[Question]:
    stored: list[Question] = []
    for index, draft in enumerate(result.questions, start=1):
        question = Question(
            **draft.model_dump(),
            id=question_id_for(result, index),
            change_id=change_id,
            phase=phase,
            run_id=result.run_id,
            asked_by=role_of(result.stage, result.phase),
        )
        stored.append(repository.add_question(question)[0])
    return stored


def _mark_addressed(
    repository: ConversationRepository, summary: ReworkSummary | None, order: ReworkOrder
) -> None:
    claimed = set(summary.addressed_comment_ids) if summary is not None else set()
    for comment_id in {*claimed, *order.comment_ids}:
        comment = repository.get_comment(comment_id)
        if comment is None or comment.status is not CommentStatus.OPEN:
            continue
        if comment_id in claimed:
            comment.mark_addressed()
            repository.save_comment(comment)


def record_stage_outcome(session: Session, advance: RunAdvance, *, change_id: str) -> None:
    """Persist what one advance did to the discussion, in the caller's transaction.

    Nothing is written for a replay (the committed result already did this).
    A fresh attempt result of a stage:

    * stores the agent's questions (deterministic ids — a repeat is inert);
    * when the attempt ended in its human wait (``request_approval`` /
      ``wait_for_input``): the answered questions of the phase are ``resolved``
      (the round took them in), the running rework order is ``done`` with the
      agent's summary and the comments the summary claims are ``addressed``;
    * when the flow spent a rework round (``rework``): the pending order is
      ``in_progress`` with the round number of the run budget;
    * when the flow stopped ``blocked`` while an order was pending or running:
      the order is ``escalated`` with the stop reason (ADR-018 p.5).
    """
    if advance.outcome is RunAdvanceOutcome.REPLAYED or advance.decision is None:
        return
    result = advance.result
    phase = result.phase if result.phase is not None else phase_of_stage(result.stage)
    repository = ConversationRepository(session)
    action = advance.decision.action
    if isinstance(action, ReworkAction):
        pending = repository.pending_rework_order(change_id, phase=phase)
        if pending is not None:
            pending.start(round=action.round, run_id=result.run_id)
            repository.save_rework_order(pending)
        return
    if isinstance(action, StopAction):
        for order in repository.list_rework_orders(
            change_id,
            phase=phase,
            status=(ReworkOrderStatus.PENDING, ReworkOrderStatus.IN_PROGRESS),
        ):
            order.escalate(action.reason)
            repository.save_rework_order(order)
        return
    _store_questions(repository, result, change_id, phase)
    if isinstance(action, RequestApprovalAction | WaitForInputAction):
        for question in repository.list_questions(
            change_id, phase=phase, status=QuestionStatus.ANSWERED
        ):
            question.apply_status(QuestionStatus.RESOLVED)
            repository.save_question(question)
        active = repository.active_rework_order(change_id, phase=phase)
        if active is not None:
            summary = result.rework_summary or ReworkSummary()
            active.finish(summary)
            repository.save_rework_order(active)
            _mark_addressed(repository, summary, active)
