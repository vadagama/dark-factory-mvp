"""``factory change`` commands: intake with a brief, and the next step (T073, plan §4/§6).

``change create`` is the CLI side of intake (T071): the change is registered
for a *product* (its repository comes from the registry, ADR-030 p.2), with a
structured brief, a scenario (``specs_only``/``full``) and a spend limit in
USD. A repeat with the same ``--id`` replays the stored change (FR-017). The
brief comes from flags (``--problem``, ``--goal``, ``--constraint``,
``--out-of-scope``) or from a JSON document (``--brief-json``, the same shape
``POST /briefs/formulate`` answers, so an agent-formulated brief can be passed
through as is); a brief without problem or goal is stored as a ``draft`` and
the next step says what to complete.

``change status`` prints the change, its latest run and the next step. Both
commands end with the ``Guidance`` block computed by the core
(``orchestration.state.guidance``), the very object ``GET
/changes/{id}/guidance`` serves — CLI and Console show one next step by
construction (ADR-033 p.3), and the block is repeated after every command
(plan §4, rule 5).

Exit codes (contract cli.md): ``0`` — created, replayed or shown; ``2`` —
invalid input (a bad limit, an unreadable brief file, an unknown product or
change) or an unreachable store. Nothing here echoes a store URL or a raw
exception (ADR-009).
"""

import json
import sys
import uuid
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING, Final

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from dark_factory.changes.conversations import Comment, Question, ReworkOrder
from dark_factory.changes.enums import (
    BriefAuthor,
    ChangeSource,
    DecisionOutcome,
    Phase,
    QuestionStatus,
)
from dark_factory.changes.intake import IntakeBrief, SpendLimit
from dark_factory.changes.run import Change, ChangeRun
from dark_factory.cli._common import (
    CLI_ACTOR,
    StateStoreUnreachableError,
    open_state_store,
    os_error_reason,
    report_execution_error,
    report_invalid_input,
    report_state_store_unreachable,
)
from dark_factory.cli.guidance import render_guidance_text
from dark_factory.cli.main import (
    EXIT_OK,
    ArtifactAction,
    ChangeAlternativeArgs,
    ChangeAnswerArgs,
    ChangeApproveArgs,
    ChangeArtifactsArgs,
    ChangeCommentArgs,
    ChangeCreateArgs,
    ChangeDecisionsArgs,
    ChangePhasesArgs,
    ChangeReworkArgs,
    ChangeStatusArgs,
    ChangeUiArgs,
    UiSection,
)
from dark_factory.orchestration.artifacts import (
    ArtifactConflictError,
    ArtifactNotFoundError,
    ArtifactService,
)
from dark_factory.orchestration.conversations import apply_revision
from dark_factory.orchestration.decisions import DecisionsView
from dark_factory.orchestration.guidance import Guidance
from dark_factory.orchestration.phase_gate import PhaseGate, discussion_phase
from dark_factory.orchestration.phases import PhasesProjection
from dark_factory.orchestration.state.change_store import (
    APPROVAL_RECORD_ACTION,
    CHANGE_INTAKE_ACTION,
    AuditRepository,
    ChangeRepository,
    ProductRepository,
)
from dark_factory.orchestration.state.conversation_ops import (
    ConversationError,
    add_comment,
    answer_question,
    issue_rework_order,
    record_phase_decision,
)
from dark_factory.orchestration.state.conversation_store import (
    ARTIFACT_EDIT_ACTION,
    COMMENT_ADD_ACTION,
    QUESTION_ANSWER_ACTION,
    REWORK_ORDER_ACTION,
    ArtifactDraftRepository,
    ConversationRepository,
)
from dark_factory.orchestration.state.engine import session_scope
from dark_factory.orchestration.state.guidance import (
    build_change_guidance,
    build_phase_gate,
    latest_run,
    waiting_on,
    waiting_phase,
)
from dark_factory.orchestration.state.phases import build_phases, current_change_phase
from dark_factory.orchestration.ui_spec import UiSpecView, build_ui_spec_view

if TYPE_CHECKING:
    from dark_factory.ports import RepositoryPort

__all__ = [
    "REPOSITORY_UNCONFIGURED",
    "brief_from_args",
    "new_change_id",
    "render_change_text",
    "render_decisions_text",
    "render_discussion_text",
    "render_gate_text",
    "render_ui_text",
    "run_change_alternative_command",
    "run_change_answer_command",
    "run_change_approve_command",
    "run_change_artifacts_command",
    "run_change_comment_command",
    "run_change_create_command",
    "run_change_decisions_command",
    "run_change_rework_command",
    "run_change_status_command",
    "run_change_ui_command",
    "spend_limit_from_args",
]

CHANGE_ID_PREFIX: Final[str] = "chg_"

REPOSITORY_UNCONFIGURED: Final[str] = (
    "the product repository is not configured in this contour (DARK_FACTORY_GITHUB_*), so"
    " artifacts cannot be read or written"
)


class IntakeInputError(ValueError):
    """Invalid intake input: reported as exit 2 with the message, nothing runs."""


def new_change_id() -> str:
    """A fresh change id in the API convention (``chg_<12 hex chars>``)."""
    return f"{CHANGE_ID_PREFIX}{uuid.uuid4().hex[:12]}"


