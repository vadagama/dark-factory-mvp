"""Unit tests of the ``factory run`` CLI commands (T-092, contract cli.md).

The PostgreSQL-backed advance is covered by the integration suite
(``tests/integration/test_runner_advance.py``); these tests hold the argument
contract, the outcome-to-exit-code mapping, the rendering and the error paths,
with the store, the intake repository and the driver stubbed out.
"""

import io
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, cast

import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

import dark_factory.cli.runner as runner_module
from dark_factory.changes.enums import Provider, Role, Route, RunStatus, Stage, StageStatus
from dark_factory.changes.implementation_contract import (
    ContractApproval,
    ImplementationContract,
)
from dark_factory.changes.next_action import WaitForInputAction
from dark_factory.changes.run import Change, ChangeRun, StageResult, StageRun
from dark_factory.changes.usage import BudgetSnapshot
from dark_factory.cli.main import (
    EXIT_BLOCKED,
    EXIT_ERROR,
    EXIT_INVALID_INPUT,
    EXIT_OK,
    EXIT_WAITING,
    RunAdvanceArgs,
    RunStatusArgs,
    main,
)
from dark_factory.cli.release_facts import CliReleaseFactsProvider
from dark_factory.execution.runs.errors import UnsafeRunRecordError
from dark_factory.orchestration.flow import FlowDecision
from dark_factory.orchestration.runner import RunAdvance, RunAdvanceOutcome
from dark_factory.orchestration.stages.gates import GateObservation
from dark_factory.orchestration.state.repositories import ContractConflictError, LeaseLostError
from tests.changes_factories import make_change, make_contract, make_manifest, make_run

NOW = datetime(2026, 9, 16, 12, 0, 0, tzinfo=UTC)
RUN_ID = "run-001"
CHANGE_ID = "chg-001"
REVISION = "rev-1"


class StubSession:
    """Minimal session: ``session_scope`` only commits, rolls back and closes."""

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


class StubSessionFactory:
    """Callable session factory: ``session_scope`` asks it for a session."""

    def __call__(self) -> StubSession:
        return StubSession()


def _factory() -> sessionmaker[Session]:
    return cast("sessionmaker[Session]", StubSessionFactory())


def _change() -> Change:
    return make_change()


class StubChanges:
    """Intake stand-in that knows exactly the fixture change."""

    def __init__(self, session: StubSession) -> None:
        self.session = session

    def get(self, change_id: str) -> Change | None:
        return _change() if change_id == CHANGE_ID else None


class StubStore:
    """Store stand-in for the CLI: resolving the run and its change needs three methods."""

    last: ClassVar["StubStore | None"] = None

    def __init__(self, session: StubSession) -> None:
        self.session = session
        self.run = make_run()
        self.run.stages.append(
            StageRun(
                id=f"{RUN_ID}:specification:1",
                stage=Stage.SPECIFICATION,
                status=StageStatus.PENDING,
                input_revision=REVISION,
            )
        )
        self.created: list[dict[str, Any]] = []
        self.attached: list[tuple[str, ImplementationContract]] = []
        self.attach_error: Exception | None = None
        StubStore.last = self

    @staticmethod
    def stage_input_revision(change: Change) -> str:
        return REVISION

    def load(self, execution_id: str) -> ChangeRun | None:
        return self.run if execution_id == self.run.id else None

    def load_history(self, execution_id: str) -> list[StageResult]:
        return []

    def create_run(self, **kwargs: Any) -> ChangeRun:
        self.created.append(kwargs)
        return self.run

    def attach_contract(self, execution_id: str, contract: ImplementationContract) -> ChangeRun:
        self.attached.append((execution_id, contract))
        if self.attach_error is not None:
            raise self.attach_error
        return self.run


def _advance_for(outcome: RunAdvanceOutcome) -> RunAdvance:
    """A fabricated driver result, so the CLI contract can be tested without the driver.

    A ``replayed`` outcome carries the committed result and no decision: the flow
    was not consulted and nothing was written.
    """
    action = WaitForInputAction(reason="waiting for a human")
    result = StageResult(
        stage=Stage.SPECIFICATION,
        run_id=RUN_ID,
        change_id=CHANGE_ID,
        input_revision=REVISION,
        status=StageStatus.WAITING,
        next_action=action,
        produced_at=NOW,
    )
    decision = FlowDecision(
        stage=Stage.SPECIFICATION,
        action=action,
        stage_status=StageStatus.WAITING,
        run_status=RunStatus.WAITING,
        next_stage=None,
    )
    return RunAdvance(
        outcome=outcome,
        stage=Stage.SPECIFICATION,
        result=result,
        decision=None if outcome is RunAdvanceOutcome.REPLAYED else decision,
    )


