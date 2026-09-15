"""Adapter bindings for the port contract suite (ADR-019 p.6).

Every fixture hands an adapter to the tests through its port type, so the test
modules never import ``dark_factory.adapters`` and the same suite runs against
fake → GitHub → GitLab. The source-control ports are parametrized over the
in-memory fakes and the real GitHub adapter (T-024) driven by the in-memory
GitHub API emulator (``github_api.py``) — no network, no real credentials; the
tracker port is parametrized the same way over the fake and the Plane adapter
(T-033) driven by ``plane_api.py``.
The ``*_journal`` fixtures expose recorded side effects that the ports
themselves do not surface (comments, dispatches, published statuses, spans,
events); fake bindings read their own state, the GitHub binding reads the
emulator's (comment journals strip the adapter's invisible idempotency
markers).
"""

import asyncio
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

import pytest

from dark_factory.adapters.fakes import (
    FakeArtifactStore,
    FakeCI,
    FakeEventPublisher,
    FakeExecution,
    FakeHarness,
    FakeKnowledge,
    FakeMergeRequests,
    FakePipelines,
    FakeReconciliationService,
    FakeRepository,
    FakeTelemetry,
    FakeTracker,
    FakeWorkflowEngine,
)
from dark_factory.adapters.scm.github import GitHubAdapter, GitHubConfig, StaticTokenProvider
from dark_factory.adapters.tracker import PlaneConfig, PlaneTrackerAdapter
from dark_factory.changes.enums import GateStatus
from dark_factory.changes.run import Change
from dark_factory.context.sdd.baseline import current_revision
from dark_factory.context.sdd.native import NativeChangeSetAdapter
from dark_factory.context.sdd.openspec import OpenSpecAdapter
from dark_factory.context.sdd.speckit import SpecKitAdapter
from dark_factory.ports import (
    ArtifactRef,
    ArtifactStorePort,
    ChangeRequestRef,
    ChangeSource,
    CIPort,
    DomainEvent,
    EventPublisherPort,
    ExecutionPort,
    Gate,
    HarnessPort,
    KnowledgePort,
    MergeRequestPort,
    PipelinePort,
    Provider,
    ReconciliationService,
    RepositoryPort,
    RepositoryRef,
    RiskClass,
    RunStatus,
    SDDPort,
    SourceKind,
    Span,
    TelemetryPort,
    TrackerPort,
    Usage,
    WorkflowEnginePort,
    WorkspaceHandle,
    WorkspaceRequest,
)
from tests.contract.github_api import (
    GITHUB_API_BASE_URL,
    GITHUB_INSTALLATION_TOKEN,
    GitHubApiEmulator,
    parse_job_ref,
    visible_comment,
)
from tests.contract.plane_api import (
    PLANE_API_BASE_URL,
    PLANE_API_TOKEN,
    PROJECT_ID,
    WORKSPACE_SLUG,
    PlaneApiEmulator,
)
from tests.sdd_factories import seed_baseline

PRODUCT = RepositoryRef(provider=Provider.GITHUB, slug="small/pilot")


class _MergeRequestBinding(NamedTuple):
    """MergeRequestPort plus the comment journal of the same binding."""

    port: MergeRequestPort
    journal: Callable[[ChangeRequestRef], tuple[str, ...]]


class _CIBinding(NamedTuple):
    """CIPort plus its dispatch journal and deterministic seeds."""

    port: CIPort
    journal: Callable[[], int]
    seed_gate: Callable[..., None]
    seed_artifacts: Callable[..., None]


class _TrackerBinding(NamedTuple):
    """TrackerPort plus the seeded change and journals of the same binding.

    The tracked change belongs to the binding, not to a separate fixture: a real
    tracker can only be written to for an issue that exists, so every binding
    seeds it up front (the fake tolerates unknown ids, Plane does not).
    """

    port: TrackerPort
    change: Change
    statuses: Callable[[str], tuple[str, ...]]
    approvals: Callable[[str], tuple[Gate, ...]]