def spend_limit_from_args(limit_usd: str, token_limit: int | None) -> SpendLimit:
    """Parse the operator's limit; a non-number or a non-positive amount is invalid input."""
    try:
        amount = Decimal(limit_usd.strip())
    except (InvalidOperation, ValueError) as error:
        raise IntakeInputError("--limit-usd must be a decimal amount, e.g. 25 or 12.50") from error
    try:
        return SpendLimit(cost_budget_usd=amount, token_budget=token_limit)
    except ValidationError as error:
        raise IntakeInputError(
            "--limit-usd must be positive with at most 4 decimals; --token-limit >= 1"
        ) from error


def brief_from_args(args: ChangeCreateArgs) -> IntakeBrief:
    """The brief from ``--brief-json`` or from the field flags (operator-authored)."""
    if args.brief_json is not None:
        if args.problem or args.goal or args.constraints or args.out_of_scope:
            raise IntakeInputError("--brief-json cannot be combined with the brief field flags")
        return _read_brief_json(args.brief_json)
    return IntakeBrief(
        problem=args.problem,
        goal=args.goal,
        constraints=args.constraints,
        out_of_scope=args.out_of_scope,
        formulated_by=BriefAuthor.OPERATOR,
    )


def _read_brief_json(source: str) -> IntakeBrief:
    try:
        raw = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
    except OSError as error:
        raise IntakeInputError(f"cannot read the brief: {os_error_reason(error)}") from error
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        raise IntakeInputError("the brief is not valid JSON") from error
    if not isinstance(data, dict):
        raise IntakeInputError("the brief must be a JSON object")
    data.setdefault("formulated_by", BriefAuthor.OPERATOR.value)
    try:
        return IntakeBrief.model_validate(data)
    except ValidationError as error:
        raise IntakeInputError("the brief does not match the IntakeBrief schema") from error


# --- commands ------------------------------------------------------------------------


def run_change_create_command(
    args: ChangeCreateArgs, *, session_factory: sessionmaker[Session] | None = None
) -> int:
    """Handle ``factory change create``; return the process exit code (T073)."""
    try:
        brief = brief_from_args(args)
        limit = spend_limit_from_args(args.limit_usd, args.token_limit)
    except IntakeInputError as error:
        return report_invalid_input("change create", str(error), json_output=args.json_output)
    return _with_store(
        "change create",
        args.json_output,
        session_factory,
        lambda factory: _create(factory, args, brief, limit),
    )


def run_change_status_command(
    args: ChangeStatusArgs,
    *,
    session_factory: sessionmaker[Session] | None = None,
    repository: "RepositoryPort | None" = None,
) -> int:
    """Handle ``factory change status``; return the process exit code (T073, T086).

    Since M2 the report also lists the discussion of the current phase — open
    and answered questions, open comments, the rework orders — and the gate
    view (T087); ``repository`` binds them to the current revision.
    """
    return _with_store(
        "change status",
        args.json_output,
        session_factory,
        lambda factory: _status(factory, args, _artifacts_of(repository)),
    )


def run_change_phases_command(
    args: ChangePhasesArgs,
    *,
    session_factory: sessionmaker[Session] | None = None,
    repository: "RepositoryPort | None" = None,
) -> int:
    """Handle ``factory change phases`` (T098, ADR-039): the eight phases and the current one.

    The same projection the API serves as ``GET /changes/{id}/phases`` and the
    Console renders in its left column; ``repository`` binds the phases to the
    revisions of their artifacts.
    """
    return _with_store(
        "change phases",
        args.json_output,
        session_factory,
        lambda factory: _phases(factory, args, _artifacts_of(repository)),
    )


def _with_store(
    command: str,
    json_output: bool,
    session_factory: sessionmaker[Session] | None,
    action: Callable[[sessionmaker[Session]], int],
) -> int:
    if session_factory is not None:
        return action(session_factory)
    try:
        with open_state_store() as factory:
            return action(factory)
    except StateStoreUnreachableError:
        return report_state_store_unreachable(command, json_output=json_output)


def _create(
    factory: sessionmaker[Session], args: ChangeCreateArgs, brief: IntakeBrief, limit: SpendLimit
) -> int:
    change_id = args.change_id or new_change_id()
    try:
        with session_scope(factory) as session:
            product = ProductRepository(session).get(args.product_id)
            if product is None:
                return report_invalid_input(
                    "change create",
                    f"unknown product {args.product_id!r}",
                    json_output=args.json_output,
                )
            try:
                change = Change(
                    id=change_id,
                    title=args.title,
                    description=args.description,
                    source=ChangeSource.CLI,
                    product=product.repository,
                    product_id=product.id,
                    risk_class=args.risk_class,
                    brief=brief,
                    scenario=args.scenario,
                    spend_limit=limit,
                )
            except ValidationError:
                return report_invalid_input(
                    "change create",
                    "the change fields are invalid: --title and --id must be non-blank",
                    json_output=args.json_output,
                )
            stored, created = ChangeRepository(session).create(change)
            AuditRepository(session).append(
                actor=CLI_ACTOR,
                role=None,
                action=CHANGE_INTAKE_ACTION,
                resource_type="change",
                resource_id=stored.id,
                outcome="created" if created else "replayed",
            )
            guidance = build_change_guidance(session, stored)
    except SQLAlchemyError:
        return report_state_store_unreachable("change create", json_output=args.json_output)
    if args.json_output:
        print(
            json.dumps(
                {
                    "outcome": "created" if created else "replayed",
                    "persisted": created,
                    "change": stored.model_dump(mode="json"),
                    "guidance": guidance.model_dump(mode="json"),
                }
            )
        )
    else:
        verb = "created" if created else "replayed (already registered, nothing was written)"
        print(f"change {stored.id}: {verb}")
        print(render_change_text(stored))
        print(render_guidance_text(guidance))
    return EXIT_OK