def _stub(monkeypatch: pytest.MonkeyPatch, outcome: RunAdvanceOutcome) -> None:
    """Stub the durable seam: store, intake and driver (and a hermetic runs root)."""
    captured: list[RunAdvance] = [_advance_for(outcome)]

    def fake_advance(**kwargs: Any) -> RunAdvance:
        return captured[0]

    monkeypatch.setattr(runner_module, "RunStore", StubStore)
    monkeypatch.setattr(runner_module, "ChangeRepository", StubChanges)
    monkeypatch.setattr(runner_module, "advance_run", fake_advance)
    monkeypatch.delenv("DARK_FACTORY_RUNS_ROOT", raising=False)


def _last_store() -> StubStore:
    store = StubStore.last
    assert store is not None
    return store


# --- seams -----------------------------------------------------------------


class _FactsSentinel:
    """Identity sentinel of a facts provider (the CLI must never call it)."""

    def __call__(self, run: ChangeRun, stage: Stage, change: Change) -> GateObservation | None:
        raise AssertionError("the CLI must not consult the facts provider itself")


def test_run_advance_hands_the_gate_facts_to_the_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    # The gate-facts seam of ``run advance`` is a value passed through to the
    # driver (T-092 S3); the CLI itself never consults it.
    facts = _FactsSentinel()
    seen: dict[str, Any] = {}

    def _recording_advance(**kwargs: Any) -> RunAdvance:
        seen.update(kwargs)
        return _advance_for(RunAdvanceOutcome.WAITING)

    _stub(monkeypatch, RunAdvanceOutcome.WAITING)
    monkeypatch.setattr(runner_module, "advance_run", _recording_advance)

    code = runner_module.run_advance_command(
        RunAdvanceArgs(change_id=CHANGE_ID, run_id=None, json_output=False),
        session_factory=_factory(),
        owner_id="test-owner",
        gate_facts=facts,
    )

    assert code == EXIT_WAITING
    assert seen["gate_facts"] is facts


# --- exit codes -----------------------------------------------------------


@pytest.mark.parametrize(
    ("outcome", "code"),
    [
        (RunAdvanceOutcome.ADVANCED, EXIT_OK),
        (RunAdvanceOutcome.COMPLETED, EXIT_OK),
        (RunAdvanceOutcome.WAITING, EXIT_WAITING),
        (RunAdvanceOutcome.BLOCKED, EXIT_BLOCKED),
        (RunAdvanceOutcome.FAILED, EXIT_ERROR),
        # A replay has no outcome of its own: the committed result is
        # authoritative, so its status picks the code (waiting -> 10 here).
        (RunAdvanceOutcome.REPLAYED, EXIT_WAITING),
    ],
)
def test_run_advance_maps_the_outcome_to_the_contract_exit_code(
    monkeypatch: pytest.MonkeyPatch, outcome: RunAdvanceOutcome, code: int
) -> None:
    _stub(monkeypatch, outcome)

    executed = runner_module.run_advance_command(
        RunAdvanceArgs(change_id=CHANGE_ID, run_id=None, json_output=False),
        session_factory=_factory(),
        owner_id="test-owner",
    )

    assert executed == code


# --- creating and resolving the run --------------------------------------


