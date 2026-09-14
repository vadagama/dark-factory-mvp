"""Adapter bindings for the port contract suite (ADR-019 p.6).

Every fixture hands an adapter to the tests through its port type, so the test
modules never import ``dark_factory.adapters`` and the same suite runs against
fake → GitHub → GitLab. The P0 bindings below are the in-memory fakes. The
``*_journal`` fixtures expose recorded side effects that the ports themselves
do not surface (comments, published statuses, spans, events); real adapter
bindings will back them through provider APIs.
"""

import asyncio
from collections.abc import Callable
from pathlib import Path

import pytest

from dark_factory.adapters.fakes import (
    FakeArtifactStore,
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
from dark_factory.changes.run import Change
from dark_factory.context.sdd.baseline import current_revision
from dark_factory.context.sdd.native import NativeChangeSetAdapter
from dark_factory.context.sdd.openspec import OpenSpecAdapter
from dark_factory.context.sdd.speckit import SpecKitAdapter
from dark_factory.ports import (
    ArtifactStorePort,
    ChangeRequestRef,
    ChangeSource,
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
from tests.sdd_factories import seed_baseline

PRODUCT = RepositoryRef(provider=Provider.GITHUB, slug="small/pilot")


@pytest.fixture
def repository() -> RepositoryRef:
    """The product repository the source-control tests run against."""
    return PRODUCT


@pytest.fixture
def repository_port() -> RepositoryPort:
    return FakeRepository()


@pytest.fixture
def merge_requests() -> FakeMergeRequests:
    return FakeMergeRequests()


@pytest.fixture
def merge_request_port(merge_requests: FakeMergeRequests) -> MergeRequestPort:
    return merge_requests


@pytest.fixture
def comment_journal(
    merge_requests: FakeMergeRequests,
) -> Callable[[ChangeRequestRef], tuple[str, ...]]:
    return merge_requests.comments_of


@pytest.fixture
def pipeline_port() -> PipelinePort:
    return FakePipelines()


@pytest.fixture
def tracker() -> FakeTracker:
    return FakeTracker()


@pytest.fixture
def tracker_port(tracker: FakeTracker) -> TrackerPort:
    return tracker


@pytest.fixture
def tracked_change(tracker: FakeTracker) -> Change:
    """Seed the tracker with one change; ``get_change`` finds it by external ref."""
    change = Change(
        id="chg-001",
        title="Add export button",
        source=ChangeSource.TRACKER,
        external_ref="PLANE-42",
        product=PRODUCT,
        risk_class=RiskClass.R1,
    )
    tracker.seed("PLANE-42", change)
    return change


@pytest.fixture
def published_statuses(tracker: FakeTracker) -> Callable[[str], tuple[str, ...]]:
    return tracker.statuses_of


@pytest.fixture
def requested_approvals(tracker: FakeTracker) -> Callable[[str], tuple[Gate, ...]]:
    return tracker.approvals_of


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
