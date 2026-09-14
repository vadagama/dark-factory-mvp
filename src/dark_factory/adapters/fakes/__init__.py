"""In-memory fakes of the port contracts (P0 implementations, ADR-019 p.6).

The contract suite runs against these fakes first and later against the real
GitHub/GitLab adapters; the fakes keep all state in memory (no network, files
or database) and make every keyed mutation idempotent.
"""

from dark_factory.adapters.fakes.artifacts import FakeArtifactStore
from dark_factory.adapters.fakes.ci import FakeCI
from dark_factory.adapters.fakes.events import FakeEventPublisher
from dark_factory.adapters.fakes.execution import FakeExecution
from dark_factory.adapters.fakes.harness import FakeHarness
from dark_factory.adapters.fakes.knowledge import FakeKnowledge
from dark_factory.adapters.fakes.scm import FakeMergeRequests, FakePipelines, FakeRepository
from dark_factory.adapters.fakes.telemetry import FakeTelemetry
from dark_factory.adapters.fakes.tracker import FakeTracker
from dark_factory.adapters.fakes.workflow import FakeReconciliationService, FakeWorkflowEngine

__all__ = [
    "FakeArtifactStore",
    "FakeCI",
    "FakeEventPublisher",
    "FakeExecution",
    "FakeHarness",
    "FakeKnowledge",
    "FakeMergeRequests",
    "FakePipelines",
    "FakeReconciliationService",
    "FakeRepository",
    "FakeTelemetry",
    "FakeTracker",
    "FakeWorkflowEngine",
]
