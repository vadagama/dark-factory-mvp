"""Port contracts of the factory core (HLD §7, ADR-015 p.3).

Signatures are 1:1 with ``specs/001-dark-factory-mvp/contracts/ports.md``.
Adapters implement these protocols and import only ``dark_factory.ports``; the
core never imports ``dark_factory.adapters`` (enforced by
``tests/test_import_boundaries.py``). Every state-changing method takes an
``idempotency_key`` so a replay never creates a second external effect
(FR-017, ADR-006 p.3); one run executes in exactly one provider (ADR-019 §5).
"""

from collections.abc import Mapping
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
class CiStageTogglePort(Protocol):
    """Repository-variable store of the CI stage toggles (T059, ADR-026/ADR-027).

    Bound to one configured repository at construction: no request can point a
    read or a write at another repository. The contract is deliberately
    value-level — *which* value switches a stage off belongs to the catalog
    (``dark_factory.ci.stages``), not to the provider — so any provider that
    stores non-secret configuration variables can implement it.
    """

    async def values(self) -> Mapping[str, str]:
        """Current values of the repository variables (name → value)."""
        ...

    async def set_value(self, variable: str, value: str | None) -> None:
        """Set ``variable`` to ``value``, or delete it when ``value`` is ``None``.

        Idempotent: deleting an absent variable and re-writing the same value
        are both no-ops. Naming an unknown variable is the caller's bug — the
        API gates every call through the catalog first.
        """
        ...


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
    """Isolated workspaces, file writes, command execution and evidence (plan T-012).

    ``idempotency_key`` is not one semantic for every method: replay-dedup is
    correct only where a call mints an external resource (FR-017), so the
    contract fixes the role per method.

    * ``prepare_workspace`` — replay-dedup by key: the same key returns the same
      ``WorkspaceHandle`` and never mints a second workspace.
    * ``write_file`` — idempotent *by state*, not by key: the path holds the
      written bytes afterwards (last write wins), and a key must not make the
      adapter drop a write.
    * ``run_command`` / ``collect_evidence`` — the key only addresses the call in
      the effect ledger and audit trail: both execute/read the *current* workspace
      state and must never return a result cached under the key. An agent stage
      edits files and re-runs the check with a stable key, so a cached result
      would pin the first (failing) outcome forever.

    ``write_file`` is the write half of ``collect_evidence``: the role tools of an
    agent stage (``AgentProfile.tools``) edit the isolated workspace through it
    (T-092 S2), so the port must be able to put content into the workspace, not
    only read it back. Like every role tool it is bound per workspace, so the
    method is additive to the versioned contract (ADR-015 p.3).
    """

    async def prepare_workspace(
        self, request: WorkspaceRequest, /, *, idempotency_key: str
    ) -> WorkspaceHandle: ...
    async def write_file(
        self, workspace: WorkspaceHandle, path: str, content: bytes, /, *, idempotency_key: str
    ) -> None: ...
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