def _tracked_change() -> Change:
    """The one change the tracker suite runs against, seeded by every binding.

    ``created_at`` is explicit so the value survives the ISO-8601 round trip
    through the Plane API and stays comparable with the mapped ``Change``.
    """
    return Change(
        id="chg-001",
        title="Add export button",
        source=ChangeSource.TRACKER,
        external_ref="PLANE-42",
        product=PRODUCT,
        risk_class=RiskClass.R1,
        created_at=datetime(2026, 9, 13, 9, 30, tzinfo=UTC),
    )


@pytest.fixture
def repository() -> RepositoryRef:
    """The product repository the source-control tests run against."""
    return PRODUCT


@pytest.fixture
def github_emulator() -> GitHubApiEmulator:
    """A fresh in-memory GitHub API for one test."""
    return GitHubApiEmulator(token=GITHUB_INSTALLATION_TOKEN)


@pytest.fixture
def github_adapter(github_emulator: GitHubApiEmulator) -> GitHubAdapter:
    """The GitHub adapter bound to the emulator: no network, no real App key."""
    return GitHubAdapter(
        GitHubConfig(api_base_url=GITHUB_API_BASE_URL),
        token_provider=StaticTokenProvider(GITHUB_INSTALLATION_TOKEN),
        transport=github_emulator.transport(),
    )


@pytest.fixture(params=["fake", "github"])
def repository_port(request: pytest.FixtureRequest) -> RepositoryPort:
    """RepositoryPort bound to the fake and the GitHub adapter (ADR-019 p.6)."""
    if request.param == "github":
        adapter: GitHubAdapter = request.getfixturevalue("github_adapter")
        return adapter.repository
    return FakeRepository()


@pytest.fixture
def merge_requests() -> FakeMergeRequests:
    return FakeMergeRequests()


@pytest.fixture(params=["fake", "github"])
def merge_request_binding(
    request: pytest.FixtureRequest, merge_requests: FakeMergeRequests
) -> _MergeRequestBinding:
    """MergeRequestPort and its comment journal, backed by the same adapter."""
    if request.param == "github":
        adapter: GitHubAdapter = request.getfixturevalue("github_adapter")
        emulator: GitHubApiEmulator = request.getfixturevalue("github_emulator")
        return _MergeRequestBinding(
            port=adapter.pull_requests,
            journal=_github_comment_journal(emulator),
        )
    return _MergeRequestBinding(port=merge_requests, journal=merge_requests.comments_of)


@pytest.fixture
def merge_request_port(merge_request_binding: _MergeRequestBinding) -> MergeRequestPort:
    return merge_request_binding.port


@pytest.fixture
def comment_journal(
    merge_request_binding: _MergeRequestBinding,
) -> Callable[[ChangeRequestRef], tuple[str, ...]]:
    return merge_request_binding.journal


def _github_comment_journal(
    emulator: GitHubApiEmulator,
) -> Callable[[ChangeRequestRef], tuple[str, ...]]:
    """Comments of a PR as the provider renders them (markers are invisible)."""

    def journal(cr: ChangeRequestRef) -> tuple[str, ...]:
        return tuple(visible_comment(body) for body in emulator.comments_of(cr.number))

    return journal


@pytest.fixture(params=["fake", "github"])
def pipeline_port(request: pytest.FixtureRequest) -> PipelinePort:
    """PipelinePort bound to the fake and the GitHub adapter (ADR-019 p.6)."""
    if request.param == "github":
        adapter: GitHubAdapter = request.getfixturevalue("github_adapter")
        return adapter.pipelines
    return FakePipelines()


@pytest.fixture(params=["fake", "github"])
def ci_binding(request: pytest.FixtureRequest) -> _CIBinding:
    """CIPort with its dispatch journal and gate/artifact seeds, one adapter."""
    if request.param == "github":
        adapter: GitHubAdapter = request.getfixturevalue("github_adapter")
        emulator: GitHubApiEmulator = request.getfixturevalue("github_emulator")
        return _CIBinding(
            port=adapter.ci,
            journal=emulator.run_count,
            seed_gate=_github_seed_gate(emulator),
            seed_artifacts=_github_seed_artifacts(emulator),
        )
    fake = FakeCI()
    return _CIBinding(
        port=fake,
        journal=fake.dispatch_count,
        seed_gate=fake.seed_outcome,
        seed_artifacts=fake.seed_artifacts,
    )


@pytest.fixture
def ci_port(ci_binding: _CIBinding) -> CIPort:
    return ci_binding.port