def _status(
    factory: sessionmaker[Session], args: ChangeStatusArgs, artifacts: ArtifactService | None
) -> int:
    try:
        with session_scope(factory) as session:
            change = ChangeRepository(session).get(args.change_id)
            if change is None:
                return report_invalid_input(
                    "change status",
                    f"unknown change {args.change_id!r}",
                    json_output=args.json_output,
                )
            run = latest_run(session, change.id)
            conversation = ConversationRepository(session)
            # The whole discussion of the change, every phase: the report is the
            # operator's one place to see what is open.
            questions = conversation.list_questions(change.id)
            comments = conversation.list_comments(change.id)
            orders = conversation.list_rework_orders(change.id)
            phase = discussion_phase(
                run, current_change_phase(session, change, run=run, artifacts=artifacts)
            )
            gate = build_phase_gate(session, change, phase=phase, run=run, artifacts=artifacts)
            guidance = build_change_guidance(session, change, artifacts=artifacts)
    except SQLAlchemyError:
        return report_state_store_unreachable("change status", json_output=args.json_output)
    if args.json_output:
        print(
            json.dumps(
                {
                    "change": change.model_dump(mode="json"),
                    "run": run.model_dump(mode="json") if run is not None else None,
                    "questions": [q.model_dump(mode="json") for q in questions],
                    "comments": [c.model_dump(mode="json") for c in comments],
                    "rework_orders": [o.model_dump(mode="json") for o in orders],
                    "phase_gate": gate.model_dump(mode="json"),
                    "guidance": guidance.model_dump(mode="json"),
                }
            )
        )
    else:
        print(render_change_text(change, run))
        discussion = render_discussion_text(questions, comments, orders)
        if discussion:
            print(discussion)
        print(render_gate_text(gate))
        print(render_guidance_text(guidance))
    return EXIT_OK


def _phases(
    factory: sessionmaker[Session], args: ChangePhasesArgs, artifacts: ArtifactService | None
) -> int:
    try:
        with session_scope(factory) as session:
            change = ChangeRepository(session).get(args.change_id)
            if change is None:
                return report_invalid_input(
                    "change phases",
                    f"unknown change {args.change_id!r}",
                    json_output=args.json_output,
                )
            run = latest_run(session, change.id)
            projection = build_phases(
                session,
                change,
                run=run,
                artifacts=artifacts,
                waiting_on=waiting_on(session, run),
                waiting_phase=waiting_phase(session, run),
            )
            guidance = build_change_guidance(session, change, artifacts=artifacts)
    except SQLAlchemyError:
        return report_state_store_unreachable("change phases", json_output=args.json_output)
    if args.json_output:
        print(
            json.dumps(
                {
                    "phases": projection.model_dump(mode="json"),
                    "guidance": guidance.model_dump(mode="json"),
                }
            )
        )
    else:
        print(render_phases_text(projection))
        print(render_guidance_text(guidance))
    return EXIT_OK


# --- rendering --------------------------------------------------------------------------


def render_phases_text(projection: PhasesProjection) -> str:
    """The eight phases as a table: F-number, label, state, revision, counts, iteration (T098)."""
    lines = [f"phases of {projection.change_id}: current={projection.current.value}"]
    for view in projection.phases:
        marker = "*" if view.phase is projection.current else " "
        counts = (
            f"q={view.open_questions}(!{view.blocking_questions})"
            f" c={view.open_comments} iter={view.iteration}"
        )
        revision = view.revision[:12] if view.revision else "-"
        reason = f" — {view.state_reason}" if view.state_reason else ""
        lines.append(
            f"{marker} F{view.index} {view.label:<24} {view.state.value:<14} rev={revision:<12}"
            f" {counts}{reason}"
        )
    return "\n".join(lines)


def render_discussion_text(
    questions: list[Question], comments: list[Comment], orders: list[ReworkOrder]
) -> str:
    """The discussion of the current phase as facts: questions, comments, rework orders."""
    lines: list[str] = []
    open_questions = [q for q in questions if q.status is QuestionStatus.OPEN]
    if open_questions:
        lines.append(f"questions open: {len(open_questions)}")
        for question in open_questions:
            where = (
                f" [{question.anchor.artifact}"
                + (f"#{question.anchor.anchor_id}" if question.anchor.anchor_id else "")
                + "]"
                if question.anchor is not None
                else ""
            )
            options = f" options: {' | '.join(question.options)}" if question.options else ""
            blocking = " (blocking)" if question.blocking else ""
            lines.append(f"  {question.id}{blocking}{where}: {question.text}{options}")
    answered = [q for q in questions if q.status is QuestionStatus.ANSWERED]
    if answered:
        lines.append(f"questions answered, not yet applied: {len(answered)}")
    open_comments = [c for c in comments if c.is_open]
    if open_comments:
        lines.append(f"comments open: {len(open_comments)}")
        for comment in open_comments:
            where = comment.anchor.artifact + (
                f"#{comment.anchor.anchor_id}" if comment.anchor.anchor_id else ""
            )
            lines.append(f"  {comment.id} [{where}] {comment.status.value}: {comment.body}")
    for order in orders:
        summary = ""
        if order.summary is not None:
            summary = (
                f" changed: {len(order.summary.changed)}, remaining: {len(order.summary.remaining)}"
            )
        lines.append(
            f"rework order {order.id}: {order.status.value}"
            + (f" (round {order.round})" if order.round is not None else "")
            + summary
        )
    return "\n".join(lines)


