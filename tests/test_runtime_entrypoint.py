"""Tests of the process entry point and its lazy composition (T-092, ADR-025).

``factory`` points at ``dark_factory.runtime.entrypoint:main``: the composition
root binds the assembled runtime into the commands whose working path consumes it
— ``run advance`` (stage executor and revision resolver) and ``api serve`` (the CI
stage switchboard, T059) — and leaves every other command on the core path. These
tests hold that split: the runtime is assembled and closed for exactly those
commands, the bindings reach their consumer, and a command without seams behaves
as before.
"""

from collections.abc import Mapping, Sequence
from typing import Any

import pytest

import dark_factory.cli.api as api_module
import dark_factory.cli.runner as runner_module
import dark_factory.runtime.entrypoint as entrypoint_module
from dark_factory.changes.enums import Stage
from dark_factory.changes.run import Change, ChangeRun, StageResult
from dark_factory.cli.main import EXIT_INVALID_INPUT, EXIT_OK, ApiServeArgs, RunAdvanceArgs
from dark_factory.cli.main import main as cli_main
from dark_factory.orchestration.runner import FactsProvider, RevisionResolver, StageExecutor
from dark_factory.orchestration.stages.gates import GateObservation
from dark_factory.ports import (
    BaselineBootstrapResult,
    MirrorRef,
    RepositoryRef,
    RepositoryValidation,
)


class ExecutorSentinel:
    """Identity sentinel of the assembled stage executor."""

    def __call__(self, context: object) -> StageResult:
        raise AssertionError("the sentinel executor must not be called by the entry point")


class ResolverSentinel:
    """Identity sentinel of the assembled revision resolver."""

    def __call__(self, change: object, stage: object) -> str:
        raise AssertionError("the sentinel resolver must not be called by the entry point")


class FactsSentinel:
    """Identity sentinel of the assembled gate-facts provider."""

    def __call__(self, run: ChangeRun, stage: Stage, change: Change) -> GateObservation | None:
        raise AssertionError("the sentinel provider must not be called by the entry point")


class CiTogglesSentinel:
    """Identity sentinel of the assembled CI stage toggle port (T059)."""

    async def values(self) -> Mapping[str, str]:
        raise AssertionError("the sentinel toggle port must not be called by the entry point")

    async def set_value(self, variable: str, value: str | None) -> None:
        raise AssertionError("the sentinel toggle port must not be called by the entry point")


class ProvisioningSentinel:
    """Identity sentinel of the assembled repository-provisioning port (T066)."""

    async def validate(self, repository: RepositoryRef, /) -> RepositoryValidation:
        raise AssertionError("the sentinel provisioning port must not be called by the entry point")

    async def ensure_mirror(
        self, repository: RepositoryRef, /, *, idempotency_key: str
    ) -> MirrorRef:
        raise AssertionError("the sentinel provisioning port must not be called by the entry point")

    async def bootstrap_baseline(
        self, repository: RepositoryRef, /, *, packs: Sequence[str], idempotency_key: str
    ) -> BaselineBootstrapResult:
        raise AssertionError("the sentinel provisioning port must not be called by the entry point")


class FakeRuntime:
    """Stand-in runtime: records how the entry point binds and releases it."""

    def __init__(self) -> None:
        self.executor = ExecutorSentinel()
        self.resolver = ResolverSentinel()
        self.facts = FactsSentinel()
        self.ci_stage_toggles: Any = CiTogglesSentinel()
        self.ci_repository: Any = "small/pilot"
        self.provisioning: Any = ProvisioningSentinel()
        self.executor_calls = 0
        self.revision_calls = 0
        self.facts_calls = 0
        self.close_calls = 0

    def agent_stage_executor(self) -> StageExecutor:
        self.executor_calls += 1
        return self.executor

    def release_stage_executor(
        self, *, expected_digest: str | None = None, inner: Any = None
    ) -> Any:
        self.release_calls = getattr(self, "release_calls", 0) + 1
        self.release_expected_digest = expected_digest
        self.release_inner = inner
        return None

    def revision_of(self) -> RevisionResolver:
        self.revision_calls += 1
        return self.resolver

    def facts_provider(self) -> FactsProvider:
        self.facts_calls += 1
        return self.facts

    async def aclose(self) -> None:
        self.close_calls += 1


class CliCall:
    """One recorded call of the CLI, with the seams it received."""

    def __init__(self, argv: Sequence[str] | None, kwargs: dict[str, Any]) -> None:
        self.argv = argv
        self.kwargs = kwargs