@pytest.fixture
def stage_job_journal(ci_binding: _CIBinding) -> Callable[[], int]:
    """Number of dispatched stage jobs; a replay is counted only once."""
    return ci_binding.journal


@pytest.fixture
def seed_gate(ci_binding: _CIBinding) -> Callable[..., None]:
    """Fix the terminal gate outcome of a dispatched job."""
    return ci_binding.seed_gate


@pytest.fixture
def seed_artifacts(ci_binding: _CIBinding) -> Callable[..., None]:
    """Record the artifacts a dispatched job is expected to have produced."""
    return ci_binding.seed_artifacts


# GitHub's conclusion vocabulary, from the fixture-side GateStatus values;
# pending is the default before seeding, so it has no conclusion.
_CONCLUSIONS: Mapping[GateStatus, str] = {
    GateStatus.PASSED: "success",
    GateStatus.FAILED: "failure",
    GateStatus.SKIPPED: "skipped",
}


def _github_seed_gate(emulator: GitHubApiEmulator) -> Callable[..., None]:
    """Seed the stage-named check run the adapter reads the gate from."""

    def seed(job_ref: str, *, status: GateStatus, summary: str | None = None) -> None:
        _, stage, run_id = parse_job_ref(job_ref)
        emulator.seed_check_run(
            run_id,
            name=f"dark-factory/{stage}",
            conclusion=_CONCLUSIONS[status],
            summary=summary,
        )

    return seed


def _github_seed_artifacts(emulator: GitHubApiEmulator) -> Callable[..., None]:
    def seed(job_ref: str, refs: Sequence[ArtifactRef]) -> None:
        _, _, run_id = parse_job_ref(job_ref)
        emulator.seed_artifacts(run_id, refs)

    return seed


@pytest.fixture
def plane_emulator() -> PlaneApiEmulator:
    """A fresh in-memory Plane API for one test."""
    return PlaneApiEmulator()


@pytest.fixture
def plane_adapter(plane_emulator: PlaneApiEmulator) -> PlaneTrackerAdapter:
    """The Plane tracker adapter bound to the emulator: no network, no real API key."""
    return PlaneTrackerAdapter(
        PlaneConfig(
            base_url=PLANE_API_BASE_URL,
            workspace_slug=WORKSPACE_SLUG,
            project_id=PROJECT_ID,
            repository=PRODUCT,
            api_key=PLANE_API_TOKEN,
        ),
        transport=plane_emulator.transport(),
    )


@pytest.fixture(params=["fake", "plane"])
def tracker_binding(request: pytest.FixtureRequest) -> _TrackerBinding:
    """TrackerPort bound to the fake and the Plane adapter, both seeded (ADR-013)."""
    change = _tracked_change()
    if request.param == "plane":
        emulator: PlaneApiEmulator = request.getfixturevalue("plane_emulator")
        adapter: PlaneTrackerAdapter = request.getfixturevalue("plane_adapter")
        emulator.seed_change("PLANE-42", change)
        return _TrackerBinding(
            port=adapter,
            change=change,
            statuses=emulator.statuses_of,
            approvals=emulator.approvals_of,
        )
    fake = FakeTracker()
    fake.seed("PLANE-42", change)
    return _TrackerBinding(
        port=fake,
        change=change,
        statuses=fake.statuses_of,
        approvals=fake.approvals_of,
    )


@pytest.fixture
def tracker_port(tracker_binding: _TrackerBinding) -> TrackerPort:
    return tracker_binding.port


@pytest.fixture
def tracked_change(tracker_binding: _TrackerBinding) -> Change:
    """The change seeded in the bound tracker; ``get_change`` finds it by external ref."""
    return tracker_binding.change


@pytest.fixture
def published_statuses(tracker_binding: _TrackerBinding) -> Callable[[str], tuple[str, ...]]:
    return tracker_binding.statuses


@pytest.fixture
def requested_approvals(tracker_binding: _TrackerBinding) -> Callable[[str], tuple[Gate, ...]]:
    return tracker_binding.approvals


@pytest.fixture
def harness_port() -> HarnessPort:
    return FakeHarness()


@pytest.fixture
def knowledge() -> FakeKnowledge:
    return FakeKnowledge()