def render_gate_text(gate: PhaseGate) -> str:
    """The phase gate as one line plus its reasons and stale approvals (T087)."""
    state = "available" if gate.available else "closed"
    revision = gate.current_revision or "unknown"
    lines = [
        f"gate {gate.phase.value}: {state} (revision={revision},"
        f" approved={'yes' if gate.approved else 'no'},"
        f" rework={gate.rework_rounds_used}/{gate.rework_rounds_max})"
    ]
    lines.extend(f"  - {reason.what} — {reason.how}" for reason in gate.reasons)
    stale = [view for view in gate.approvals if view.state == "stale"]
    if stale:
        lines.append(f"  stale approvals: {', '.join(view.decision_id for view in stale)}")
    return "\n".join(lines)


def render_change_text(change: Change, run: ChangeRun | None = None) -> str:
    """The change as facts: product, scenario, limit, brief status, latest run."""
    limit = (
        f"{change.spend_limit.cost_budget_usd} USD" if change.spend_limit is not None else "none"
    )
    brief = change.brief
    brief_line = (
        "brief: none"
        if brief is None
        else f"brief: {brief.status.value}"
        + (f" (missing: {', '.join(brief.missing)})" if brief.missing else "")
        + (f"; error: {brief.error}" if brief.error else "")
    )
    lines = [
        f"change {change.id}: {change.title}"
        f" (product={change.product_id or '-'}, repository="
        f"{change.product.provider.value}:{change.product.slug},"
        f" scenario={change.scenario.value}, limit={limit}, risk={change.risk_class.value})",
        brief_line,
    ]
    if brief is not None and brief.problem:
        lines.append(f"problem: {brief.problem}")
    if brief is not None and brief.goal:
        lines.append(f"goal: {brief.goal}")
    if run is not None:
        lines.append(
            f"run {run.id}: {run.status.value} (state_revision={run.state_revision},"
            f" cost_used={run.budget.cost_used})"
        )
    else:
        lines.append("run: none")
    return "\n".join(lines)


# --- M2: the discussion and the artifacts from the CLI (T086) ---------------------------
#
# ``change answer|comment|rework|approve`` are the operator's decisions of the
# cycle «вопрос → ответ → правка → сводка → согласовать / на доработку» (plan
# §6); ``change artifacts`` reads and writes the document artifacts (ADR-035).
# The operations are the ones the API runs (``orchestration.state.
# conversation_ops``, ``orchestration.artifacts``), so the two surfaces agree
# (ADR-033 p.3); the ``repository`` seam is the product repository port the
# composition root binds — without it revisions are unknown and the artifact
# commands refuse (exit 2) instead of inventing a document.


def _artifacts_of(repository: "RepositoryPort | None") -> ArtifactService | None:
    return ArtifactService(repository) if repository is not None else None


def _phase_for(
    session: Session,
    change: Change,
    requested: Phase | None,
    artifacts: ArtifactService | None = None,
) -> Phase:
    if requested is not None:
        return requested
    run = latest_run(session, change.id)
    return discussion_phase(
        run, current_change_phase(session, change, run=run, artifacts=artifacts)
    )


def _cli_audit(session: Session, action: str, resource_type: str, resource_id: str) -> None:
    AuditRepository(session).append(
        actor=CLI_ACTOR,
        role=None,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        outcome="created",
    )


def _emit(json_output: bool, *, payload: dict[str, object], text: str, guidance: Guidance) -> int:
    """Print the outcome plus the next step: JSON as one object, text as lines."""
    if json_output:
        print(json.dumps({**payload, "guidance": guidance.model_dump(mode="json")}))
    else:
        print(text)
        print(render_guidance_text(guidance))
    return EXIT_OK


def _conversation_error(command: str, error: ConversationError, json_output: bool) -> int:
    """A typed refusal of the shared operation is invalid input of the CLI (exit 2)."""
    return report_invalid_input(command, str(error), json_output=json_output)


def run_change_answer_command(
    args: ChangeAnswerArgs,
    *,
    session_factory: sessionmaker[Session] | None = None,
    repository: "RepositoryPort | None" = None,
) -> int:
    """Handle ``factory change answer``; return the process exit code (T086, ADR-034 p.1)."""
    return _with_store(
        "change answer",
        args.json_output,
        session_factory,
        lambda factory: _answer(factory, args, _artifacts_of(repository)),
    )


