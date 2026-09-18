"""``factory run advance`` and ``factory run status``: the durable run CLI (T-092).

``factory run advance`` drives exactly one stage of a run through the durable
runner (``orchestration/runner.py``): it loads the change snapshot and the run
from PostgreSQL (ADR-004), creates the run when only ``--change-id`` is given,
advances one stage, persists the whole decision atomically (the stage result,
the statuses, the successor stage row and the ``run.stage_completed`` event) and
reports it. Exactly one of ``--change-id`` / ``--run-id`` is required.

A repeat of an already committed stage operation writes nothing and reports
``replayed`` with the committed result (ADR-006 p.3, the same policy as
``factory stage run``). A stage whose attempt ended ``failed``/``blocked`` is
retried by the next advance: the attempt number advances and a new physical
attempt of the same logical operation is opened (ADR-006 p.7), so a failed stage
is re-run rather than refused.

- ``--change-id`` resolves the run from the change: the run id is derived from
  the change snapshot (``state.run_store.generated_run_id``), so a repeated call
  for the same snapshot advances the same run instead of starting a second one,
  and a first call on an unknown change is invalid input.
- ``--run-id`` advances an existing run and takes the change snapshot of that
  run from intake.

The route is the conservative ``standard`` profile (ADR-005: it never skips a
gate); the provider comes from the change's repository and is fixed for the life
of the run (ADR-019 p.5). A new run starts from the default budget snapshot and
without an approved Implementation Contract — entering construction without one
is blocked by the policy (T-016), which is the honest S1 outcome.

``factory run status`` reads the persisted run back and prints its status and
stages. Its ``--json`` document is the domain ``ChangeRun`` (``changes/run.py``):
``id``, ``change_id``, ``route``, ``provider``, ``status``, ``state_revision``,
``stages`` (each with ``stage``, ``status``, ``attempt_number``,
``input_revision``, ``state_revision``, ``started_at``, ``finished_at``),
``budget``, ``implementation_contract``, ``created_at``, ``updated_at``,
``finished_at``.

The ``--json`` document of ``run advance`` is one object with a stable key set:
``run_id``, ``change_id``, ``outcome``, ``persisted``, ``stage``,
``result_status``, ``next_action``, ``attempt_number``, ``stage_status``,
``run_status``, ``next_stage``, ``reason``. ``persisted`` is ``false`` for
``replayed`` (the committed result of the operation is authoritative and nothing
was written), and the four decision fields (``stage_status``, ``run_status``,
``next_stage``, ``reason``) are ``null`` there — the flow was not consulted.
That writer/decider split is deliberate: ``apply_result`` decides in memory and
the durable writer mirrors the decision through the domain transition tables;
both the stage status and the run status go through those tables — the run status
additionally passes ``ExecutionRepository.update_status``, which validates the
optimistic revision, the fencing token and ``RUN_STATUS_TRANSITIONS``.

Exit codes (contract cli.md), for ``run advance``:

| Code | Outcome |
|---|---|
| 0 | the stage completed: the run advanced (``advanced``) or finished (``completed``) |
| 10 | ``waiting`` — external wait, the result is already persisted (ADR-006 p.8) |
| 20 | ``blocked`` — a limit, gate or escalation needs a human decision |
| 1 | ``failed`` — the run reached a failed terminal status |
| 2 | invalid input, unknown change/run, an unadvanceable run, a refused or unreachable store |

A replayed advance reports the status of the committed result it returned —
``0`` for a committed ``succeeded``, ``10`` for a committed ``waiting``, ``20``
for ``blocked`` and ``1`` for ``failed`` — the same rule ``factory stage run``
applies to a committed result. A ``failed``/``blocked`` stage is not refused:
its next advance retries it as a new attempt (ADR-006 p.7).

``run status`` exits 0 for a run it found, 2 for an unknown run or an
unreachable store. Errors never echo the database URL or a raw exception text,
which may embed credentials (ADR-009); the ``owner_id`` of the run lease is
``DARK_FACTORY_RUN_OWNER_ID`` when set and unique per process otherwise.
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Final
from uuid import uuid4

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from dark_factory.changes.enums import Route, StageStatus
from dark_factory.changes.next_action import StopAction, WaitForInputAction
from dark_factory.changes.run import Change, ChangeRun
from dark_factory.changes.run_records import RunRecord
from dark_factory.changes.usage import BudgetSnapshot
from dark_factory.cli.main import (
    EXIT_BLOCKED,
    EXIT_ERROR,
    EXIT_INVALID_INPUT,
    EXIT_OK,
    EXIT_WAITING,
    RunAdvanceArgs,
    RunStatusArgs,
)
from dark_factory.cli.release import (
    ReleaseVerifyError,
    resolve_expected_digest,
    validate_smoke_options,
)
from dark_factory.cli.release_facts import CliReleaseFactsProvider
from dark_factory.cli.run_records import RunRecordError, collect_run_manifest
from dark_factory.execution.runs.store import RunRecordStore
from dark_factory.flows.routes import route_profile
from dark_factory.orchestration.runner import (
    FactsProvider,
    ReleaseFactsProvider,
    RevisionResolver,
    RunAdvance,
    RunAdvanceOutcome,
    RunnerError,
    StageExecutor,
    advance_run,
)
from dark_factory.orchestration.state.change_store import ChangeRepository
from dark_factory.orchestration.state.engine import (
    DEFAULT_DATABASE_URL,
    create_session_factory,
    create_state_engine,
    session_scope,
)
from dark_factory.orchestration.state.repositories import StateError
from dark_factory.orchestration.state.run_store import RunStore

DATABASE_URL_ENV_VAR: Final[str] = "DATABASE_URL"
"""State-store URL variable; the same one ``factory doctor`` checks (ADR-004)."""

RUN_OWNER_ID_ENV_VAR: Final[str] = "DARK_FACTORY_RUN_OWNER_ID"
"""Optional stable owner id of the run lease; a unique default otherwise."""

RUNS_ROOT_ENV_VAR: Final[str] = "DARK_FACTORY_RUNS_ROOT"
"""Checkout of the ``dark-factory-runs`` repository (ADR-015 p.4); ``--runs-root`` wins."""

DEFAULT_RUN_ROUTE: Final[Route] = Route.STANDARD
"""Route of a run created by the CLI (ADR-005): the full gate set, never a shortcut."""

_EXIT_CODE_BY_OUTCOME: Final[dict[RunAdvanceOutcome, int]] = {
    RunAdvanceOutcome.ADVANCED: EXIT_OK,
    RunAdvanceOutcome.COMPLETED: EXIT_OK,
    RunAdvanceOutcome.WAITING: EXIT_WAITING,
    RunAdvanceOutcome.BLOCKED: EXIT_BLOCKED,
    RunAdvanceOutcome.FAILED: EXIT_ERROR,
}

_REPLAY_EXIT_CODE_BY_STATUS: Final[dict[StageStatus, int]] = {
    StageStatus.SUCCEEDED: EXIT_OK,
    StageStatus.WAITING: EXIT_WAITING,
    StageStatus.FAILED: EXIT_ERROR,
    StageStatus.BLOCKED: EXIT_BLOCKED,
}
"""Exit codes of a replayed advance: the committed result status is authoritative.

