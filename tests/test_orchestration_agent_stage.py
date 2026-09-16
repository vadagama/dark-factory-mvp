"""Tests of the harness-backed stage executor and its role tools (T-092 S2).

The executor is driven end to end over the in-memory fakes: the harness, the
repository, the change requests and the isolated workspace are all fake, so the
whole attempt — profile resolution, workspace, tools, harness call, branch and
change request — is exercised without a network, a database or an LLM.
"""

import asyncio
import hashlib
from collections.abc import Sequence

import pytest

from dark_factory.adapters.fakes import (
    FakeExecution,
    FakeHarness,
    FakeMergeRequests,
    FakeRepository,
    FakeTelemetry,
)
from dark_factory.agents.profiles.manifest import AgentProfile
from dark_factory.agents.profiles.registry import DEVELOP_PROFILE, PRODUCT_PROFILE
from dark_factory.changes.enums import GateStatus, Route, Stage, StageStatus
from dark_factory.changes.next_action import StopAction, WaitForCIAction
from dark_factory.changes.refs import RepositoryRef
from dark_factory.changes.usage import BudgetSnapshot, Usage
from dark_factory.orchestration.stages.agent import (
    STAGE_ROLE,
    AgentStageExecutor,
    ScmRevision,
    branch_name,
)
from dark_factory.orchestration.stages.context import StageContext, build_context
from dark_factory.orchestration.stages.tools import (
    TOOL_NAMES,
    ToolFunction,
    UnknownToolError,
    UnsafeWorkspacePath,
    WorkspaceTools,
    resolve_path,
    tools_for,
)
from dark_factory.ports import (
    AgentResult,
    HarnessPort,
    HealthStatus,
    PortError,
    TaskEnvelope,
    WorkspaceHandle,
    WorkspaceRequest,
)
from tests.changes_factories import make_change

REVISION = "abc123"
RUN_ID = "run-001"
USAGE = Usage(prompt_tokens=3, completion_tokens=5, total_tokens=8)


class RecordingHarnessFactory:
    """Harness factory that records the tools each role was given."""

    def __init__(self, harness: HarnessPort | None = None) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []
        self._harness = harness or FakeHarness()

    def __call__(self, profile: AgentProfile, tools: Sequence[ToolFunction]) -> HarnessPort:
        self.calls.append((profile.role.value, tuple(tool.__name__ for tool in tools)))
        return self._harness


class FailingHarness(HarnessPort):
    """Harness that reports a failed call with usage, like an unreachable endpoint."""

    async def run_stage(self, envelope: TaskEnvelope, /) -> AgentResult:
        return AgentResult(ok=False, output="harness call failed: RuntimeError", usage=USAGE)

    async def health(self, /) -> HealthStatus:
        return HealthStatus(healthy=False, detail="not configured")


class ExplodingRepository(FakeRepository):
    """Repository whose branch read fails with a secret-bearing provider error."""

    async def ensure_branch(
        self,
        repository: RepositoryRef,
        branch: str,
        *,
        from_revision: str,
        idempotency_key: str,
    ) -> str:
        raise RuntimeError("provider failed at https://token@scm.example/api")


class UnreachableRepository(FakeRepository):
    """Repository whose reads fail like a rejected token, not like a missing ref."""

    async def get_revision(self, repository: RepositoryRef, ref: str, /) -> str:
        raise PortError("the provider rejected the installation token")


class RecordingExecution(FakeExecution):
    """Execution fake that records the idempotency key of every write."""

    def __init__(self) -> None:
        super().__init__()
        self.write_keys: list[tuple[str, str]] = []

    async def write_file(
        self, workspace: WorkspaceHandle, path: str, content: bytes, /, *, idempotency_key: str
    ) -> None:
        self.write_keys.append((path, idempotency_key))
        await super().write_file(workspace, path, content, idempotency_key=idempotency_key)


def _context(
    *, stage: Stage = Stage.CONSTRUCTION, input_revision: str | None = REVISION, attempt: int = 1
) -> StageContext:
    return build_context(
        change=make_change(),
        stage=stage,
        route=Route.STANDARD,
        run_id=RUN_ID,
        input_revision=input_revision,
        budget=BudgetSnapshot(),
        attempt_number=attempt,
    )


