"""Port contracts of the factory core (HLD §7, ADR-015 p.3).

Signatures are 1:1 with ``specs/001-dark-factory-mvp/contracts/ports.md``.
Adapters implement these protocols and import only ``dark_factory.ports``; the
core never imports ``dark_factory.adapters`` (enforced by
``tests/test_import_boundaries.py``). Every state-changing method takes an
``idempotency_key`` so a replay never creates a second external effect
(FR-017, ADR-006 p.3); one run executes in exactly one provider (ADR-019 §5).
"""

from collections.abc import Mapping, Sequence
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
    ChangeRequestObservation,
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
from dark_factory.ports.provisioning import (
    BaselineBootstrapResult,
    MirrorRef,
    RepositoryValidation,
)
from dark_factory.ports.reconciliation import ReconcileDesired, ReconcileObserved, ReconcileResult


@runtime_checkable
class RepositoryPort(Protocol):
    """Branch, revision and publication operations on a product repository.

    ``publish_commit`` is the publication step of an agent stage: it lands the
    changed file set of the isolated workspace on the task branch as **one**
    external effect — commit and push together (ADR-006 p.3: a local commit
    without a push is not an external effect, and splitting one publication
    across two ports would break its per-effect ledger entry). The unit of
    transfer is the file set (``path → bytes``), not a diff: a snapshot is
    self-consistent and replays to the same tree, while a diff would need a
    provider-side apply. The handle of the workspace the files came from is
    deliberately *not* part of the signature — a repository adapter cannot
    read a workspace, so the stage collects the values through
    ``ExecutionPort.collect_changes`` and passes them by value.

    * ``publish_commit`` — replay-dedup by key: the same key returns the commit
      SHA recorded at the first call and never creates a second commit (the
      lookup must survive a cold adapter, like ``MergeRequestPort.add_comment``
      does). An empty ``changes`` is a ``ValueError`` — "no changes" is a stage
      decision, not a silent empty commit. The branch must already exist
      (``ensure_branch``); a missing one is a ``KeyError`` (404 = absent). The
      commit lands on the branch's current head and the returned SHA is the
      revision the change request carries as ``head_sha``. Deletions are not
      expressible in the MVP file set: the role tools do not delete.
    """

    async def get_revision(self, repository: RepositoryRef, ref: str, /) -> str: ...
    async def ensure_branch(
        self,
        repository: RepositoryRef,
        branch: str,
        *,
        from_revision: str,
        idempotency_key: str,
    ) -> str: ...
    async def publish_commit(
        self,
        repository: RepositoryRef,
        branch: str,
        changes: Mapping[str, bytes],
        /,
        *,
        message: str,
        idempotency_key: str,
    ) -> str: ...


@runtime_checkable
class RepositoryProvisioningPort(Protocol):
    """Repository preparation before any agent stage touches it (ADR-031 p.1).

    Two adapters share this port (ADR-031 p.2): ``LocalMirror`` over an
    operator-prepared local mirror and ``ProviderClone`` over a provider clone
    on a GitHub App installation token (T068). The core never learns where the
    mirror came from; the execution layer keeps working from the prepared one.

    * ``validate`` — read-only probe (no idempotency key and no mutation,
      ADR-031 p.5): availability, the observed default branch and head
      revision, and whether the product baseline is present. An *empty*
      repository (unborn HEAD) is a normal state, not an error (p.4) — the
      baseline bootstrap is what gives it a first revision. A repository the
      adapter cannot reach is reported as ``UNAVAILABLE``, not raised: the
      availability fact is evidence (p.6), not a control-flow error.
    * ``ensure_mirror`` — prepare the local mirror the execution layer works
      from. Replay-dedup by key: the same key returns the locator recorded at
      the first call. A repository the adapter cannot materialise is absent —
      the shared ``KeyError`` convention.
    * ``bootstrap_baseline`` — apply the named ``packs`` to an empty or
      unprepared repository. Replay-dedup by key (ADR-006 p.3: one external
      effect) and evidence as the result (ADR-031 p.6). An adapter that cannot
      perform the operation raises
      ``ProvisioningOperationUnsupportedError`` naming it — never reporting a
      bootstrap it did not perform.
    """

    async def validate(self, repository: RepositoryRef, /) -> RepositoryValidation: ...
    async def ensure_mirror(
        self, repository: RepositoryRef, /, *, idempotency_key: str
    ) -> MirrorRef: ...
    async def bootstrap_baseline(
        self, repository: RepositoryRef, /, *, packs: Sequence[str], idempotency_key: str
    ) -> BaselineBootstrapResult: ...


@runtime_checkable
class MergeRequestPort(Protocol):
    """Change request lifecycle; GitHub PR and GitLab MR in one domain type (ADR-019 p.2).

    ``observe`` is the read model of wait resolution (T-092 S3): the live head
    SHA, the merge state and the human reviews of one change request in a
    single provider-neutral value. Read-only — no idempotency key, because an
    observation mints no external effect (FR-017). An unknown change request
    is a ``KeyError`` (404 = absent, the shared convention); a provider that
    does not report a head returns ``head_sha = None`` and the observation
    degrades honestly — no pipeline verdict and no version-bound approval can
    be derived without a SHA (FR-009).
    """

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
    async def observe(self, cr: ChangeRequestRef, /) -> ChangeRequestObservation: ...


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
    (``dark_factory.orchestration.ci``), not to the provider — so any provider that
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
    * ``collect_changes`` — the read half of publication: the file set the
      workspace currently holds, passed by value to ``RepositoryPort.publish_commit``
      (a repository adapter cannot read a workspace, so publication carries
      values, not handles). Like ``run_command``/``collect_evidence`` the key
      only addresses the call in the ledger: the *current* file set is
      returned, never a snapshot cached under the key.

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
    async def collect_changes(
        self, workspace: WorkspaceHandle, /, *, idempotency_key: str
    ) -> Mapping[str, bytes]: ...


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