def run_change_comment_command(
    args: ChangeCommentArgs,
    *,
    session_factory: sessionmaker[Session] | None = None,
    repository: "RepositoryPort | None" = None,
) -> int:
    """Handle ``factory change comment``; return the process exit code (T086, ADR-034 p.1)."""
    return _with_store(
        "change comment",
        args.json_output,
        session_factory,
        lambda factory: _comment(factory, args, _artifacts_of(repository)),
    )


def run_change_rework_command(
    args: ChangeReworkArgs,
    *,
    session_factory: sessionmaker[Session] | None = None,
    repository: "RepositoryPort | None" = None,
) -> int:
    """Handle ``factory change rework``; return the process exit code (T086, ADR-034 p.3)."""
    return _with_store(
        "change rework",
        args.json_output,
        session_factory,
        lambda factory: _rework(factory, args, _artifacts_of(repository)),
    )


def run_change_approve_command(
    args: ChangeApproveArgs,
    *,
    session_factory: sessionmaker[Session] | None = None,
    repository: "RepositoryPort | None" = None,
) -> int:
    """Handle ``factory change approve``; return the process exit code (T086/T087)."""
    return _with_store(
        "change approve",
        args.json_output,
        session_factory,
        lambda factory: _approve(factory, args, _artifacts_of(repository)),
    )


def run_change_artifacts_command(
    args: ChangeArtifactsArgs,
    *,
    session_factory: sessionmaker[Session] | None = None,
    repository: "RepositoryPort | None" = None,
) -> int:
    """Handle ``factory change artifacts``; return the process exit code (T086, ADR-035).

    Without the repository seam the command refuses before touching the store:
    a document the factory cannot read is never shown (ADR-035 p.1).
    """
    if repository is None:
        return report_invalid_input(
            "change artifacts", REPOSITORY_UNCONFIGURED, json_output=args.json_output
        )
    if args.action is not ArtifactAction.LIST and not args.path:
        return report_invalid_input(
            "change artifacts", "--path is required for this action", json_output=args.json_output
        )
    return _with_store(
        "change artifacts",
        args.json_output,
        session_factory,
        lambda factory: _artifacts(factory, args, ArtifactService(repository)),
    )


def _load_change(session: Session, command: str, change_id: str, json_output: bool) -> Change | int:
    change = ChangeRepository(session).get(change_id)
    if change is None:
        return report_invalid_input(
            command, f"unknown change {change_id!r}", json_output=json_output
        )
    return change


def _answer(
    factory: sessionmaker[Session], args: ChangeAnswerArgs, artifacts: ArtifactService | None
) -> int:
    try:
        with session_scope(factory) as session:
            change = _load_change(session, "change answer", args.change_id, args.json_output)
            if isinstance(change, int):
                return change
            try:
                question, created = answer_question(
                    session,
                    change,
                    args.question_id,
                    value=args.value,
                    comment=args.comment,
                    answered_by=CLI_ACTOR,
                )
            except ConversationError as error:
                return _conversation_error("change answer", error, args.json_output)
            _cli_audit(session, QUESTION_ANSWER_ACTION, "question", question.id)
            guidance = build_change_guidance(session, change, artifacts=artifacts)
    except SQLAlchemyError:
        return report_state_store_unreachable("change answer", json_output=args.json_output)
    verb = "answered" if created else "already answered (nothing was written)"
    answer_value = question.answer.value if question.answer is not None else "-"
    return _emit(
        args.json_output,
        payload={
            "outcome": "answered" if created else "replayed",
            "question": question.model_dump(mode="json"),
        },
        text=f"question {question.id}: {verb} — {answer_value}",
        guidance=guidance,
    )


def _comment(
    factory: sessionmaker[Session], args: ChangeCommentArgs, artifacts: ArtifactService | None
) -> int:
    try:
        with session_scope(factory) as session:
            change = _load_change(session, "change comment", args.change_id, args.json_output)
            if isinstance(change, int):
                return change
            stored, _created = add_comment(
                session,
                change,
                phase=_phase_for(session, change, args.phase, artifacts),
                artifact=args.artifact,
                anchor_id=args.anchor_id,
                body=args.body,
                author=CLI_ACTOR,
                artifacts=artifacts,
            )
            _cli_audit(session, COMMENT_ADD_ACTION, "comment", stored.id)
            guidance = build_change_guidance(session, change, artifacts=artifacts)
    except SQLAlchemyError:
        return report_state_store_unreachable("change comment", json_output=args.json_output)
    where = stored.anchor.artifact + (
        f"#{stored.anchor.anchor_id}" if stored.anchor.anchor_id else ""
    )
    revision = stored.anchor.revision or "unbound"
    return _emit(
        args.json_output,
        payload={"outcome": "created", "comment": stored.model_dump(mode="json")},
        text=f"comment {stored.id}: {where} @ {revision} — {stored.body}",
        guidance=guidance,
    )