def _executor(
    *,
    harness: HarnessPort | None = None,
    repository: FakeRepository | None = None,
    merge_requests: FakeMergeRequests | None = None,
    execution: FakeExecution | None = None,
    telemetry: FakeTelemetry | None = None,
    factory: RecordingHarnessFactory | None = None,
) -> tuple[AgentStageExecutor, RecordingHarnessFactory, FakeRepository, FakeMergeRequests]:
    repo = repository or FakeRepository()
    changes = merge_requests or FakeMergeRequests()
    recorder = factory or RecordingHarnessFactory(harness)
    executor = AgentStageExecutor(
        harness_of=recorder,
        repository=repo,
        merge_requests=changes,
        execution=execution or FakeExecution(),
        telemetry=telemetry,
    )
    return executor, recorder, repo, changes


# --- the successful attempt ------------------------------------------------


def test_successful_stage_waits_for_ci_with_a_change_request() -> None:
    executor, recorder, repo, _changes = _executor()
    result = executor(_context())

    assert result.status is StageStatus.WAITING
    assert isinstance(result.next_action, WaitForCIAction)
    assert result.next_action.change_request is not None
    assert result.next_action.change_request.number == 1
    assert result.attempt_number == 1
    assert result.input_revision == REVISION
    assert [artifact.artifact_type for artifact in result.artifacts] == ["change_request"]
    assert result.artifacts[0].revision == REVISION
    assert result.artifacts[0].producer == "develop"
    # The branch exists at the pinned revision the stage started from.
    assert asyncio.run(repo.get_revision(make_change().product, branch_name("chg-001"))) == REVISION
    assert recorder.calls == [("develop", DEVELOP_PROFILE.tools)]


def test_gates_are_reported_pending_not_passed() -> None:
    executor, _, _, _ = _executor()
    context = _context()
    result = executor(context)

    assert result.gate_results
    assert all(item.status is GateStatus.PENDING for item in result.gate_results)
    assert {item.gate for item in result.gate_results} == set(context.required_gates)


def test_usage_of_the_harness_call_reaches_the_result() -> None:
    executor, _, _, _ = _executor()
    result = executor(_context())
    assert result.usage is None  # FakeHarness reports no usage

    failing, _, _, _ = _executor(harness=FailingHarness())
    blocked = failing(_context())
    assert blocked.status is StageStatus.BLOCKED
    assert blocked.usage == USAGE


def test_a_retry_reuses_the_branch_and_the_change_request() -> None:
    executor, _, _, changes = _executor()
    first = executor(_context())
    second = executor(_context(attempt=2))

    assert isinstance(first.next_action, WaitForCIAction)
    assert isinstance(second.next_action, WaitForCIAction)
    assert first.next_action.change_request == second.next_action.change_request
    # One change request, not two: the second attempt found the existing one
    # (FR-011) and the branch ensure is keyed per operation (FR-017).
    assert asyncio.run(changes.find_existing(make_change().product, "chg-001")) is not None


def test_product_stages_bind_the_product_tools() -> None:
    executor, recorder, _, _ = _executor()
    executor(_context(stage=Stage.SPECIFICATION))
    assert recorder.calls == [("product", PRODUCT_PROFILE.tools)]


def test_telemetry_records_one_span_per_attempt() -> None:
    telemetry = FakeTelemetry()
    executor, _, _, _ = _executor(telemetry=telemetry)
    executor(_context())

    spans = telemetry.recorded_spans()
    assert [span.name for span in spans] == ["factory.stage.agent"]
    assert spans[0].attributes["stage"] == "construction"
    assert spans[0].attributes["role"] == "develop"


# --- the honest stops -------------------------------------------------------


def test_stage_without_a_core_profile_is_blocked() -> None:
    # The release stage is owned by ci_cd, which has no core profile yet (ADR-007 p.4).
    executor, recorder, _, _ = _executor()
    result = executor(_context(stage=Stage.RELEASE))

    assert result.status is StageStatus.BLOCKED
    assert isinstance(result.next_action, StopAction)
    assert "ci_cd" in result.next_action.reason
    assert recorder.calls == []


def test_stage_without_a_mapped_skill_is_blocked() -> None:
    # A profile exists for the role but no skill is mapped to the stage: the
    # executor stops instead of running an agent with an empty instruction.
    recorder = RecordingHarnessFactory()
    executor = AgentStageExecutor(
        harness_of=recorder,
        repository=FakeRepository(),
        merge_requests=FakeMergeRequests(),
        execution=FakeExecution(),
        profile_of=lambda role: PRODUCT_PROFILE,
    )
    result = executor(_context(stage=Stage.RELEASE))
    assert result.status is StageStatus.BLOCKED
    assert recorder.calls == []