Every result status is representable. A committed ``succeeded``/``waiting``
result of the operation the advance would execute is replayed before any write;
a ``failed``/``blocked`` result is what a racing writer committed for the attempt
this advance had opened (the retry of such a stage is the *next* attempt,
ADR-006 p.7) — either way the reported code is the status of that result.
"""


def exit_code_for(advance: RunAdvance) -> int:
    """Contract exit code of one advance (contract cli.md).

    A replay reports the status of the committed result it returned
    (``succeeded``/``waiting`` — the only replayable ones) and not an outcome the
    driver never reached; every other outcome has its own code.
    """
    if advance.outcome is RunAdvanceOutcome.REPLAYED:
        return _REPLAY_EXIT_CODE_BY_STATUS[advance.result.status]
    return _EXIT_CODE_BY_OUTCOME[advance.outcome]


class InvalidRunnerInput(ValueError):
    """Invalid command input or unknown change/run: nothing was advanced (exit 2)."""


def _database_url() -> str:
    """Configured state-store URL; the local default when unset (ADR-004)."""
    raw = os.environ.get(DATABASE_URL_ENV_VAR)
    return raw if raw and raw.strip() else DEFAULT_DATABASE_URL


def _default_owner_id() -> str:
    """Unique-per-process lease owner id so concurrent advances never share a lease."""
    configured = os.environ.get(RUN_OWNER_ID_ENV_VAR, "").strip()
    if configured:
        return configured
    return f"factory-run-{os.getpid()}-{uuid4().hex[:8]}"


def _validate_release_options(args: RunAdvanceArgs) -> str | None:
    """Validate the release options of the advance and resolve the expected digest.

    The XOR of ``--expected-digest``/``--digest-json`` holds when either is
    given (both set is a conflict, none set is a valid pre-S4 call — the
    digest is optional here, unlike ``release verify``); the smoke options
    must be consistent, and their URLs http(s). Raises
    :class:`ReleaseVerifyError` (a ``ValueError``) naming the rule, never
    echoing a value (ADR-009).
    """
    if args.expected_digest is not None and args.digest_json is not None:
        raise ReleaseVerifyError("--expected-digest and --digest-json are mutually exclusive")
    validate_smoke_options(
        smoke_url=args.smoke_url,
        smoke_digest_url=args.smoke_digest_url,
        smoke_digest_header=args.smoke_digest_header,
    )
    if args.expected_digest is None and args.digest_json is None:
        return None
    return resolve_expected_digest(
        expected_digest=args.expected_digest, digest_json=args.digest_json
    )


def run_advance_command(
    args: RunAdvanceArgs,
    *,
    session_factory: sessionmaker[Session] | None = None,
    owner_id: str | None = None,
    now: datetime | None = None,
    executor: StageExecutor | None = None,
    revision_of: RevisionResolver | None = None,
    gate_facts: FactsProvider | None = None,
    release_facts: ReleaseFactsProvider | None = None,
) -> int:
    """Handle ``factory run advance``; return the process exit code (contract cli.md).

    ``session_factory``, ``owner_id`` and ``now`` are injection seams for tests;
    ``executor``, ``revision_of``, ``gate_facts`` and ``release_facts`` are how
    the composition root (``dark_factory.runtime``) plugs the harness-backed
    executor, the SCM-derived revision resolver and the facts observers of the
    wait resolution (T-092 S3/S4) into the working path: this module is core
    and may not import ``runtime`` (ADR-024 p.5), so the bindings arrive as
    arguments. Without them the deterministic stage path runs and a waiting
    stage is never resolved by observed facts — exactly as before.

    The release options (T-092 S4) are validated and wired here, core-side:
    the expected digest must come from exactly one source (the XOR of
    ``--expected-digest``/``--digest-json``) and the smoke options must be
    consistent (exit 2 otherwise, before anything is observed); when any
    observation option is set, the value-level release facts provider of
    ``cli.release_facts`` observes the waiting release attempts. A terminal
    advance publishes the run record into the ``dark-factory-runs`` checkout
    (ADR-015 p.4) when ``--runs-root``/``DARK_FACTORY_RUNS_ROOT`` is set —
    best-effort, a publication failure never changes the exit code.
    """
    try:
        _validate_release_options(args)
    except ReleaseVerifyError as exc:
        return _report(
            "run advance", "invalid_input", str(exc), EXIT_INVALID_INPUT, args.json_output
        )
    if release_facts is None:
        release_facts = CliReleaseFactsProvider.from_options(args)
    resolved_owner = owner_id if owner_id is not None else _default_owner_id()
    if session_factory is not None:
        return _advance(
            session_factory,
            args,
            owner_id=resolved_owner,
            now=now,
            executor=executor,
            revision_of=revision_of,
            gate_facts=gate_facts,
            release_facts=release_facts,
        )
    try:
        engine = create_state_engine(_database_url())
        with engine.connect():
            pass  # liveness probe: fail fast with exit 2 when the store is unreachable
    except SQLAlchemyError:
        return _unreachable("run advance", args.json_output)
    factory = create_session_factory(engine)
    try:
        return _advance(
            factory,
            args,
            owner_id=resolved_owner,
            now=now,
            executor=executor,
            revision_of=revision_of,
            gate_facts=gate_facts,
            release_facts=release_facts,
        )
    finally:
        engine.dispose()


def run_status_command(
    args: RunStatusArgs, *, session_factory: sessionmaker[Session] | None = None
) -> int:
    """Handle ``factory run status``; return the process exit code (contract cli.md).

    A read-only command: the transaction it opens commits nothing. ``None``
    from the store means the run id is unknown — invalid input, not an empty
    report, so a typo never looks like a run without stages.
    """
    if session_factory is not None:
        return _show_status(session_factory, args)
    try:
        engine = create_state_engine(_database_url())
        with engine.connect():
            pass  # liveness probe: fail fast with exit 2 when the store is unreachable
    except SQLAlchemyError:
        return _unreachable("run status", args.json_output)
    factory = create_session_factory(engine)
    try:
        return _show_status(factory, args)
    finally:
        engine.dispose()


def render_advance_text(advance: RunAdvance) -> str:
    """Human-readable summary of one advance: outcome line plus the wait/stop reason.

    A replay has no decision to report: the flow was not consulted and nothing
    was written, so the line names the committed result instead of an effective
    action it did not take.
    """
    decision = advance.decision
    if decision is None:
        return (
            f"run {advance.result.run_id}: {advance.outcome.value}"
            f" (stage={advance.stage.value}, result_status={advance.result.status.value},"
            f" next_action={advance.result.next_action.type}; nothing was written)"
        )
    lines = [
        f"run {advance.result.run_id}: {advance.outcome.value}"
        f" (stage={decision.stage.value}, stage_status={decision.stage_status.value},"
        f" run_status={decision.run_status.value}, next_action={decision.action.type},"
        f" next_stage={decision.next_stage.value if decision.next_stage else None})"
    ]
    if isinstance(decision.action, WaitForInputAction | StopAction):
        lines.append(f"reason: {decision.action.reason}")
    return "\n".join(lines)


def render_advance_json(advance: RunAdvance) -> str:
    """Stable JSON of one advance (shape in the module docstring).

    The keys are always present; the fields that belong to a flow decision are
    ``null`` on a replay, where there is no decision and nothing was written.
    """
    decision = advance.decision
    action = decision.action if decision is not None else advance.result.next_action
    payload: dict[str, object] = {
        "run_id": advance.result.run_id,
        "change_id": advance.result.change_id,
        "outcome": advance.outcome.value,
        "persisted": decision is not None,
        "stage": advance.stage.value,
        "result_status": advance.result.status.value,
        "next_action": action.type,
        "attempt_number": advance.result.attempt_number,
        "stage_status": decision.stage_status.value if decision is not None else None,
        "run_status": decision.run_status.value if decision is not None else None,
        "next_stage": (
            decision.next_stage.value if decision is not None and decision.next_stage else None
        ),
        "reason": action.reason if isinstance(action, WaitForInputAction | StopAction) else None,
    }
    return json.dumps(payload)


def render_status_text(run: ChangeRun) -> str:
    """Human-readable run report: one header line plus one line per stage."""
    lines = [
        f"run {run.id}: {run.status.value}"
        f" (change_id={run.change_id}, route={run.route.value}, provider={run.provider.value},"
        f" state_revision={run.state_revision})"
    ]
    lines.extend(
        f"{stage_run.stage.value}: {stage_run.status.value}"
        f" (attempt={stage_run.attempt_number}, input_revision={stage_run.input_revision},"
        f" state_revision={stage_run.state_revision})"
        for stage_run in run.stages
    )
    return "\n".join(lines)


def render_status_json(run: ChangeRun) -> str:
    """Stable JSON of a run: the domain ``ChangeRun`` document (module docstring)."""
    return json.dumps(run.model_dump(mode="json"))


def _advance(
    factory: sessionmaker[Session],
    args: RunAdvanceArgs,
    *,
    owner_id: str,
    now: datetime | None,
    executor: StageExecutor | None,
    revision_of: RevisionResolver | None,
    gate_facts: FactsProvider | None,
    release_facts: ReleaseFactsProvider | None,
) -> int:
    """Advance one stage in one transaction and emit the outcome (contract cli.md)."""
    try:
        with session_scope(factory) as session:
            advance = _advance_in_session(
                session,
                args,
                owner_id=owner_id,
                now=now,
                executor=executor,
                revision_of=revision_of,
                gate_facts=gate_facts,
                release_facts=release_facts,
            )
    except InvalidRunnerInput as exc:
        return _report(
            "run advance", "invalid_input", str(exc), EXIT_INVALID_INPUT, args.json_output
        )
    except RunnerError as exc:
        # An unknown run or a terminal one: the command cannot advance the run,
        # and nothing was written. A committed failed/blocked attempt is not a
        # refusal any more — the next advance retries it (ADR-006 p.7).
        return _report(
            "run advance", "invalid_input", str(exc), EXIT_INVALID_INPUT, args.json_output
        )
    except StateError:
        # The store refused the write — a stale revision, a lost lease, a missing
        # row: the advance did not happen. The text may embed identifiers, so it
        # is not echoed (ADR-009).
        return _report(
            "run advance",
            "invalid_input",
            "the state store refused the advance (conflict, lost lease or missing state)",
            EXIT_INVALID_INPUT,
            args.json_output,
        )
    except SQLAlchemyError:
        return _unreachable("run advance", args.json_output)
    print(render_advance_json(advance) if args.json_output else render_advance_text(advance))
    _publish_run_record(factory, advance, args)
    return exit_code_for(advance)


def _advance_in_session(
    session: Session,
    args: RunAdvanceArgs,
    *,
    owner_id: str,
    now: datetime | None,
    executor: StageExecutor | None,
    revision_of: RevisionResolver | None,
    gate_facts: FactsProvider | None,
    release_facts: ReleaseFactsProvider | None,
) -> RunAdvance:
    """Resolve the run and its change snapshot, then advance one stage."""
    store = RunStore(session)
    run_id, change = _resolve_run(session, store, args, revision_of=revision_of)
    return advance_run(
        store=store,
        change=change,
        run_id=run_id,
        owner_id=owner_id,
        executor=executor,
        revision_of=revision_of,
        gate_facts=gate_facts,
        release_facts=release_facts,
        now=now,
    )


def _resolve_run(
    session: Session,
    store: RunStore,
    args: RunAdvanceArgs,
    *,
    revision_of: RevisionResolver | None = None,
) -> tuple[str, Change]:
    """Run id and change snapshot of the command; raises :class:`InvalidRunnerInput`.

    The run id is derived from the change snapshot (``generated_run_id``), so a
    repeated call for the same snapshot advances the same run. The first stage
    row, though, is keyed by the SCM-derived revision when the composition root
    supplied a resolver (ADR-006 p.4): the agent executor mints its workspace at
    that revision, and a snapshot digest — not a git object — would block the
    stage at the workspace boundary on every attempt.
    """
    changes = ChangeRepository(session)
    if args.change_id is not None:
        change = changes.get(args.change_id)
        if change is None:
            raise InvalidRunnerInput(f"unknown change {args.change_id!r}")
        initial_stage_revision = (
            revision_of(change, route_profile(DEFAULT_RUN_ROUTE).initial_stage)
            if revision_of is not None
            else None
        )
        created = store.create_run(
            change_id=change.id,
            route=DEFAULT_RUN_ROUTE,
            provider=change.product.provider,
            budget=BudgetSnapshot(),
            input_revision=store.stage_input_revision(change),
            initial_stage_revision=initial_stage_revision,
        )
        return created.id, change
    if args.run_id is None:
        raise InvalidRunnerInput("exactly one of --change-id / --run-id is required")
    run = store.load(args.run_id)
    if run is None:
        raise InvalidRunnerInput(f"unknown run {args.run_id!r}")
    change = changes.get(run.change_id)
    if change is None:
        raise InvalidRunnerInput(
            f"run {run.id!r} references change {run.change_id!r}, which is not in intake"
        )
    return run.id, change


def _publish_run_record(
    factory: sessionmaker[Session], advance: RunAdvance, args: RunAdvanceArgs
) -> None:
    """Publish the run record after a terminal advance (S4, ADR-015 p.4) — best-effort.

    Only a run that reached a terminal status is indexed: ``completed``
    (``succeeded``) and ``failed``; the intermediate outcomes advance/waiting/
    blocked/replayed are still in flight and are published when they land.
    The record is built from the committed state (a fresh read transaction —
    the advance's transaction is already closed) and written into the
    ``dark-factory-runs`` checkout idempotently: the same record publishing
    again is ``UNCHANGED`` (T-061). The runs root comes from ``--runs-root``,
    falling back to ``DARK_FACTORY_RUNS_ROOT``; without either the publication
    is skipped silently. A failure prints one warning line on stderr and never
    changes the exit code: the advance itself succeeded and is reported as it
    is (ADR-009 hygiene — the database URL and raw provider texts are never
    echoed).
    """
    if advance.outcome not in (RunAdvanceOutcome.COMPLETED, RunAdvanceOutcome.FAILED):
        return
    root = (args.runs_root or os.environ.get(RUNS_ROOT_ENV_VAR, "")).strip()
    if not root:
        return
    try:
        record = _build_run_record(factory, advance)
        RunRecordStore(Path(root)).publish(record)
    except SQLAlchemyError:
        print(
            "factory run advance: the run record was not published:"
            " the state store is not reachable",
            file=sys.stderr,
        )
    except (OSError, ValueError) as exc:
        # Domain record errors (RunRecordError, RunRecordStoreError, validation)
        # are operator-actionable and safe to echo; OSError names its reason.
        reason = exc.strerror or exc if isinstance(exc, OSError) else exc
        print(f"factory run advance: the run record was not published: {reason}", file=sys.stderr)


def _build_run_record(factory: sessionmaker[Session], advance: RunAdvance) -> RunRecord:
    """Rebuild the run record from the committed state the advance left behind.

    The run and its stage results are read back from the store, the change
    snapshot from intake, and the manifest from the environment (ADR-015 p.5
    — exact revisions, never ``latest``); the release section of the terminal
    result, when the advance carried one, becomes the record's release
    section. Raises :class:`RunRecordError` when the manifest cannot resolve
    its commits — the caller warns and skips.
    """
    with session_scope(factory) as session:
        store = RunStore(session)
        run = store.load(advance.result.run_id)
        if run is None:
            raise RunRecordError(f"unknown run {advance.result.run_id!r}")
        change = ChangeRepository(session).get(run.change_id)
        if change is None:
            raise RunRecordError(
                f"run {run.id!r} references change {run.change_id!r}, which is not in intake"
            )
        history = store.load_history(run.id)
    return RunRecord(
        manifest=collect_run_manifest(os.environ),
        change=change,
        run=run,
        stage_results=history,
        release=advance.result.release,
    )


def _show_status(factory: sessionmaker[Session], args: RunStatusArgs) -> int:
    """Read the persisted run and emit its status and stages (contract cli.md)."""
    try:
        with session_scope(factory) as session:
            run = RunStore(session).load(args.run_id)
    except SQLAlchemyError:
        return _unreachable("run status", args.json_output)
    if run is None:
        return _report(
            "run status",
            "invalid_input",
            f"unknown run {args.run_id!r}",
            EXIT_INVALID_INPUT,
            args.json_output,
        )
    print(render_status_json(run) if args.json_output else render_status_text(run))
    return EXIT_OK


def _unreachable(command: str, json_output: bool) -> int:
    """Report an unreachable or misconfigured state store (exit 2, ADR-009 hygiene)."""
    return _report(
        command,
        "invalid_input",
        "the state store is not reachable or misconfigured",
        EXIT_INVALID_INPUT,
        json_output,
    )


def _report(command: str, tag: str, message: str, code: int, json_output: bool) -> int:
    """Emit one error report: JSON payload on stdout, or a line on stderr."""
    if json_output:
        print(json.dumps({"error": tag, "detail": message}))
    else:
        print(f"factory {command}: {message}", file=sys.stderr)
    return code
