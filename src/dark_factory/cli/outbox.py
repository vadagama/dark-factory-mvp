"""``factory outbox``: delivery of outbox events (T028, contract cli.md, ADR-016 p.4/p.5/p.6).

``outbox dispatch`` runs exactly one
:class:`~dark_factory.orchestration.events.dispatcher.OutboxDispatcher` pass
over the PostgreSQL state store (``DATABASE_URL``, ADR-004/ADR-009) — the
same pass the scheduled CronJob in ``deploy/events/`` executes — and prints
its report (text by default, stable JSON with ``--json``). ``--cleanup``
adds the retention cleanup step (ADR-016 p.9) to the pass; ``--limit`` caps
the reserved batch. ``outbox replay`` resets dead/failed deliveries of one
event to ``pending`` (manual replay, ADR-016 p.5); ``outbox skip`` records an
operator's administrative waiver of one stuck delivery, unblocking the stream
(ADR-016 p.6).

Exit codes (contract cli.md): 0 for a finished command (including a pass with
nothing to do), 1 when a replay/skip target does not exist (or is not in a
replayable/skippable state), and 2 when the state store is unreachable or the
input is invalid (non-positive ``--limit``). Errors never echo the URL or the
raw exception, which may contain credentials (ADR-009).
"""

import asyncio
import json
import sys
from collections.abc import Callable

from sqlalchemy.orm import Session, sessionmaker

from dark_factory.cli._common import (
    StateStoreUnreachableError,
    open_state_store,
    report_state_store_unreachable,
)
from dark_factory.cli.main import (
    EXIT_ERROR,
    EXIT_INVALID_INPUT,
    EXIT_OK,
    OutboxDispatchArgs,
    OutboxReplayArgs,
    OutboxSkipArgs,
)
from dark_factory.orchestration.events.dispatcher import DISPATCH_BATCH_SIZE, OutboxDispatcher
from dark_factory.orchestration.events.models import (
    DeliveryOutcome,
    DeliveryOutcomeKind,
    DispatchReport,
)
from dark_factory.orchestration.events.repository import DeliveryRepository
from dark_factory.orchestration.state.engine import session_scope


def _run_with_store(operation: Callable[[sessionmaker[Session]], int], *, command: str) -> int:
    """Run ``operation`` against the state store; exit 2 fast when it is unreachable.

    The diagnostic goes to stderr in every mode: the JSON document of a
    finished command is only ever its report, never an error object.
    """
    try:
        with open_state_store() as factory:
            return operation(factory)
    except StateStoreUnreachableError:
        return report_state_store_unreachable(command, json_output=False)


def render_text(report: DispatchReport) -> str:
    """Human-readable one-line-per-outcome report of the pass."""
    counts: dict[DeliveryOutcomeKind, int] = {}
    for entry in report.outcomes:
        counts[entry.outcome] = counts.get(entry.outcome, 0) + 1
    summary = " ".join(
        f"{kind.value}={counts[kind]}" for kind in DeliveryOutcomeKind if kind in counts
    )
    lines = [f"factory outbox dispatch: reserved={report.reserved} {summary}"]
    lines.extend(_outcome_line(entry) for entry in report.outcomes)
    return "\n".join(lines)


def _outcome_line(entry: DeliveryOutcome) -> str:
    """One journal line: ``event[/consumer]: kind (attempts=N[, next=...][, error: ...])``."""
    subject = (
        entry.event_id if entry.consumer_id is None else f"{entry.event_id}/{entry.consumer_id}"
    )
    details = [f"attempts={entry.attempts}"]
    if entry.next_attempt_at is not None:
        details.append(f"next={entry.next_attempt_at.isoformat()}")
    if entry.error is not None:
        details.append(f"error: {entry.error}")
    return f"{subject}: {entry.outcome.value} ({', '.join(details)})"


def render_json(report: DispatchReport) -> str:
    """Stable JSON report (``--json``): the pass journal as a single object."""
    return json.dumps(report.model_dump(mode="json"))


def run_dispatch_command(
    args: OutboxDispatchArgs,
    *,
    session_factory: sessionmaker[Session] | None = None,
) -> int:
    """Run one dispatch pass (plus optional cleanup) and emit its report (contract cli.md).

    ``session_factory`` is an injection seam for tests; by default the state
    store is reached through ``DATABASE_URL`` and probed with a connection
    first so an unreachable store fails fast with exit code 2 instead of a
    traceback.
    """
    if args.limit is not None and args.limit < 1:
        print("factory outbox dispatch: --limit must be a positive integer", file=sys.stderr)
        return EXIT_INVALID_INPUT
    batch_size = DISPATCH_BATCH_SIZE if args.limit is None else args.limit

    def operation(factory: sessionmaker[Session]) -> int:
        report = asyncio.run(
            OutboxDispatcher(factory, batch_size=batch_size).run_pass(cleanup=args.cleanup)
        )
        print(render_json(report) if args.json_output else render_text(report))
        return EXIT_OK

    if session_factory is not None:
        return operation(session_factory)
    return _run_with_store(operation, command="outbox dispatch")


def run_replay_command(
    args: OutboxReplayArgs,
    *,
    session_factory: sessionmaker[Session] | None = None,
) -> int:
    """Reset dead/failed deliveries of one event to ``pending`` (ADR-016 p.5 manual replay)."""
    command = "outbox replay"

    def operation(factory: sessionmaker[Session]) -> int:
        with session_scope(factory) as session:
            replayed = DeliveryRepository(session).replay(
                event_id=args.event_id, consumer_id=args.consumer
            )
        if not replayed:
            print(
                f"factory {command}: no dead or failed delivery matches the given event/consumer",
                file=sys.stderr,
            )
            return EXIT_ERROR
        if args.json_output:
            payload = {
                "event_id": args.event_id,
                "replayed": [
                    {"consumer_id": consumer_id, "previous_status": previous}
                    for consumer_id, previous in replayed
                ],
            }
            print(json.dumps(payload))
        else:
            for consumer_id, previous in replayed:
                print(
                    f"factory {command}: {args.event_id}/{consumer_id} {previous} -> pending "
                    "(attempts reset)"
                )
        return EXIT_OK

    if session_factory is not None:
        return operation(session_factory)
    return _run_with_store(operation, command=command)


def run_skip_command(
    args: OutboxSkipArgs,
    *,
    session_factory: sessionmaker[Session] | None = None,
) -> int:
    """Waive one stuck delivery by operator decision, unblocking its stream (ADR-016 p.6)."""
    command = "outbox skip"

    def operation(factory: sessionmaker[Session]) -> int:
        with session_scope(factory) as session:
            previous = DeliveryRepository(session).skip(
                event_id=args.event_id, consumer_id=args.consumer
            )
        if previous is None:
            print(
                f"factory {command}: no pending, failed or dead delivery matches the given "
                "event/consumer",
                file=sys.stderr,
            )
            return EXIT_ERROR
        if args.json_output:
            payload = {
                "event_id": args.event_id,
                "consumer_id": args.consumer,
                "previous_status": previous,
            }
            print(json.dumps(payload))
        else:
            print(f"factory {command}: {args.event_id}/{args.consumer} {previous} -> waived")
        return EXIT_OK

    if session_factory is not None:
        return operation(session_factory)
    return _run_with_store(operation, command=command)