def _rework(
    factory: sessionmaker[Session], args: ChangeReworkArgs, artifacts: ArtifactService | None
) -> int:
    try:
        with session_scope(factory) as session:
            change = _load_change(session, "change rework", args.change_id, args.json_output)
            if isinstance(change, int):
                return change
            try:
                order, created = issue_rework_order(
                    session,
                    change,
                    phase=_phase_for(session, change, args.phase, artifacts),
                    comment_ids=args.comment_ids,
                    question_ids=args.question_ids,
                    instruction=args.instruction,
                    issued_by=CLI_ACTOR,
                    actor_role=None,
                    artifacts=artifacts,
                )
            except ConversationError as error:
                return _conversation_error("change rework", error, args.json_output)
            _cli_audit(session, REWORK_ORDER_ACTION, "rework_order", order.id)
            guidance = build_change_guidance(session, change, artifacts=artifacts)
    except SQLAlchemyError:
        return report_state_store_unreachable("change rework", json_output=args.json_output)
    return _emit(
        args.json_output,
        payload={
            "outcome": "created" if created else "replayed",
            "rework_order": order.model_dump(mode="json"),
        },
        text=(
            f"rework order {order.id}: {order.status.value} (phase={order.phase.value},"
            f" comments={len(order.comment_ids)}, questions={len(order.question_ids)})"
        ),
        guidance=guidance,
    )


def _approve(
    factory: sessionmaker[Session], args: ChangeApproveArgs, artifacts: ArtifactService | None
) -> int:
    try:
        with session_scope(factory) as session:
            change = _load_change(session, "change approve", args.change_id, args.json_output)
            if isinstance(change, int):
                return change
            try:
                decision, gate = record_phase_decision(
                    session,
                    change,
                    phase=_phase_for(session, change, args.phase, artifacts),
                    outcome=DecisionOutcome.WAIVED if args.waive else DecisionOutcome.APPROVED,
                    subject_revision=args.revision,
                    comment=args.comment,
                    actor_role=None,
                    artifacts=artifacts,
                )
            except ConversationError as error:
                return _conversation_error("change approve", error, args.json_output)
            _cli_audit(session, APPROVAL_RECORD_ACTION, "change", change.id)
            guidance = build_change_guidance(session, change, artifacts=artifacts)
    except SQLAlchemyError:
        return report_state_store_unreachable("change approve", json_output=args.json_output)
    return _emit(
        args.json_output,
        payload={
            "outcome": "created",
            "decision": decision.model_dump(mode="json"),
            "phase_gate": gate.model_dump(mode="json"),
        },
        text=(
            f"decision {decision.id}: {decision.outcome.value} {decision.gate.value}"
            f" @ {decision.commit_sha} (phase={gate.phase.value})"
        ),
        guidance=guidance,
    )


def _read_content(source: str) -> str:
    try:
        return sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
    except OSError as error:
        raise IntakeInputError(f"cannot read the content: {os_error_reason(error)}") from error


def _artifacts(
    factory: sessionmaker[Session], args: ChangeArtifactsArgs, service: ArtifactService
) -> int:
    command = "change artifacts"
    try:
        with session_scope(factory) as session:
            change = _load_change(session, command, args.change_id, args.json_output)
            if isinstance(change, int):
                return change
            match args.action:
                case ArtifactAction.LIST:
                    tree = service.tree(change)
                    if args.json_output:
                        print(json.dumps(tree.model_dump(mode="json")))
                    elif not tree.exists:
                        print(f"change {change.id}: no branch yet ({tree.branch})")
                    elif not tree.nodes:
                        print(f"change {change.id}: {tree.branch} @ {tree.revision} — no artifacts")
                    else:
                        print(f"change {change.id}: {tree.branch} @ {tree.revision}")
                        print("\n".join(f"{node.kind.value:7} {node.path}" for node in tree.nodes))
                    return EXIT_OK
                case ArtifactAction.SHOW:
                    document = service.get(change, args.path or "", revision=args.revision)
                    if args.json_output:
                        print(json.dumps(document.model_dump(mode="json")))
                    else:
                        sys.stdout.write(document.content)
                        if not document.content.endswith("\n"):
                            sys.stdout.write("\n")
                    return EXIT_OK
                case ArtifactAction.VERSIONS:
                    versions = service.versions(change, args.path or "")
                    if args.json_output:
                        print(json.dumps([v.model_dump(mode="json") for v in versions]))
                    elif versions:
                        print(
                            "\n".join(
                                f"{v.revision} {v.message.splitlines()[0] if v.message else ''}"
                                for v in versions
                            )
                        )
                    else:
                        print(f"{args.path}: no revisions on the change branch")
                    return EXIT_OK
                case ArtifactAction.DIFF:
                    if not args.from_revision or not args.to_revision:
                        return report_invalid_input(
                            command,
                            "--from and --to are required for diff",
                            json_output=args.json_output,
                        )
                    diff = service.diff(
                        change,
                        args.path or "",
                        from_revision=args.from_revision,
                        to_revision=args.to_revision,
                    )
                    if args.json_output:
                        print(json.dumps(diff.model_dump(mode="json")))
                    else:
                        sys.stdout.write(diff.unified or f"{args.path}: no differences\n")
                    return EXIT_OK
                case ArtifactAction.EDIT:
                    if not args.file:
                        return report_invalid_input(
                            command, "--file is required for edit", json_output=args.json_output
                        )
                    content = _read_content(args.file)
                    written = service.write(
                        change,
                        args.path or "",
                        content,
                        base_revision=args.base_revision,
                        actor=CLI_ACTOR,
                    )
                    ArtifactDraftRepository(session).delete(change.id, args.path or "")
                    conversation = ConversationRepository(session)
                    effects = apply_revision(
                        conversation.list_questions(change.id, artifact=args.path),
                        conversation.list_comments(change.id, artifact=args.path),
                        {args.path or "": content},
                    )
                    for question in effects.stale_questions:
                        conversation.save_question(question)
                    _cli_audit(
                        session, ARTIFACT_EDIT_ACTION, "artifact", f"{change.id}:{args.path}"
                    )
                    guidance = build_change_guidance(session, change, artifacts=service)
                    created = written.revision != written.previous_revision
                    stale_ids = ", ".join(q.id for q in effects.stale_questions)
                    detached_ids = ", ".join(c.id for c in effects.detached_comments)
                    text = (
                        f"{written.path}: {'committed' if created else 'unchanged'}"
                        f" {written.revision} (was {written.previous_revision})"
                    )
                    if stale_ids:
                        text += f"; stale questions: {stale_ids}"
                    if detached_ids:
                        text += f"; detached comments: {detached_ids}"
                    return _emit(
                        args.json_output,
                        payload={
                            "outcome": "created" if created else "unchanged",
                            "path": written.path,
                            "revision": written.revision,
                            "previous_revision": written.previous_revision,
                            "stale_questions": [q.id for q in effects.stale_questions],
                            "detached_comments": [c.id for c in effects.detached_comments],
                        },
                        text=text,
                        guidance=guidance,
                    )
    except IntakeInputError as error:
        return report_invalid_input(command, str(error), json_output=args.json_output)
    except ArtifactNotFoundError as error:
        return report_invalid_input(command, str(error), json_output=args.json_output)
    except ArtifactConflictError as error:
        return report_execution_error(command, str(error), json_output=args.json_output)
    except SQLAlchemyError:
        return report_state_store_unreachable(command, json_output=args.json_output)