@pytest.fixture
def knowledge_port(knowledge: FakeKnowledge) -> KnowledgePort:
    return knowledge


@pytest.fixture
def seeded_knowledge_port(knowledge: FakeKnowledge) -> KnowledgePort:
    """Knowledge seeded with one pinned repo source and one unpinned ADR."""
    knowledge.seed(SourceKind.REPO, "src/dark_factory", "abc123", "factory source")
    knowledge.seed(SourceKind.ADR, "docs/adr/ADR-001.md", None, "decision record")
    return knowledge


@pytest.fixture
def execution() -> FakeExecution:
    return FakeExecution()


@pytest.fixture
def execution_port(execution: FakeExecution) -> ExecutionPort:
    return execution


@pytest.fixture
def failing_execution(execution: FakeExecution) -> ExecutionPort:
    """Execution port whose ``pytest`` command fails deterministically."""
    execution.seed_failure(("pytest", "tests/"))
    return execution


@pytest.fixture
def evidence_workspace(execution: FakeExecution) -> WorkspaceHandle:
    """A prepared workspace with one seeded evidence file."""
    handle = asyncio.run(
        execution.prepare_workspace(
            WorkspaceRequest(repository=PRODUCT, revision="abc123", change_id="chg-001"),
            idempotency_key="ws-evidence",
        )
    )
    execution.seed_file(handle, "reports/pytest-report.xml", b"<testsuite tests='3'/>")
    return handle


@pytest.fixture
def artifact_store_port() -> ArtifactStorePort:
    return FakeArtifactStore()


@pytest.fixture
def telemetry() -> FakeTelemetry:
    return FakeTelemetry()


@pytest.fixture
def telemetry_port(telemetry: FakeTelemetry) -> TelemetryPort:
    return telemetry


@pytest.fixture
def recorded_spans(telemetry: FakeTelemetry) -> Callable[[], tuple[Span, ...]]:
    return telemetry.recorded_spans


@pytest.fixture
def recorded_usage(
    telemetry: FakeTelemetry,
) -> Callable[[], tuple[tuple[Usage, dict[str, str]], ...]]:
    return telemetry.recorded_usage


@pytest.fixture
def workflow_engine() -> FakeWorkflowEngine:
    return FakeWorkflowEngine()


@pytest.fixture
def workflow_engine_port(workflow_engine: FakeWorkflowEngine) -> WorkflowEnginePort:
    return workflow_engine


@pytest.fixture
def waiting_run(workflow_engine: FakeWorkflowEngine) -> str:
    """A started run parked in ``waiting``; resume must move it back to running."""
    run_id = asyncio.run(workflow_engine.start(idempotency_key="start-waiting"))
    workflow_engine.set_status(run_id, RunStatus.WAITING)
    return run_id


@pytest.fixture
def reconciliation_service() -> ReconciliationService:
    return FakeReconciliationService()


@pytest.fixture
def publisher() -> FakeEventPublisher:
    return FakeEventPublisher()


@pytest.fixture
def event_publisher_port(publisher: FakeEventPublisher) -> EventPublisherPort:
    return publisher


@pytest.fixture
def recorded_events(publisher: FakeEventPublisher) -> Callable[[], tuple[DomainEvent, ...]]:
    return publisher.recorded_events


@pytest.fixture
def sdd_factory_root(tmp_path: Path) -> Path:
    """A seeded ``.factory/`` Product Baseline for the SDD contract suite."""
    root = tmp_path / ".factory"
    seed_baseline(root)
    return root


@pytest.fixture
def sdd_revision(sdd_factory_root: Path) -> str:
    """Current baseline revision of the seeded factory root."""
    return current_revision(sdd_factory_root)


@pytest.fixture(params=["native", "speckit", "openspec"])
def sdd_port(sdd_factory_root: Path, tmp_path: Path, request: pytest.FixtureRequest) -> SDDPort:
    """SDDPort bound to every adapter over one shared factory root (ADR-020 p.8)."""
    native = NativeChangeSetAdapter(sdd_factory_root)
    if request.param == "speckit":
        return SpecKitAdapter(native, tmp_path / "specs")
    if request.param == "openspec":
        return OpenSpecAdapter(native, tmp_path / "openspec")
    return native
