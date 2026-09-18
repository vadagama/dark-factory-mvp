"""Tests of the harness-backed stage executor and its role tools (T-092 S2).

The executor is driven end to end over the in-memory fakes: the harness, the
repository, the change requests and the isolated workspace are all fake, so the
whole attempt — profile resolution, workspace, tools, harness call, branch and
change request — is exercised without a network, a database or an LLM.
"""

import asyncio
import hashlib
from collections.abc import Mapping, Sequence

import pytest

from dark_factory.adapters.fakes import (
    FakeExecution,
    FakeHarness,
    FakeMergeRequests,
    FakeRepository,
    FakeTelemetry,
)
from dark_factory.agents.profiles.manifest import AgentProfile
from dark_factory.agents.profiles.registry import DEVELOP_PROFILE, PRODUCT_PROFILE, QUALITY_PROFILE
from dark_factory.changes.enums import (
    ChangeRequestStatus,
    GateStatus,
    RiskClass,
    Route,
    Stage,
    StageStatus,
)
from dark_factory.changes.next_action import StopAction, WaitForCIAction, WaitForInputAction
from dark_factory.changes.refs import RepositoryRef
from dark_factory.changes.usage import BudgetSnapshot, Usage
from dark_factory.orchestration.stages.agent import (
    STAGE_ROLE,
    AgentStageExecutor,
    ScmRevision,
    branch_name,
)
from dark_factory.orchestration.stages.context import StageContext, build_context
from dark_factory.orchestration.stages.pr_description import PrDescriptionRenderer
from dark_factory.orchestration.stages.tools import (
    TOOL_NAMES,
    ToolFunction,
    UnknownToolError,
    WorkspaceTools,
    resolve_path,
    tools_for,
)
from dark_factory.ports import (
    AgentResult,
    ChangeRequestRef,
    HarnessPort,
    HealthStatus,
    OpenChangeRequest,
    PortError,
    TaskEnvelope,
    WorkspaceHandle,
    WorkspaceRequest,
)
from tests.changes_factories import make_change

REVISION = "abc123"
RUN_ID = "run-001"
USAGE = Usage(prompt_tokens=3, completion_tokens=5, total_tokens=8)


class ProducingHarness(HarnessPort):
    """Harness that edits the workspace through the bound ``write_file`` tool.

    The canned ``FakeHarness`` writes nothing, which would make every attempt
    stop in "no changes" — the honest outcome of the new publication step, but
    useless for exercising the success path. This fake produces the way a real
    agent does: through the role tools the executor bound, not around them. A
    profile without ``write_file`` (product stages read and search only)
    produces nothing — an honest empty workspace.
    """

    def __init__(self, tools: Sequence[ToolFunction]) -> None:
        self._write = next((tool for tool in tools if tool.__name__ == "write_file"), None)

    async def run_stage(self, envelope: TaskEnvelope, /) -> AgentResult:
        if self._write is not None:
            await self._write("docs/note.md", "produced\n")
        return AgentResult(ok=True, output=f"fake produced:{envelope.stage.value}")

    async def health(self, /) -> HealthStatus:
        return HealthStatus(healthy=True, detail="producing fake harness")


class RecordingHarnessFactory:
    """Harness factory that records the tools each role was given.

    Without an explicit harness it builds a :class:`ProducingHarness` over the
    bound tools, so the default attempt really publishes something.
    """

    def __init__(self, harness: HarnessPort | None = None) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []
        self._harness = harness

    def __call__(self, profile: AgentProfile, tools: Sequence[ToolFunction]) -> HarnessPort:
        self.calls.append((profile.role.value, tuple(tool.__name__ for tool in tools)))
        return self._harness if self._harness is not None else ProducingHarness(tools)


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