def _record_cli(monkeypatch: pytest.MonkeyPatch, code: int) -> list[CliCall]:
    """Replace the CLI callable of the entry point with a spy returning ``code``."""
    calls: list[CliCall] = []

    def _spy(argv: Sequence[str] | None = None, **kwargs: Any) -> int:
        calls.append(CliCall(argv, kwargs))
        return code

    monkeypatch.setattr(entrypoint_module, "cli_main", _spy)
    return calls


def _stub_runtime(monkeypatch: pytest.MonkeyPatch, runtime: FakeRuntime) -> list[int]:
    """Replace ``build_runtime`` with a recorder that returns ``runtime``."""
    built: list[int] = []

    def _build() -> FakeRuntime:
        built.append(1)
        return runtime

    monkeypatch.setattr(entrypoint_module, "build_runtime", _build)
    return built


def _forbid_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make assembling a runtime fail loudly: no other command may do it."""

    def _build() -> object:
        raise AssertionError("the runtime must not be assembled for this command")

    monkeypatch.setattr(entrypoint_module, "build_runtime", _build)


# --- run advance: assemble, bind, release ---------------------------------


def test_run_advance_assembles_the_runtime_and_passes_the_bindings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = FakeRuntime()
    built = _stub_runtime(monkeypatch, runtime)
    calls = _record_cli(monkeypatch, EXIT_OK)

    code = entrypoint_module.main(["run", "advance", "--change-id", "chg-001"])

    assert code == EXIT_OK
    assert built == [1]
    assert runtime.executor_calls == 1
    assert runtime.revision_calls == 1
    assert runtime.facts_calls == 1
    assert len(calls) == 1
    # The same argv is handed to the CLI (it parses it again as the authority on
    # the command tree), and the seams are the assembled bindings themselves.
    assert calls[0].argv == ["run", "advance", "--change-id", "chg-001"]
    assert calls[0].kwargs == {
        "executor": runtime.executor,
        "revision_of": runtime.resolver,
        "gate_facts": runtime.facts,
    }


def test_run_advance_releases_the_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = FakeRuntime()
    _stub_runtime(monkeypatch, runtime)
    _record_cli(monkeypatch, EXIT_OK)

    entrypoint_module.main(["run", "advance", "--change-id", "chg-001"])

    assert runtime.close_calls == 1


def test_run_advance_releases_the_runtime_even_when_the_command_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = FakeRuntime()
    _stub_runtime(monkeypatch, runtime)

    def _failing_cli(argv: Sequence[str] | None = None, **kwargs: Any) -> int:
        raise RuntimeError("the command blew up")

    monkeypatch.setattr(entrypoint_module, "cli_main", _failing_cli)

    with pytest.raises(RuntimeError, match="blew up"):
        entrypoint_module.main(["run", "advance", "--change-id", "chg-001"])

    assert runtime.close_calls == 1


def test_the_returned_exit_code_of_the_command_is_propagated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = FakeRuntime()
    _stub_runtime(monkeypatch, runtime)
    _record_cli(monkeypatch, EXIT_OK)

    assert entrypoint_module.main(["run", "advance", "--run-id", "run-001"]) == EXIT_OK
    assert runtime.close_calls == 1


# --- every other command stays on the core path ---------------------------


def test_a_command_that_needs_no_binding_does_not_assemble_the_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ``stage resume`` is a stub: it reads no environment and touches no store, so
    # the entry point must not assemble anything for it — and must pass no seams.
    _forbid_runtime(monkeypatch)
    calls = _record_cli(monkeypatch, EXIT_INVALID_INPUT)
    argv = ["stage", "resume", "--run-id", "run-001", "--next-action", "wa"]

    code = entrypoint_module.main(argv)

    assert code == EXIT_INVALID_INPUT
    assert len(calls) == 1
    assert calls[0].argv == argv
    assert calls[0].kwargs == {}


def test_run_status_does_not_assemble_the_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    # ``run status`` is in the same module as ``run advance`` but takes no seam:
    # it reads the run back and must not drag the composition in.
    _forbid_runtime(monkeypatch)
    calls = _record_cli(monkeypatch, EXIT_OK)

    assert entrypoint_module.main(["run", "status", "--run-id", "run-001"]) == EXIT_OK
    assert calls[0].kwargs == {}


def test_a_parse_error_does_not_assemble_the_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    # argparse is the CLI's, and it rejects an unknown command with exit code 2
    # before the entry point has decided anything. The second argv is recognized
    # as ``run advance`` but invalid (no target) — still no assembly.
    _forbid_runtime(monkeypatch)

    for argv in (["bogus"], ["run", "advance"]):
        with pytest.raises(SystemExit) as exit_info:
            entrypoint_module.main(argv)
        assert exit_info.value.code == EXIT_INVALID_INPUT


# --- api serve: the CI stage switchboard (T059, ADR-027) ------------------


def test_api_serve_assembles_the_runtime_and_passes_the_ci_bindings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = FakeRuntime()
    built = _stub_runtime(monkeypatch, runtime)
    calls = _record_cli(monkeypatch, EXIT_OK)
    argv = ["api", "serve", "--host", "0.0.0.0", "--port", "8000"]

    code = entrypoint_module.main(argv)

    assert code == EXIT_OK
    assert built == [1]
    assert runtime.executor_calls == 0, "api serve consumes no stage executor"
    assert runtime.revision_calls == 0, "api serve consumes no revision resolver"
    assert len(calls) == 1
    assert calls[0].argv == argv
    assert calls[0].kwargs == {
        "ci_toggles": runtime.ci_stage_toggles,
        "ci_repository": runtime.ci_repository,
        "provisioning": runtime.provisioning,
    }
    assert runtime.close_calls == 1, "the assembled adapters are released"


def test_api_serve_without_github_configuration_passes_absent_bindings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A contour without GitHub credentials still serves the stage catalog: the
    # absence must travel as None, never as an invented port (fail-closed).
    runtime = FakeRuntime()
    runtime.ci_stage_toggles = None
    runtime.ci_repository = None
    runtime.provisioning = None
    _stub_runtime(monkeypatch, runtime)
    calls = _record_cli(monkeypatch, EXIT_OK)

    code = entrypoint_module.main(["api", "serve", "--host", "127.0.0.1", "--port", "8000"])

    assert code == EXIT_OK
    assert calls[0].kwargs == {
        "ci_toggles": None,
        "ci_repository": None,
        "provisioning": None,
    }
    assert runtime.close_calls == 1


def test_api_serve_releases_the_runtime_even_when_the_command_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = FakeRuntime()
    _stub_runtime(monkeypatch, runtime)

    def _failing_cli(argv: Sequence[str] | None = None, **kwargs: Any) -> int:
        raise RuntimeError("the server blew up")

    monkeypatch.setattr(entrypoint_module, "cli_main", _failing_cli)

    with pytest.raises(RuntimeError, match="blew up"):
        entrypoint_module.main(["api", "serve", "--host", "127.0.0.1", "--port", "8000"])

    assert runtime.close_calls == 1


def test_cli_main_forwards_the_api_seams_to_the_api_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def _fake_serve(args: ApiServeArgs, **kwargs: Any) -> int:
        seen["args"] = args
        seen.update(kwargs)
        return EXIT_OK

    monkeypatch.setattr(api_module, "run_api_serve_command", _fake_serve)
    toggles = CiTogglesSentinel()
    provisioning = ProvisioningSentinel()

    code = cli_main(
        ["api", "serve", "--host", "127.0.0.1", "--port", "8000"],
        ci_toggles=toggles,
        ci_repository="small/pilot",
        provisioning=provisioning,
    )

    assert code == EXIT_OK
    assert seen["args"] == ApiServeArgs(host="127.0.0.1", port=8000)
    assert seen["ci_toggles"] is toggles
    assert seen["ci_repository"] == "small/pilot"
    assert seen["provisioning"] is provisioning


# --- the CLI seam itself --------------------------------------------------


def test_cli_main_forwards_the_seams_to_the_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def _fake_command(args: RunAdvanceArgs, **kwargs: Any) -> int:
        seen["args"] = args
        seen.update(kwargs)
        return EXIT_OK

    monkeypatch.setattr(runner_module, "run_advance_command", _fake_command)
    executor = ExecutorSentinel()
    revision_of = ResolverSentinel()
    gate_facts = FactsSentinel()

    code = cli_main(
        ["run", "advance", "--change-id", "chg-001"],
        executor=executor,
        revision_of=revision_of,
        gate_facts=gate_facts,
    )

    assert code == EXIT_OK
    assert seen["args"] == RunAdvanceArgs(change_id="chg-001", run_id=None, json_output=False)
    assert seen["executor"] is executor
    assert seen["revision_of"] is revision_of
    assert seen["gate_facts"] is gate_facts


def test_cli_main_without_seams_keeps_the_deterministic_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression: the command tree stays usable on its own (``python -m
    # dark_factory.cli``), with both seams absent.
    seen: dict[str, Any] = {}

    def _fake_command(args: RunAdvanceArgs, **kwargs: Any) -> int:
        seen.update(kwargs)
        return EXIT_OK

    monkeypatch.setattr(runner_module, "run_advance_command", _fake_command)

    assert cli_main(["run", "advance", "--change-id", "chg-001"]) == EXIT_OK
    assert seen == {"executor": None, "revision_of": None, "gate_facts": None}