# --- decisions and UI spec (M3, T093/T094) ----------------------------------------------


def run_change_decisions_command(
    args: ChangeDecisionsArgs,
    *,
    session_factory: sessionmaker[Session] | None = None,
    repository: "RepositoryPort | None" = None,
) -> int:
    """Handle ``factory change decisions`` (T093): the ADR cards with their derived status.

    The same view ``GET /changes/{id}/decisions`` serves. Without the
    repository seam the command refuses: a card the factory cannot read is
    never shown (ADR-035 p.1).
    """
    if repository is None:
        return report_invalid_input(
            "change decisions", REPOSITORY_UNCONFIGURED, json_output=args.json_output
        )
    return _with_store(
        "change decisions",
        args.json_output,
        session_factory,
        lambda factory: _decisions(factory, args, ArtifactService(repository)),
    )


def run_change_alternative_command(
    args: ChangeAlternativeArgs,
    *,
    session_factory: sessionmaker[Session] | None = None,
    repository: "RepositoryPort | None" = None,
) -> int:
    """Handle ``factory change alternative`` (T093): «Запросить альтернативу» on one ADR.

    The same operation as ``POST /changes/{id}/decisions/{adr}/alternative``: a
    rework order of the architecture phase naming the decision, with the
    version-bound ``rejected`` decision recorded next to it.
    """
    if repository is None:
        return report_invalid_input(
            "change alternative", REPOSITORY_UNCONFIGURED, json_output=args.json_output
        )
    if not args.instruction.strip():
        return report_invalid_input(
            "change alternative", "--instruction must not be blank", json_output=args.json_output
        )
    return _with_store(
        "change alternative",
        args.json_output,
        session_factory,
        lambda factory: _alternative(factory, args, ArtifactService(repository)),
    )


def run_change_ui_command(
    args: ChangeUiArgs,
    *,
    session_factory: sessionmaker[Session] | None = None,
    repository: "RepositoryPort | None" = None,
) -> int:
    """Handle ``factory change ui`` (T094): scenarios, screens and links of the change."""
    if repository is None:
        return report_invalid_input(
            "change ui", REPOSITORY_UNCONFIGURED, json_output=args.json_output
        )
    return _with_store(
        "change ui",
        args.json_output,
        session_factory,
        lambda factory: _ui(factory, args, ArtifactService(repository)),
    )


def _decisions_view(session: Session, change: Change, service: ArtifactService) -> DecisionsView:
    # Imported here: the API module is the one place the view assembly lives
    # (shared with the router), and cli.changes must not be imported by it.
    from dark_factory.orchestration.state.decisions import load_decisions_view

    return load_decisions_view(session, change, artifacts=service)


def _decisions(
    factory: sessionmaker[Session], args: ChangeDecisionsArgs, service: ArtifactService
) -> int:
    try:
        with session_scope(factory) as session:
            change = _load_change(session, "change decisions", args.change_id, args.json_output)
            if isinstance(change, int):
                return change
            view = _decisions_view(session, change, service)
            guidance = build_change_guidance(session, change, artifacts=service)
    except SQLAlchemyError:
        return report_state_store_unreachable("change decisions", json_output=args.json_output)
    return _emit(
        args.json_output,
        payload={"decisions": view.model_dump(mode="json")},
        text=render_decisions_text(view),
        guidance=guidance,
    )


