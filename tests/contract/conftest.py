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

import pytest

from dark_factory.adapters.fakes import (
    FakeArtifactStore,
    FakeEventPublisher,
    FakeHarness,
    FakeMergeRequests,
    FakePipelines,
    FakeReconciliationService,
    FakeRepository,
    FakeTelemetry,
    FakeTracker,
    FakeWorkflowEngine,
)
from dark_factory.changes.run import Change
from dark_factory.ports import (
    ArtifactStorePort,
    ChangeRequestRef,
    ChangeSource,
    DomainEvent,
    EventPublisherPort,
    Gate,
    HarnessPort,
    MergeRequestPort,
    PipelinePort,
    Provider,
    ReconciliationService,
    RepositoryPort,
    RepositoryRef,
    RiskClass,
    RunStatus,
    Span,
    TelemetryPort,
    TrackerPort,
    Usage,
    WorkflowEnginePort,
)

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
