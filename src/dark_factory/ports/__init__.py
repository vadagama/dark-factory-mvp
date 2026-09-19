"""Abstract interfaces (ports) decoupling the factory core from external systems.

Single public surface of the port layer (ADR-015 p.3): adapters import only
``dark_factory.ports``, never ``dark_factory.changes`` or SDKs. This package
re-exports the port contracts, their DTOs and the domain types that appear in
port signatures.
"""

from dark_factory.changes.enums import (
    ChangeRequestStatus,
    ChangeSource,
    Gate,
    GateStatus,
    Provider,
    RiskClass,
    Role,
    RunStatus,
    Stage,
)
from dark_factory.changes.findings import GateResult
from dark_factory.changes.refs import ArtifactRef, ChangeRequestRef, RepositoryRef
from dark_factory.changes.run import Change
from dark_factory.changes.usage import Usage
from dark_factory.context.bundle import (
    CONTEXT_SCHEMA_VERSION,
    ContextBundle,
    ContextSchemaVersion,
    ContextSource,
    SourceKind,
    build_bundle,
)
from dark_factory.context.sdd.errors import BaselineMismatchError, ChangeNotFoundError
from dark_factory.context.sdd.normalized import ChangeSet, RequirementsSnapshot
from dark_factory.ports.agents import (
    AGENTS_SCHEMA_VERSION,
    AgentResult,
    AgentSchemaVersion,
    TaskEnvelope,
)
from dark_factory.ports.common import (
    ArtifactSpec,
    ChangeRequestObservation,
    CommitInfo,
    HealthStatus,
    OpenChangeRequest,
    PipelineStatus,
    ReviewObservation,
    Span,
    StageJobRequest,
    stage_gate,
)
from dark_factory.ports.context import (
    ContextRequest,
    EvidenceFile,
    ExecutionResult,
    WorkspaceHandle,
    WorkspaceRequest,
)
from dark_factory.ports.errors import (
    HeadMismatchError,
    PortError,
    ProvisioningOperationUnsupportedError,
    RunNotFoundError,
    UnsafeWorkspacePathError,
)
from dark_factory.ports.events import DomainEvent, EventType
from dark_factory.ports.protocols import (
    ArtifactStorePort,
    CIPort,
    CiStageTogglePort,
    EventPublisherPort,
    ExecutionPort,
    HarnessPort,
    KnowledgePort,
    MergeRequestPort,
    PipelinePort,
    ReconciliationService,
    RepositoryPort,
    RepositoryProvisioningPort,
    SDDPort,
    TelemetryPort,
    TrackerPort,
    WorkflowEnginePort,
)
from dark_factory.ports.provisioning import (
    AppliedPack,
    BaselineBootstrapResult,
    MirrorRef,
    RepositoryState,
    RepositoryValidation,
)
from dark_factory.ports.reconciliation import ReconcileDesired, ReconcileObserved, ReconcileResult

__all__ = [
    "AGENTS_SCHEMA_VERSION",
    "CONTEXT_SCHEMA_VERSION",
    "AgentResult",
    "AgentSchemaVersion",
    "AppliedPack",
    "ArtifactRef",
    "ArtifactSpec",
    "ArtifactStorePort",
    "BaselineBootstrapResult",
    "BaselineMismatchError",
    "CIPort",
    "Change",
    "ChangeNotFoundError",
    "ChangeRequestObservation",
    "ChangeRequestRef",
    "ChangeRequestStatus",
    "ChangeSet",
    "ChangeSource",
    "CiStageTogglePort",
    "CommitInfo",
    "ContextBundle",
    "ContextRequest",
    "ContextSchemaVersion",
    "ContextSource",
    "DomainEvent",
    "EventPublisherPort",
    "EventType",
    "EvidenceFile",
    "ExecutionPort",
    "ExecutionResult",
    "Gate",
    "GateResult",
    "GateStatus",
    "HarnessPort",
    "HeadMismatchError",
    "HealthStatus",
    "KnowledgePort",
    "MergeRequestPort",
    "MirrorRef",
    "OpenChangeRequest",
    "PipelinePort",
    "PipelineStatus",
    "PortError",
    "Provider",
    "ProvisioningOperationUnsupportedError",
    "ReconcileDesired",
    "ReconcileObserved",
    "ReconcileResult",
    "ReconciliationService",
    "RepositoryPort",
    "RepositoryProvisioningPort",
    "RepositoryRef",
    "RepositoryState",
    "RepositoryValidation",
    "RequirementsSnapshot",
    "ReviewObservation",
    "RiskClass",
    "Role",
    "RunNotFoundError",
    "RunStatus",
    "SDDPort",
    "SourceKind",
    "Span",
    "Stage",
    "StageJobRequest",
    "TaskEnvelope",
    "TelemetryPort",
    "TrackerPort",
    "UnsafeWorkspacePathError",
    "Usage",
    "WorkflowEnginePort",
    "WorkspaceHandle",
    "WorkspaceRequest",
    "build_bundle",
    "stage_gate",
]