def test_attempt_without_a_pinned_revision_is_blocked() -> None:
    executor, recorder, _, _ = _executor()
    result = executor(_context(input_revision=None))

    assert result.status is StageStatus.BLOCKED
    assert isinstance(result.next_action, StopAction)
    assert "input revision" in result.next_action.reason
    assert recorder.calls == []


def test_failed_harness_call_blocks_without_leaking_provider_text() -> None:
    executor, _, _, changes = _executor(harness=FailingHarness())
    result = executor(_context())

    assert result.status is StageStatus.BLOCKED
    assert isinstance(result.next_action, StopAction)
    assert "RuntimeError" not in result.next_action.reason  # the reason is written by us
    assert "harness" in result.next_action.reason
    assert asyncio.run(changes.find_existing(make_change().product, "chg-001")) is None


def test_port_failure_blocks_with_the_exception_type_only() -> None:
    executor, _, _, _ = _executor(repository=ExplodingRepository())
    result = executor(_context())

    assert result.status is StageStatus.BLOCKED
    assert isinstance(result.next_action, StopAction)
    assert "RuntimeError" in result.next_action.reason
    assert "token" not in result.next_action.reason
    assert "scm.example" not in result.next_action.reason


# --- role tools over the isolated workspace --------------------------------


def _tools(execution: FakeExecution | None = None) -> tuple[WorkspaceTools, FakeExecution]:
    execution = execution if execution is not None else FakeExecution()
    workspace = asyncio.run(
        execution.prepare_workspace(
            WorkspaceRequest(
                repository=make_change().product, revision=REVISION, change_id="chg-001"
            ),
            idempotency_key="ws-1",
        )
    )
    return WorkspaceTools(execution, workspace), execution


def test_tools_for_resolves_the_profile_names_in_order() -> None:
    tools, _ = _tools()
    assert tools_for(DEVELOP_PROFILE, tools).keys() == set(DEVELOP_PROFILE.tools)
    assert [tool.__name__ for tool in tools.tools_for(DEVELOP_PROFILE)] == list(
        DEVELOP_PROFILE.tools
    )


def test_tools_for_rejects_a_name_without_a_binding() -> None:
    tools, _ = _tools()
    profile = DEVELOP_PROFILE.model_copy(update={"tools": ("frobnicate",)})
    with pytest.raises(UnknownToolError, match="frobnicate"):
        tools.tools_for(profile)


def test_write_then_read_round_trips_a_file() -> None:
    tools, _ = _tools()
    asyncio.run(tools.write_file("src/app.py", "print('hi')\n"))
    assert asyncio.run(tools.read_file("src/app.py")) == "print('hi')\n"


def test_write_file_key_addresses_content_not_length() -> None:
    # Two different payloads of the same length must not collide: a key derived
    # from the byte count would let an adapter that deduplicates by key drop the
    # second write silently (FR-017).
    execution = RecordingExecution()
    tools, _ = _tools(execution)
    asyncio.run(tools.write_file("src/app.py", "v1"))
    asyncio.run(tools.write_file("src/app.py", "v2"))

    (first_path, first_key), (second_path, second_key) = execution.write_keys
    assert first_path == second_path == "src/app.py"
    assert first_key != second_key
    assert asyncio.run(tools.read_file("src/app.py")) == "v2"


def test_write_file_key_is_stable_for_identical_content() -> None:
    # The same path and content always resolve to the same key, so replaying the
    # same write is a true no-op and not a second effect.
    execution = RecordingExecution()
    tools, _ = _tools(execution)
    asyncio.run(tools.write_file("src/app.py", "same"))
    asyncio.run(tools.write_file("src/app.py", "same"))

    first_key = execution.write_keys[0][1]
    assert [key for _path, key in execution.write_keys] == [first_key, first_key]


def test_write_file_key_addresses_the_path_too() -> None:
    # The address is (path, content): content alone would make the same bytes
    # written to two files share one key, and a deduplicating adapter would
    # create only the first file.
    execution = RecordingExecution()
    tools, _ = _tools(execution)
    asyncio.run(tools.write_file("src/a.py", "same"))
    asyncio.run(tools.write_file("src/b.py", "same"))

    keys = [key for _path, key in execution.write_keys]
    assert keys[0] != keys[1]
    assert asyncio.run(tools.read_file("src/b.py")) == "same"