def _alternative(
    factory: sessionmaker[Session], args: ChangeAlternativeArgs, service: ArtifactService
) -> int:
    try:
        with session_scope(factory) as session:
            change = _load_change(session, "change alternative", args.change_id, args.json_output)
            if isinstance(change, int):
                return change
            view = _decisions_view(session, change, service)
            if all(card.id != args.decision_id for card in view.decisions):
                return report_invalid_input(
                    "change alternative",
                    f"decision {args.decision_id!r} is not among the ADRs of the change",
                    json_output=args.json_output,
                )
            try:
                order, created = issue_rework_order(
                    session,
                    change,
                    phase=Phase.ARCHITECTURE,
                    comment_ids=args.comment_ids,
                    question_ids=(),
                    instruction=args.instruction,
                    issued_by=CLI_ACTOR,
                    actor_role=None,
                    artifacts=service,
                    decision_ids=[args.decision_id],
                )
            except ConversationError as error:
                return _conversation_error("change alternative", error, args.json_output)
            _cli_audit(session, REWORK_ORDER_ACTION, "rework_order", order.id)
            guidance = build_change_guidance(session, change, artifacts=service)
    except SQLAlchemyError:
        return report_state_store_unreachable("change alternative", json_output=args.json_output)
    return _emit(
        args.json_output,
        payload={
            "outcome": "created" if created else "replayed",
            "rework_order": order.model_dump(mode="json"),
        },
        text=(
            f"rework order {order.id}: {order.status.value} (phase={order.phase.value},"
            f" decisions={', '.join(order.decision_ids)}, comments={len(order.comment_ids)})"
        ),
        guidance=guidance,
    )


def _ui(factory: sessionmaker[Session], args: ChangeUiArgs, service: ArtifactService) -> int:
    try:
        with session_scope(factory) as session:
            change = _load_change(session, "change ui", args.change_id, args.json_output)
            if isinstance(change, int):
                return change
            view = build_ui_spec_view(change, artifacts=service)
    except SQLAlchemyError:
        return report_state_store_unreachable("change ui", json_output=args.json_output)
    if args.json_output:
        payload = view.model_dump(mode="json")
        if args.section is not None:
            keep = {"change_id", "revision", "dev_url", "errors", args.section.value}
            payload = {key: value for key, value in payload.items() if key in keep}
        print(json.dumps(payload))
    else:
        print(render_ui_text(view, section=args.section))
    return EXIT_OK


def render_decisions_text(view: DecisionsView) -> str:
    """One block per ADR card: id, title, status, revision, proposal, alternatives, open order."""
    lines = [
        f"decisions of {view.change_id}: revision={view.revision or '-'}"
        f" approved={'yes' if view.approved else 'no'}"
    ]
    if not view.decisions:
        lines.append("  (no ADR documents on the change branch)")
    for card in view.decisions:
        proposal = card.proposal.splitlines()[0] if card.proposal else "-"
        pending = card.pending_alternative.id if card.pending_alternative else "-"
        lines.append(f"- {card.id}: {card.title}")
        lines.append(
            f"  status={card.status} document_status={card.document_status or '-'}"
            f" rev={card.revision or '-'}"
        )
        lines.append(f"  proposal: {proposal}")
        lines.append(
            f"  alternatives={len(card.alternatives)} impact={', '.join(card.impact) or '-'}"
            f" pending_alternative={pending}"
        )
        if card.affected_artifacts:
            lines.append(f"  affected: {', '.join(card.affected_artifacts)}")
        for error in card.errors:
            lines.append(f"  ! {error}")
    for error in view.errors:
        lines.append(f"! {error}")
    return "\n".join(lines)


def render_ui_text(view: UiSpecView, *, section: UiSection | None = None) -> str:
    """The UI spec as text: scenarios with steps, screens with states/elements, links."""
    lines = [
        f"ui of {view.change_id}: revision={view.revision or '-'} dev_url={view.dev_url or '-'}"
    ]
    if section in (None, UiSection.SCENARIOS):
        lines.append(f"scenarios ({len(view.scenarios)}):")
        for scenario in view.scenarios:
            lines.append(
                f"- {scenario.id}: {scenario.title} [{', '.join(scenario.screens) or '-'}]"
            )
            for step in scenario.steps:
                target = f" -> {step.screen}" if step.screen else ""
                lines.append(f"  {step.id}: {step.text}{target}")
    if section in (None, UiSection.SCREENS):
        lines.append(f"screens ({len(view.screens)}):")
        for screen in view.screens:
            lines.append(
                f"- {screen.id}: {screen.title} route={screen.route or '-'}"
                f" preview={screen.preview_url or '-'}"
            )
            states = ", ".join(state.kind for state in screen.states) or "-"
            lines.append(f"  states: {states}")
            for element in screen.elements:
                lines.append(
                    f"  {element.id}: {element.kind or '-'} {element.label or ''}"
                    f"{' (' + element.component + ')' if element.component else ''}".rstrip()
                )
    if section in (None, UiSection.LINKS):
        lines.append(f"links ({len(view.links)}):")
        for link in view.links:
            detail = " / ".join(part for part in (link.trigger, link.condition) if part)
            lines.append(f"- {link.id}{': ' + detail if detail else ''}")
    for error in view.errors:
        lines.append(f"! {error}")
    return "\n".join(lines)