def test_run_advance_creates_the_run_from_the_change_snapshot(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _stub(monkeypatch, RunAdvanceOutcome.WAITING)

    code = runner_module.run_advance_command(
        RunAdvanceArgs(change_id=CHANGE_ID, run_id=None, json_output=False),
        session_factory=_factory(),
        owner_id="test-owner",
    )

    assert code == EXIT_WAITING
    assert _last_store().created == [
        {
            "change_id": CHANGE_ID,
            "route": Route.STANDARD,
            "provider": Provider.GITHUB,
            "budget": BudgetSnapshot(),
            "input_revision": REVISION,
            "initial_stage_revision": None,
            "implementation_contract": None,
        }
    ]
    assert "run run-001: waiting" in capsys.readouterr().out


def test_run_advance_keys_the_created_run_stage_by_the_resolver_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # With the composition root's resolver the run is still identified by the
    # change snapshot, but its first stage is keyed by the SCM revision the
    # executor will mint its workspace at (ADR-006 p.4).
    _stub(monkeypatch, RunAdvanceOutcome.WAITING)

    def revision_of(change: Change, stage: Stage) -> str:
        return "scm-rev-1"

    code = runner_module.run_advance_command(
        RunAdvanceArgs(change_id=CHANGE_ID, run_id=None, json_output=False),
        session_factory=_factory(),
        owner_id="test-owner",
        revision_of=revision_of,
    )

    assert code == EXIT_WAITING
    created = _last_store().created[0]
    assert created["input_revision"] == REVISION
    assert created["initial_stage_revision"] == "scm-rev-1"


def test_run_advance_rejects_an_unknown_change(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _stub(monkeypatch, RunAdvanceOutcome.WAITING)

    code = runner_module.run_advance_command(
        RunAdvanceArgs(change_id="chg-missing", run_id=None, json_output=False),
        session_factory=_factory(),
        owner_id="test-owner",
    )

    assert code == EXIT_INVALID_INPUT
    assert "unknown change" in capsys.readouterr().err


def test_run_advance_rejects_an_unknown_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _stub(monkeypatch, RunAdvanceOutcome.WAITING)

    code = runner_module.run_advance_command(
        RunAdvanceArgs(change_id=None, run_id="run-missing", json_output=False),
        session_factory=_factory(),
        owner_id="test-owner",
    )

    assert code == EXIT_INVALID_INPUT
    assert "unknown run" in capsys.readouterr().err


def test_run_advance_reports_a_write_the_store_refused(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A lost lease or a stale revision is a store error, not a stage result: the
    # command reports it instead of raising a traceback.
    def _refusing_advance(**kwargs: Any) -> RunAdvance:
        raise LeaseLostError("lease execution/run-001 is held by another owner")

    _stub(monkeypatch, RunAdvanceOutcome.WAITING)
    monkeypatch.setattr(runner_module, "advance_run", _refusing_advance)

    code = runner_module.run_advance_command(
        RunAdvanceArgs(change_id=CHANGE_ID, run_id=None, json_output=False),
        session_factory=_factory(),
        owner_id="test-owner",
    )

    assert code == EXIT_INVALID_INPUT
    assert "refused the advance" in capsys.readouterr().err


def test_run_advance_refuses_an_unreachable_state_store(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class _UnreachableEngine:
        def connect(self) -> None:
            raise SQLAlchemyError("connection refused")

        def dispose(self) -> None:
            return None

    def _failing_engine(url: str) -> _UnreachableEngine:
        return _UnreachableEngine()

    monkeypatch.setattr(runner_module, "create_state_engine", _failing_engine)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:secret@db.example/dark_factory")

    code = runner_module.run_advance_command(
        RunAdvanceArgs(change_id=CHANGE_ID, run_id=None, json_output=True), owner_id="test-owner"
    )

    assert code == EXIT_INVALID_INPUT
    captured = capsys.readouterr()
    # The URL and the exception text may carry credentials — never echoed (ADR-009).
    assert "secret" not in captured.out
    assert "secret" not in captured.err
    assert "not reachable" in captured.out


# --- rendering ------------------------------------------------------------


def test_run_advance_json_reports_the_outcome(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _stub(monkeypatch, RunAdvanceOutcome.WAITING)

    code = runner_module.run_advance_command(
        RunAdvanceArgs(change_id=CHANGE_ID, run_id=None, json_output=True),
        session_factory=_factory(),
        owner_id="test-owner",
    )

    assert code == EXIT_WAITING
    assert json.loads(capsys.readouterr().out) == {
        "run_id": RUN_ID,
        "change_id": CHANGE_ID,
        "outcome": "waiting",
        "persisted": True,
        "stage": "specification",
        "result_status": "waiting",
        "next_action": "wait_for_input",
        "attempt_number": 1,
        "stage_status": "waiting",
        "run_status": "waiting",
        "next_stage": None,
        "reason": "waiting for a human",
    }


def test_run_advance_json_reports_a_replay_as_nothing_written(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _stub(monkeypatch, RunAdvanceOutcome.REPLAYED)

    code = runner_module.run_advance_command(
        RunAdvanceArgs(change_id=CHANGE_ID, run_id=None, json_output=True),
        session_factory=_factory(),
        owner_id="test-owner",
    )

    assert code == EXIT_WAITING
    # The keys are stable; the fields a replay has no decision for are null.
    assert json.loads(capsys.readouterr().out) == {
        "run_id": RUN_ID,
        "change_id": CHANGE_ID,
        "outcome": "replayed",
        "persisted": False,
        "stage": "specification",
        "result_status": "waiting",
        "next_action": "wait_for_input",
        "attempt_number": 1,
        "stage_status": None,
        "run_status": None,
        "next_stage": None,
        "reason": "waiting for a human",
    }


def test_run_advance_text_says_a_replay_wrote_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _stub(monkeypatch, RunAdvanceOutcome.REPLAYED)

    runner_module.run_advance_command(
        RunAdvanceArgs(change_id=CHANGE_ID, run_id=None, json_output=False),
        session_factory=_factory(),
        owner_id="test-owner",
    )

    assert capsys.readouterr().out.strip() == (
        "run run-001: replayed (stage=specification, result_status=waiting,"
        " next_action=wait_for_input; nothing was written)"
    )


def test_run_advance_text_prints_the_wait_reason(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _stub(monkeypatch, RunAdvanceOutcome.WAITING)

    runner_module.run_advance_command(
        RunAdvanceArgs(change_id=CHANGE_ID, run_id=None, json_output=False),
        session_factory=_factory(),
        owner_id="test-owner",
    )

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == (
        "run run-001: waiting (stage=specification, stage_status=waiting, run_status=waiting,"
        " next_action=wait_for_input, next_stage=None)"
    )
    assert lines[1] == "reason: waiting for a human"


# --- run status -----------------------------------------------------------


def test_run_status_renders_the_persisted_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(runner_module, "RunStore", StubStore)

    code = runner_module.run_status_command(
        RunStatusArgs(run_id=RUN_ID, json_output=False), session_factory=_factory()
    )

    assert code == EXIT_OK
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == (
        "run run-001: pending (change_id=chg-001, route=standard, provider=github,"
        " state_revision=1)"
    )
    assert lines[1] == "specification: pending (attempt=1, input_revision=rev-1, state_revision=1)"


def test_run_status_json_is_the_domain_run_document(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(runner_module, "RunStore", StubStore)

    code = runner_module.run_status_command(
        RunStatusArgs(run_id=RUN_ID, json_output=True), session_factory=_factory()
    )

    assert code == EXIT_OK
    assert json.loads(capsys.readouterr().out) == _last_store().run.model_dump(mode="json")


def test_run_status_rejects_an_unknown_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(runner_module, "RunStore", StubStore)

    code = runner_module.run_status_command(
        RunStatusArgs(run_id="run-missing", json_output=False), session_factory=_factory()
    )

    assert code == EXIT_INVALID_INPUT
    assert "unknown run" in capsys.readouterr().err


# --- dispatch and lease identity -----------------------------------------


def test_main_dispatches_run_advance_to_the_runner_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: dict[str, Any] = {}

    def _fake_command(args: RunAdvanceArgs, **kwargs: Any) -> int:
        seen["change_id"] = args.change_id
        print(runner_module.render_advance_json(_advance_for(RunAdvanceOutcome.ADVANCED)))
        return EXIT_OK

    monkeypatch.setattr(runner_module, "run_advance_command", _fake_command)

    assert main(["run", "advance", "--change-id", CHANGE_ID, "--json"]) == EXIT_OK
    assert seen["change_id"] == CHANGE_ID
    assert json.loads(capsys.readouterr().out)["outcome"] == "advanced"


def test_main_dispatches_run_status_to_the_runner_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: dict[str, Any] = {}

    def _fake_command(args: RunStatusArgs, **kwargs: Any) -> int:
        seen["run_id"] = args.run_id
        print(runner_module.render_status_text(make_run()))
        return EXIT_OK

    monkeypatch.setattr(runner_module, "run_status_command", _fake_command)

    assert main(["run", "status", "--run-id", RUN_ID]) == EXIT_OK
    assert seen["run_id"] == RUN_ID
    assert capsys.readouterr().out.startswith("run run-001: pending")


def test_default_owner_id_is_unique_per_process(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DARK_FACTORY_RUN_OWNER_ID", raising=False)

    first = runner_module._default_owner_id()
    second = runner_module._default_owner_id()

    assert first != second
    int(first.rsplit("-", 1)[1], 16)  # the suffix is a uuid hex chunk
    monkeypatch.setenv("DARK_FACTORY_RUN_OWNER_ID", "cronjob-runner")
    assert runner_module._default_owner_id() == "cronjob-runner"


# --- release options and the run-record publication (T-092 S4) ---------------


def test_run_advance_hands_the_release_facts_to_the_driver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The observation options build the value-level release facts provider the
    # driver consumes (T-092 S4); the CLI passes it through like gate_facts.
    seen: dict[str, Any] = {}

    def _recording_advance(**kwargs: Any) -> RunAdvance:
        seen.update(kwargs)
        return _advance_for(RunAdvanceOutcome.WAITING)

    _stub(monkeypatch, RunAdvanceOutcome.WAITING)
    monkeypatch.setattr(runner_module, "advance_run", _recording_advance)

    code = runner_module.run_advance_command(
        RunAdvanceArgs(
            change_id=CHANGE_ID,
            run_id=None,
            json_output=False,
            observed_digest="sha256:abc",
            argo_sync="Synced",
            argo_health="Healthy",
        ),
        session_factory=_factory(),
        owner_id="test-owner",
    )

    assert code == EXIT_WAITING
    release_facts = seen["release_facts"]
    assert isinstance(release_facts, CliReleaseFactsProvider)


def test_run_advance_builds_no_release_facts_without_observation_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def _recording_advance(**kwargs: Any) -> RunAdvance:
        seen.update(kwargs)
        return _advance_for(RunAdvanceOutcome.WAITING)

    _stub(monkeypatch, RunAdvanceOutcome.WAITING)
    monkeypatch.setattr(runner_module, "advance_run", _recording_advance)

    runner_module.run_advance_command(
        RunAdvanceArgs(
            change_id=CHANGE_ID,
            run_id=None,
            json_output=False,
            expected_digest="sha256:abc",  # promotion input only, not an observation
        ),
        session_factory=_factory(),
        owner_id="test-owner",
    )

    assert seen["release_facts"] is None


def test_run_advance_rejects_conflicting_digest_sources(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _stub(monkeypatch, RunAdvanceOutcome.WAITING)

    code = runner_module.run_advance_command(
        RunAdvanceArgs(
            change_id=CHANGE_ID,
            run_id=None,
            json_output=False,
            expected_digest="sha256:abc",
            digest_json="/tmp/digest.json",
        ),
        session_factory=_factory(),
        owner_id="test-owner",
    )

    assert code == EXIT_INVALID_INPUT
    assert "mutually exclusive" in capsys.readouterr().err


def test_run_advance_rejects_inconsistent_smoke_options(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _stub(monkeypatch, RunAdvanceOutcome.WAITING)

    code = runner_module.run_advance_command(
        RunAdvanceArgs(
            change_id=CHANGE_ID,
            run_id=None,
            json_output=False,
            smoke_digest_header="X-Version",  # no --smoke-url: the base of the set
        ),
        session_factory=_factory(),
        owner_id="test-owner",
    )

    assert code == EXIT_INVALID_INPUT
    assert "requires --smoke-digest-url" in capsys.readouterr().err


def test_run_advance_publishes_the_run_record_after_a_completed_advance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    _stub(monkeypatch, RunAdvanceOutcome.COMPLETED)
    monkeypatch.setenv("DARK_FACTORY_RUNS_ROOT", str(tmp_path))
    monkeypatch.setattr(runner_module, "collect_run_manifest", lambda env: make_manifest())

    code = runner_module.run_advance_command(
        RunAdvanceArgs(change_id=CHANGE_ID, run_id=None, json_output=False),
        session_factory=_factory(),
        owner_id="test-owner",
    )

    assert code == EXIT_OK
    # The record landed in the deterministic layout of the runs repository.
    published = list(tmp_path.rglob("snapshot.json"))
    assert len(published) == 1


def test_run_advance_does_not_publish_without_a_runs_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    _stub(monkeypatch, RunAdvanceOutcome.COMPLETED)  # _stub removed the env var

    code = runner_module.run_advance_command(
        RunAdvanceArgs(change_id=CHANGE_ID, run_id=None, json_output=False),
        session_factory=_factory(),
        owner_id="test-owner",
    )

    assert code == EXIT_OK
    assert not any(tmp_path.iterdir()) if tmp_path.exists() else True


def test_run_advance_skips_the_publication_for_a_waiting_advance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    # Only a terminal run is indexed: waiting/blocked/advanced runs are in flight.
    _stub(monkeypatch, RunAdvanceOutcome.WAITING)
    monkeypatch.setenv("DARK_FACTORY_RUNS_ROOT", str(tmp_path))
    monkeypatch.setattr(runner_module, "collect_run_manifest", lambda env: make_manifest())

    code = runner_module.run_advance_command(
        RunAdvanceArgs(change_id=CHANGE_ID, run_id=None, json_output=False),
        session_factory=_factory(),
        owner_id="test-owner",
    )

    assert code == EXIT_WAITING
    assert not any(tmp_path.iterdir()) if tmp_path.exists() else True


def test_run_advance_warns_and_keeps_the_exit_code_when_publication_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    _stub(monkeypatch, RunAdvanceOutcome.COMPLETED)
    monkeypatch.setenv("DARK_FACTORY_RUNS_ROOT", str(tmp_path))
    monkeypatch.setattr(runner_module, "collect_run_manifest", lambda env: make_manifest())

    class ExplodingStore:
        def __init__(self, root: Any) -> None: ...

        def publish(self, record: Any) -> Any:
            raise UnsafeRunRecordError("record carries a secret")

    monkeypatch.setattr(runner_module, "RunRecordStore", ExplodingStore)

    code = runner_module.run_advance_command(
        RunAdvanceArgs(change_id=CHANGE_ID, run_id=None, json_output=False),
        session_factory=_factory(),
        owner_id="test-owner",
    )

    assert code == EXIT_OK  # the advance succeeded; the publication is best-effort
    assert "was not published" in capsys.readouterr().err


# --- implementation contract (T-016, ADR-018 p.3) ---------------------------


def _contract_file(tmp_path: Path, contract: ImplementationContract) -> Path:
    path = tmp_path / "contract.json"
    path.write_text(contract.model_dump_json(), encoding="utf-8")
    return path


def test_run_advance_attaches_the_contract_to_an_existing_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub(monkeypatch, RunAdvanceOutcome.WAITING)
    contract = make_contract()

    code = runner_module.run_advance_command(
        RunAdvanceArgs(
            change_id=None,
            run_id=RUN_ID,
            json_output=False,
            contract_json=str(_contract_file(tmp_path, contract)),
        ),
        session_factory=_factory(),
        owner_id="test-owner",
    )

    assert code == EXIT_WAITING
    assert _last_store().attached == [(RUN_ID, contract)]


def test_run_advance_attaches_the_contract_to_a_fresh_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub(monkeypatch, RunAdvanceOutcome.WAITING)
    contract = make_contract()

    code = runner_module.run_advance_command(
        RunAdvanceArgs(
            change_id=CHANGE_ID,
            run_id=None,
            json_output=False,
            contract_json=str(_contract_file(tmp_path, contract)),
        ),
        session_factory=_factory(),
        owner_id="test-owner",
    )

    assert code == EXIT_WAITING
    store = _last_store()
    assert store.created[0]["implementation_contract"] == contract
    assert store.attached == [(RUN_ID, contract)]


def test_run_advance_approves_the_contract_before_attaching_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub(monkeypatch, RunAdvanceOutcome.WAITING)
    contract = make_contract().model_copy(update={"approval": None})

    code = runner_module.run_advance_command(
        RunAdvanceArgs(
            change_id=None,
            run_id=RUN_ID,
            json_output=False,
            contract_json=str(_contract_file(tmp_path, contract)),
            approve_contract=True,
        ),
        session_factory=_factory(),
        owner_id="test-owner",
        now=NOW,
    )

    assert code == EXIT_WAITING
    run_id, attached = _last_store().attached[0]
    assert run_id == RUN_ID
    assert attached.approval == ContractApproval(approved_by=Role.PRODUCT, decided_at=NOW)


def test_run_advance_reads_the_contract_from_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub(monkeypatch, RunAdvanceOutcome.WAITING)
    contract = make_contract()
    monkeypatch.setattr("sys.stdin", io.StringIO(contract.model_dump_json()))

    code = runner_module.run_advance_command(
        RunAdvanceArgs(change_id=None, run_id=RUN_ID, json_output=False, contract_json="-"),
        session_factory=_factory(),
        owner_id="test-owner",
    )

    assert code == EXIT_WAITING
    assert _last_store().attached == [(RUN_ID, contract)]


def test_run_advance_refuses_to_approve_an_already_approved_contract(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _stub(monkeypatch, RunAdvanceOutcome.WAITING)
    # The refusal must happen before the advance touches the store.
    instantiations: list[StubSession] = []

    class _RecordingStore(StubStore):
        def __init__(self, session: StubSession) -> None:
            instantiations.append(session)
            super().__init__(session)

    monkeypatch.setattr(runner_module, "RunStore", _RecordingStore)
    contract = make_contract()  # carries an approval already

    code = runner_module.run_advance_command(
        RunAdvanceArgs(
            change_id=None,
            run_id=RUN_ID,
            json_output=False,
            contract_json=str(_contract_file(tmp_path, contract)),
            approve_contract=True,
        ),
        session_factory=_factory(),
        owner_id="test-owner",
    )

    assert code == EXIT_INVALID_INPUT
    assert "--approve-contract is ambiguous" in capsys.readouterr().err
    assert instantiations == []


def test_run_advance_refuses_approve_without_a_contract(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _stub(monkeypatch, RunAdvanceOutcome.WAITING)

    code = runner_module.run_advance_command(
        RunAdvanceArgs(change_id=None, run_id=RUN_ID, json_output=False, approve_contract=True),
        session_factory=_factory(),
        owner_id="test-owner",
    )

    assert code == EXIT_INVALID_INPUT
    assert "--approve-contract requires --contract-json" in capsys.readouterr().err


def test_run_advance_rejects_a_missing_contract_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _stub(monkeypatch, RunAdvanceOutcome.WAITING)

    code = runner_module.run_advance_command(
        RunAdvanceArgs(
            change_id=None,
            run_id=RUN_ID,
            json_output=False,
            contract_json=str(tmp_path / "missing.json"),
        ),
        session_factory=_factory(),
        owner_id="test-owner",
    )

    assert code == EXIT_INVALID_INPUT
    assert "cannot read the contract file" in capsys.readouterr().err


def test_run_advance_rejects_broken_contract_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _stub(monkeypatch, RunAdvanceOutcome.WAITING)
    path = tmp_path / "contract.json"
    path.write_text("{not json", encoding="utf-8")

    code = runner_module.run_advance_command(
        RunAdvanceArgs(change_id=None, run_id=RUN_ID, json_output=False, contract_json=str(path)),
        session_factory=_factory(),
        owner_id="test-owner",
    )

    assert code == EXIT_INVALID_INPUT
    assert "not valid JSON" in capsys.readouterr().err


def test_run_advance_rejects_a_contract_off_the_schema(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _stub(monkeypatch, RunAdvanceOutcome.WAITING)
    path = tmp_path / "contract.json"
    path.write_text(json.dumps({"id": "ict-1"}), encoding="utf-8")

    code = runner_module.run_advance_command(
        RunAdvanceArgs(change_id=None, run_id=RUN_ID, json_output=False, contract_json=str(path)),
        session_factory=_factory(),
        owner_id="test-owner",
    )

    assert code == EXIT_INVALID_INPUT
    assert "violates the schema" in capsys.readouterr().err


def test_run_advance_refuses_to_swap_the_contract_of_a_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The store refuses a different contract for a run that already carries one
    # (T-016); the command maps the refusal to invalid input and names the run.
    def _conflicting(
        self: StubStore, execution_id: str, contract: ImplementationContract
    ) -> ChangeRun:
        self.attached.append((execution_id, contract))
        raise ContractConflictError(
            f"run {execution_id!r} already carries a different implementation contract"
        )

    _stub(monkeypatch, RunAdvanceOutcome.WAITING)
    monkeypatch.setattr(StubStore, "attach_contract", _conflicting)
    other = make_contract().model_copy(update={"id": "ict-other"})

    code = runner_module.run_advance_command(
        RunAdvanceArgs(
            change_id=None,
            run_id=RUN_ID,
            json_output=False,
            contract_json=str(_contract_file(tmp_path, other)),
        ),
        session_factory=_factory(),
        owner_id="test-owner",
    )

    assert code == EXIT_INVALID_INPUT
    assert "different implementation contract" in capsys.readouterr().err