@pytest.mark.parametrize("path", ["../etc/passwd", "/etc/passwd", "~/secrets", "", "src/../../x"])
def test_tools_refuse_paths_outside_the_workspace(path: str) -> None:
    tools, _ = _tools()
    with pytest.raises(UnsafeWorkspacePath):
        asyncio.run(tools.read_file(path))


def test_resolve_path_normalizes_safe_relative_paths() -> None:
    assert resolve_path("./src//app.py") == "src/app.py"


def test_failing_test_command_is_reported_not_raised() -> None:
    tools, execution = _tools()
    execution.seed_failure(("pytest", "-q"), exit_code=1)
    rendered = asyncio.run(tools.run_tests())
    assert "exit_code=1" in rendered
    assert "fake:failed:pytest -q" in rendered


def test_search_repo_reports_no_matches_instead_of_a_failure() -> None:
    tools, execution = _tools()
    execution.seed_failure(("grep", "-rn", "-F", "--", "nothing", "."), exit_code=1)
    assert asyncio.run(tools.search_repo("nothing")) == "no matches"


def test_run_command_requires_an_argv() -> None:
    tools, _ = _tools()
    with pytest.raises(ValueError, match="non-empty argv"):
        asyncio.run(tools.run_command([]))


def test_apply_patch_writes_the_patch_and_runs_git_apply() -> None:
    tools, _execution = _tools()
    rendered = asyncio.run(tools.apply_patch("--- a\n+++ b\n"))
    assert "exit_code=0" in rendered
    # The patch file is content-addressed inside the workspace, so the same
    # patch always resolves to the same path (a replay leaves no second copy).
    digest = hashlib.sha256(b"--- a\n+++ b\n").hexdigest()[:32]
    assert asyncio.run(tools.read_file(f".factory/patches/{digest}.patch")) == "--- a\n+++ b\n"


def test_every_known_tool_name_resolves() -> None:
    tools, _ = _tools()
    assert TOOL_NAMES == (
        "read_file",
        "write_file",
        "apply_patch",
        "run_command",
        "run_tests",
        "search_repo",
    )
    for name in TOOL_NAMES:
        assert callable(getattr(tools, name))


# --- SCM-derived revision ---------------------------------------------------


def test_branch_name_is_deterministic_and_ref_safe() -> None:
    assert branch_name("chg:product:0001") == "factory/chg-product-0001"
    assert branch_name("chg-001") == "factory/chg-001"


def test_scm_revision_reads_the_task_branch_head() -> None:
    repository = FakeRepository()
    change = make_change()
    branch = branch_name(change.id)
    asyncio.run(
        repository.ensure_branch(
            change.product, branch, from_revision="deadbeef", idempotency_key="b-1"
        )
    )
    resolver = ScmRevision(repository, base_ref="main")

    assert resolver(change, Stage.CONSTRUCTION) == "deadbeef"


def test_scm_revision_falls_back_to_the_base_ref_before_the_branch_exists() -> None:
    repository = FakeRepository()
    change = make_change()
    asyncio.run(
        repository.ensure_branch(
            change.product, "main", from_revision="base-1", idempotency_key="b-base"
        )
    )
    resolver = ScmRevision(repository, base_ref="main")

    assert resolver(change, Stage.CONSTRUCTION) == "base-1"


def test_scm_revision_falls_back_to_the_base_ref_for_an_unknown_ref() -> None:
    # get_revision reports a missing ref as KeyError (the shared port contract,
    # in the fake and in the GitHub adapter alike): an absent task branch is the
    # normal state before the first stage runs, and the base ref is the answer.
    resolver = ScmRevision(FakeRepository(), base_ref="main")
    assert resolver(make_change(), Stage.CONSTRUCTION) == "main"


def test_scm_revision_does_not_mask_a_provider_failure() -> None:
    # Only a missing ref is a fallback: a rejected token must fail the advance
    # instead of keying the operation by a guessed revision (ADR-006 p.3).
    resolver = ScmRevision(UnreachableRepository(), base_ref="main")
    with pytest.raises(PortError):
        resolver(make_change(), Stage.CONSTRUCTION)


def test_stage_role_covers_every_stage() -> None:
    assert set(STAGE_ROLE) == set(Stage)
    assert STAGE_ROLE[Stage.CONSTRUCTION].value == "develop"
    assert STAGE_ROLE[Stage.REVIEW_VERIFICATION].value == "quality"
    assert STAGE_ROLE[Stage.RELEASE].value == "ci_cd"
