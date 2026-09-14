"""Port contracts of the factory core (HLD §7, ADR-015 p.3).

Signatures are 1:1 with ``specs/001-dark-factory-mvp/contracts/ports.md``.
Adapters implement these protocols and import only ``dark_factory.ports``; the
core never imports ``dark_factory.adapters`` (enforced by
``tests/test_import_boundaries.py``). Every state-changing method takes an
``idempotency_key`` so a replay never creates a second external effect
(FR-017, ADR-006 p.3); one run executes in exactly one provider (ADR-019 §5).
"""

from contextlib import AbstractContextManager
from typing import Protocol, runtime_checkable

from dark_factory.changes.enums import Gate, RunStatus
from dark_factory.changes.findings import GateResult
from dark_factory.changes.refs import ArtifactRef, ChangeRequestRef, RepositoryRef
from dark_factory.changes.run import Change
from dark_factory.changes.usage import Usage
from dark_factory.context.bundle import ContextBundle
from dark_factory.context.sdd.normalized import ChangeSet, RequirementsSnapshot
from dark_factory.ports.agents import AgentResult, TaskEnvelope
from dark_factory.ports.common import (
    ArtifactSpec,
    HealthStatus,
    OpenChangeRequest,
    PipelineStatus,
    Span,
    StageJobRequest,
)
from dark_factory.ports.context import (
    ContextRequest,
    EvidenceFile,
    ExecutionResult,
    WorkspaceHandle,
    WorkspaceRequest,
)
from dark_factory.ports.events import DomainEvent
from dark_factory.ports.reconciliation import ReconcileDesired, ReconcileObserved, ReconcileResult


@runtime_checkable
class RepositoryPort(Protocol):
    """Branch and revision operations on a product repository."""

    async def get_revision(self, repository: RepositoryRef, ref: str, /) -> str: ...
    async def ensure_branch(
        self,
        repository: RepositoryRef,
        branch: str,
        *,
        from_revision: str,
        idempotency_key: str,
    ) -> str: ...


@runtime_checkable
class MergeRequestPort(Protocol):
    """Change request lifecycle; GitHub PR and GitLab MR in one domain type (ADR-019 p.2)."""

    async def open(
        self, request: OpenChangeRequest, *, idempotency_key: str
    ) -> ChangeRequestRef: ...
    async def find_existing(
        self, repository: RepositoryRef, change_id: str, /
    ) -> ChangeRequestRef | None: ...
    async def add_comment(
        self, cr: ChangeRequestRef, body: str, *, idempotency_key: str
    ) -> None: ...
    async def merge(
        self, cr: ChangeRequestRef, *, expected_sha: str, idempotency_key: str
    ) -> None: ...


@runtime_checkable
class PipelinePort(Protocol):
    """Observation of CI pipelines on a repository."""

    async def status(self, repository: RepositoryRef, ref: str, /) -> PipelineStatus: ...


@runtime_checkable
class CIPort(Protocol):
    """CI job execution for stage gates (ADR-019 p.3): dispatch, gate result, artifacts."""

    async def run_stage_job(self, request: StageJobRequest, *, idempotency_key: str) -> str: ...
    async def gate_status(self, job_ref: str, /) -> GateResult: ...
    async def artifacts(self, job_ref: str, /) -> list[ArtifactRef]: ...


@runtime_checkable
class TrackerPort(Protocol):
    """External work tracker (Plane); unavailability must not block CLI/Console (FR-020)."""

    async def get_change(self, external_ref: str, /) -> Change | None: ...
    async def publish_status(
        self, change_id: str, status: str, *, idempotency_key: str
    ) -> None: ...
    async def request_approval(
        self, change_id: str, gate: Gate, *, idempotency_key: str
    ) -> None: ...


@runtime_checkable
class HarnessPort(Protocol):
    """Agent execution (PydanticAI first, ADR-002 p.2); deterministic stage steps bypass it."""

    async def run_stage(self, envelope: TaskEnvelope, /) -> AgentResult: ...
    async def health(self, /) -> HealthStatus: ...


@runtime_checkable
class ArtifactStorePort(Protocol):
    """Artifact storage (CI artifacts in MVP, S3/MinIO in DC: ADR-009, ADR-015 p.4)."""

    async def put(self, spec: ArtifactSpec, /) -> ArtifactRef: ...
    async def get(self, ref: ArtifactRef, /) -> bytes: ...
    async def exists(self, ref: ArtifactRef, /) -> bool: ...


@runtime_checkable
class TelemetryPort(Protocol):
    """Traces and usage with change → run → stage → agent correlation (ADR-009 p.2)."""

    def span(self, name: str, /, **attributes: str) -> AbstractContextManager[Span]: ...
    def record_usage(self, usage: Usage, /, **attributes: str) -> None: ...


@runtime_checkable
class WorkflowEnginePort(Protocol):
    """Lightweight workflow engine (workflow-core now, Temporal later: ADR-003, ADR-006 p.9)."""

    async def start(self, *, idempotency_key: str, expected_revision: int | None = None) -> str: ...
    async def resume(self, run_id: str, *, idempotency_key: str) -> str: ...
    async def cancel(self, run_id: str, *, idempotency_key: str, reason: str) -> None: ...
    async def get_status(self, run_id: str, /) -> RunStatus: ...


@runtime_checkable
class ReconciliationService(Protocol):
    """Separate reconciliation service, not an engine method (ADR-006 p.9)."""

    async def reconcile(
        self, *, desired: ReconcileDesired, observed: ReconcileObserved
    ) -> ReconcileResult: ...


@runtime_checkable
class EventPublisherPort(Protocol):
    """Outbox event publishing; producers never touch outbox tables (ADR-016 p.7)."""

    async def publish(self, event: DomainEvent, /) -> None: ...


@runtime_checkable
class KnowledgePort(Protocol):
    """Context sources of a change → versioned ContextBundle (plan T-012, FR-001).

    Minimal by design: search and traversal arrive with the real source
    providers, not before (YAGNI).
    """

    async def collect(self, request: ContextRequest, /) -> ContextBundle: ...


@runtime_checkable
class ExecutionPort(Protocol):
    """Isolated workspaces, command execution and evidence collection (plan T-012)."""

    async def prepare_workspace(
        self, request: WorkspaceRequest, /, *, idempotency_key: str
    ) -> WorkspaceHandle: ...
    async def run_command(
        self, workspace: WorkspaceHandle, argv: tuple[str, ...], /, *, idempotency_key: str
    ) -> ExecutionResult: ...
    async def collect_evidence(
        self, workspace: WorkspaceHandle, path: str, /, *, idempotency_key: str
    ) -> EvidenceFile: ...


@runtime_checkable
class SDDPort(Protocol):
    """Native SDD Core: ChangeSet lifecycle over a product baseline (ADR-020 p.8).

    Signatures per ``specs/001-dark-factory-mvp/contracts/ports.md``.
    Adapters (``NativeChangeSetAdapter``, ``SpecKitAdapter``,
    ``OpenSpecAdapter``) implement this structurally from
    ``dark_factory.context.sdd`` and do not import ``dark_factory.ports`` —
    the runtime-checkable protocol validates them without that import.
    """

    async def create_change(self, change: ChangeSet, /) -> str: ...
    async def read_requirements(self, change_id: str, /) -> RequirementsSnapshot: ...
    async def apply_delta(self, change_id: str, /, *, expected_revision: str) -> str: ...