class ExplodingPublishRepository(FakeRepository):
    """Repository whose commit publication fails with a provider error."""

    async def publish_commit(
        self,
        repository: RepositoryRef,
        branch: str,
        changes: Mapping[str, bytes],
        /,
        *,
        message: str,
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


class RecordingMergeRequests(FakeMergeRequests):
    """Merge-request fake that records every ``open`` input for assertions."""

    def __init__(self) -> None:
        super().__init__()
        self.opened: list[OpenChangeRequest] = []

    async def open(self, request: OpenChangeRequest, *, idempotency_key: str) -> ChangeRequestRef:
        self.opened.append(request)
        return await super().open(request, idempotency_key=idempotency_key)


def _context(
    *,
    stage: Stage = Stage.CONSTRUCTION,
    input_revision: str | None = REVISION,
    attempt: int = 1,
    description: str | None = None,
    risk_class: RiskClass | None = None,
) -> StageContext:
    return build_context(
        change=make_change().model_copy(update={"description": description}),
        stage=stage,
        route=Route.STANDARD,
        run_id=RUN_ID,
        input_revision=input_revision,
        budget=BudgetSnapshot(),
        attempt_number=attempt,
        risk_class=risk_class,
    )


def _executor(
    *,
    harness: HarnessPort | None = None,
    repository: FakeRepository | None = None,
    merge_requests: FakeMergeRequests | None = None,
    execution: FakeExecution | None = None,
    telemetry: FakeTelemetry | None = None,
    factory: RecordingHarnessFactory | None = None,
    descriptions: PrDescriptionRenderer | None = None,
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
        descriptions=descriptions,
    )
    return executor, recorder, repo, changes


# --- the successful attempt ------------------------------------------------


def test_a_risk_widened_human_gate_parks_for_the_human_not_for_ci() -> None:
    # At R3 every required gate of the stage is a human decision (ADR-023 p.3),
    # so the machine set is empty and the attempt must park for the human input
    # instead of a pipeline verdict that could never resolve it (ADR-029 p.3).
    executor, _recorder, _repo, _changes = _executor()

    result = executor(_context(risk_class=RiskClass.R3))

    assert result.status is StageStatus.WAITING
    assert isinstance(result.next_action, WaitForInputAction)


def test_successful_stage_waits_for_ci_with_a_change_request() -> None:
    executor, recorder, repo, _changes = _executor()
    result = executor(_context())
    repository = make_change().product
    branch = branch_name("chg-001")

    assert result.status is StageStatus.WAITING
    assert isinstance(result.next_action, WaitForCIAction)
    assert result.next_action.change_request is not None
    assert result.next_action.change_request.number == 1
    assert result.attempt_number == 1
    assert result.input_revision == REVISION
    assert [artifact.artifact_type for artifact in result.artifacts] == ["change_request"]
    assert result.artifacts[0].producer == "develop"
    # The change request carries the commit the stage published, not the
    # pinned input revision (TD-024).
    head = asyncio.run(repo.get_revision(repository, branch))
    assert result.artifacts[0].revision == head
    assert repo.commits_of(repository, branch) == (head,)
    assert recorder.calls == [("develop", DEVELOP_PROFILE.tools)]


def test_successful_planning_stage_waits_for_ci_with_a_change_request() -> None:
    """Planning publishes its request like construction and parks on CI (T-043)."""
    executor, recorder, repo, _changes = _executor()
    result = executor(_context(stage=Stage.PLANNING))
    repository = make_change().product
    branch = branch_name("chg-001")

    assert result.status is StageStatus.WAITING
    assert isinstance(result.next_action, WaitForCIAction)
    assert result.next_action.change_request is not None
    assert result.next_action.change_request.number == 1
    assert result.attempt_number == 1
    assert result.input_revision == REVISION
    assert [artifact.artifact_type for artifact in result.artifacts] == ["change_request"]
    assert result.artifacts[0].producer == "product"
    # The change request carries the commit the stage published, not the
    # pinned input revision (TD-024).
    head = asyncio.run(repo.get_revision(repository, branch))
    assert result.artifacts[0].revision == head
    assert repo.commits_of(repository, branch) == (head,)
    assert recorder.calls == [("product", PRODUCT_PROFILE.tools)]


def test_change_request_description_is_rendered_from_the_template() -> None:
    changes = RecordingMergeRequests()
    executor, _, _, _ = _executor(merge_requests=changes)
    task_text = "Bug: the health page reports the backend down when only the database is down."
    result = executor(_context(description=task_text))

    [request] = changes.opened
    description = request.description or ""
    assert "## Summary" in description
    assert "Add export button" in description
    assert task_text in description
    assert "factory/chg-001" in description
    assert "main" in description
    head = result.artifacts[0].revision
    assert head is not None
    assert head in description
    # The body is the structured document rendered from the template, not the
    # raw tracker text the change carried.
    assert not description.startswith(task_text)


def test_a_custom_description_renderer_shapes_the_change_request_body() -> None:
    changes = RecordingMergeRequests()
    executor, _, _, _ = _executor(
        merge_requests=changes,
        descriptions=PrDescriptionRenderer("CUSTOM {{change_id}} @ {{commit_sha}}"),
    )
    result = executor(_context())

    [request] = changes.opened
    assert request.description == f"CUSTOM chg-001 @ {result.artifacts[0].revision}"


def test_publish_opens_a_fresh_request_when_the_existing_one_is_merged() -> None:
    """A merged request cannot carry the commit: the attempt opens a new one (T-043).

    The live pilot parked forever here: the operator's merge of the
    specification request is the human decision (ADR-011), so the next
    stage's publish found a merged request, pushed to its branch and waited
    for CI that no open request would ever run. The publish must open a
    fresh request instead - the wait then observes an open request whose
    head the CI judges.
    """
    executor, _recorder, repo, changes = _executor()
    repository = make_change().product
    branch = branch_name("chg-001")
    merged = asyncio.run(
        changes.open(
            OpenChangeRequest(
                repository=repository,
                change_id="chg-001",
                source_branch=branch,
                target_branch="main",
                title="spec",
                description="spec body",
                head_sha=REVISION,
            ),
            idempotency_key="spec-open",
        )
    )
    asyncio.run(changes.merge(merged, expected_sha=REVISION, idempotency_key="spec-merge"))

    result = executor(_context(stage=Stage.PLANNING))

    assert result.status is StageStatus.WAITING
    assert isinstance(result.next_action, WaitForCIAction)
    assert result.next_action.change_request is not None
    assert result.next_action.change_request.number == 2
    assert result.next_action.change_request.status is ChangeRequestStatus.OPEN
    assert result.artifacts[0].revision == asyncio.run(repo.get_revision(repository, branch))
    # The fresh request is the one a cold lookup resolves to (FR-011).
    found = asyncio.run(changes.find_existing(repository, "chg-001"))
    assert found is not None
    assert found.number == 2


def test_publish_reuses_the_open_request_of_the_change() -> None:
    """An open request still carries the stage's commit: no duplicate is opened."""
    changes = RecordingMergeRequests()
    executor, _recorder, _repo, _changes = _executor(merge_requests=changes)
    opened = asyncio.run(
        changes.open(
            OpenChangeRequest(
                repository=make_change().product,
                change_id="chg-001",
                source_branch=branch_name("chg-001"),
                target_branch="main",
                title="spec",
                description="spec body",
                head_sha=REVISION,
            ),
            idempotency_key="spec-open",
        )
    )

    result = executor(_context(stage=Stage.PLANNING))

    assert result.status is StageStatus.WAITING
    assert isinstance(result.next_action, WaitForCIAction)
    assert result.next_action.change_request is not None
    assert result.next_action.change_request.number == opened.number
    assert len(changes.opened) == 1


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
    executor, _, repo, changes = _executor()
    first = executor(_context())
    second = executor(_context(attempt=2))

    assert isinstance(first.next_action, WaitForCIAction)
    assert isinstance(second.next_action, WaitForCIAction)
    assert first.next_action.change_request == second.next_action.change_request
    # One change request, not two: the second attempt found the existing one
    # (FR-011) and the branch ensure is keyed per operation (FR-017).
    assert asyncio.run(changes.find_existing(make_change().product, "chg-001")) is not None
    # One commit, not two: the replayed publication returns the commit SHA
    # recorded at the first call (TD-024).
    assert repo.commits_of(make_change().product, branch_name("chg-001")) == (
        first.artifacts[0].revision,
    )
    assert second.artifacts[0].revision == first.artifacts[0].revision


def test_stage_publishes_the_workspace_changes_as_one_commit() -> None:
    executor, _, repo, _ = _executor()
    result = executor(_context())
    repository = make_change().product

    commits = repo.commits_of(repository, branch_name("chg-001"))
    assert len(commits) == 1
    assert repo.commit_files(commits[0]) == {"docs/note.md": b"produced\n"}
    assert result.artifacts[0].revision == commits[0]


def test_an_attempt_without_changes_blocks_before_any_external_effect() -> None:
    # A canned harness writes nothing: "no changes" is the stage's honest
    # decision — a retryable blocked attempt, never a silent empty change
    # request, and the branch/commit must not exist either.
    executor, _, repo, changes = _executor(harness=FakeHarness())
    result = executor(_context())

    assert result.status is StageStatus.BLOCKED
    assert isinstance(result.next_action, StopAction)
    assert "no changes" in result.next_action.reason
    assert asyncio.run(changes.find_existing(make_change().product, "chg-001")) is None
    with pytest.raises(KeyError):
        asyncio.run(repo.get_revision(make_change().product, branch_name("chg-001")))


def test_review_stage_reuses_the_open_request_when_the_agent_changed_nothing() -> None:
    """The review stage judges the request under review and writes nothing (T-043).

    The live pilot blocked here: the quality agent reviewed the construction
    request and legitimately changed no file, so the attempt must publish the
    request already under review and park on it instead of stopping with
    "no changes". The quality profile has no write tool, so the default fake
    harness leaves the workspace empty, exactly like the real review.
    """
    changes = RecordingMergeRequests()
    executor, recorder, repo, _ = _executor(merge_requests=changes)
    repository = make_change().product
    opened = asyncio.run(
        changes.open(
            OpenChangeRequest(
                repository=repository,
                change_id="chg-001",
                source_branch=branch_name("chg-001"),
                target_branch="main",
                title="construction",
                description="construction body",
                head_sha=REVISION,
            ),
            idempotency_key="construction-open",
        )
    )

    result = executor(_context(stage=Stage.REVIEW_VERIFICATION))

    assert result.status is StageStatus.WAITING
    assert isinstance(result.next_action, WaitForCIAction)
    assert result.next_action.change_request == opened
    assert [artifact.artifact_type for artifact in result.artifacts] == ["change_request"]
    assert result.artifacts[0].producer == "quality"
    # The reviewed reference carries no head SHA, so the artifact names the
    # pinned input revision - the revision the reviewed request's CI ran on.
    assert result.artifacts[0].revision == REVISION
    # The review published no external effect of its own: no second request and
    # no branch or commit.
    assert len(changes.opened) == 1
    assert recorder.calls == [("quality", QUALITY_PROFILE.tools)]
    assert repo.commits_of(repository, branch_name("chg-001")) == ()
    with pytest.raises(KeyError):
        asyncio.run(repo.get_revision(repository, branch_name("chg-001")))


def test_review_stage_blocks_without_an_open_request_to_review() -> None:
    """Nothing under review: the no-changes decision stays a blocked attempt."""
    executor, _, repo, changes = _executor()
    result = executor(_context(stage=Stage.REVIEW_VERIFICATION))

    assert result.status is StageStatus.BLOCKED
    assert isinstance(result.next_action, StopAction)
    assert "no changes" in result.next_action.reason
    assert asyncio.run(changes.find_existing(make_change().product, "chg-001")) is None
    with pytest.raises(KeyError):
        asyncio.run(repo.get_revision(make_change().product, branch_name("chg-001")))


def test_review_stage_blocks_when_the_request_under_review_is_already_merged() -> None:
    """A merged request is nothing under review: the honest block stays."""
    executor, _, _, changes = _executor()
    repository = make_change().product
    merged = asyncio.run(
        changes.open(
            OpenChangeRequest(
                repository=repository,
                change_id="chg-001",
                source_branch=branch_name("chg-001"),
                target_branch="main",
                title="construction",
                description="construction body",
                head_sha=REVISION,
            ),
            idempotency_key="construction-open",
        )
    )
    asyncio.run(changes.merge(merged, expected_sha=REVISION, idempotency_key="construction-merge"))

    result = executor(_context(stage=Stage.REVIEW_VERIFICATION))

    assert result.status is StageStatus.BLOCKED
    assert isinstance(result.next_action, StopAction)
    assert "no changes" in result.next_action.reason


def test_a_publish_failure_blocks_without_leaking_provider_text() -> None:
    executor, _, repo, changes = _executor(repository=ExplodingPublishRepository())
    result = executor(_context())

    assert result.status is StageStatus.BLOCKED
    assert isinstance(result.next_action, StopAction)
    assert "RuntimeError" in result.next_action.reason
    assert "token" not in result.next_action.reason
    assert "scm.example" not in result.next_action.reason
    # The branch effect happened before the failing commit; the change request
    # never did.
    assert asyncio.run(changes.find_existing(make_change().product, "chg-001")) is None
    assert asyncio.run(repo.get_revision(make_change().product, branch_name("chg-001"))) == "abc123"


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
    # Rejected to the model as an error result, not raised: an exception would
    # abort the whole attempt, while the model can correct its next call. The
    # path is still never read - the isolation itself lives in resolve_path.
    rendered = asyncio.run(tools.read_file(path))
    assert rendered.startswith("error:")
    assert "workspace" in rendered


def test_resolve_path_normalizes_safe_relative_paths() -> None:
    assert resolve_path("./src//app.py") == "src/app.py"


@pytest.mark.parametrize("path", ["../etc/passwd", "/etc/passwd", "~/secrets", "", "src/../../x"])
def test_resolve_path_still_rejects_unsafe_paths(path: str) -> None:
    # The isolation boundary itself: resolve_path keeps raising - only the
    # model-facing tools translate the rejection into an error result.
    with pytest.raises(ValueError):
        resolve_path(path)


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


def test_read_of_a_missing_file_is_reported_not_raised() -> None:
    # The port's absent convention is a KeyError; surfacing it to the model as
    # text keeps the attempt alive (T-043 increment 1: one read of a file the
    # agent had not written yet blocked the whole stage attempt).
    tools, _ = _tools()
    rendered = asyncio.run(tools.read_file("src/missing.py"))
    assert rendered.startswith("error:")
    assert "no evidence file" in rendered


def test_search_repo_requires_a_pattern() -> None:
    tools, _ = _tools()
    assert asyncio.run(tools.search_repo("  ")).startswith("error:")


def test_run_command_requires_an_argv() -> None:
    tools, _ = _tools()
    assert asyncio.run(tools.run_command([])).startswith("error:")


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
