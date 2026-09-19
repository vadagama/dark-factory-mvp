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
from typing import Final

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from dark_factory.changes.enums import BriefAuthor, ChangeSource
from dark_factory.changes.intake import IntakeBrief, SpendLimit
from dark_factory.changes.run import Change, ChangeRun
from dark_factory.cli._common import (
    CLI_ACTOR,
    StateStoreUnreachableError,
    open_state_store,
    os_error_reason,
    report_invalid_input,
    report_state_store_unreachable,
)
from dark_factory.cli.guidance import render_guidance_text
from dark_factory.cli.main import EXIT_OK, ChangeCreateArgs, ChangeStatusArgs
from dark_factory.orchestration.state.change_store import (
    CHANGE_INTAKE_ACTION,
    AuditRepository,
    ChangeRepository,
    ProductRepository,
)
from dark_factory.orchestration.state.engine import session_scope
from dark_factory.orchestration.state.guidance import build_change_guidance, latest_run

__all__ = [
    "brief_from_args",
    "new_change_id",
    "render_change_text",
    "run_change_create_command",
    "run_change_status_command",
    "spend_limit_from_args",
]

CHANGE_ID_PREFIX: Final[str] = "chg_"


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
    args: ChangeStatusArgs, *, session_factory: sessionmaker[Session] | None = None
) -> int:
    """Handle ``factory change status``; return the process exit code (T073)."""
    return _with_store(
        "change status", args.json_output, session_factory, lambda factory: _status(factory, args)
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


def _status(factory: sessionmaker[Session], args: ChangeStatusArgs) -> int:
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
            guidance = build_change_guidance(session, change)
    except SQLAlchemyError:
        return report_state_store_unreachable("change status", json_output=args.json_output)
    if args.json_output:
        print(
            json.dumps(
                {
                    "change": change.model_dump(mode="json"),
                    "run": run.model_dump(mode="json") if run is not None else None,
                    "guidance": guidance.model_dump(mode="json"),
                }
            )
        )
    else:
        print(render_change_text(change, run))
        print(render_guidance_text(guidance))
    return EXIT_OK


# --- rendering --------------------------------------------------------------------------


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
