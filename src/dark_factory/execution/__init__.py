"""Execution layer of the factory core (HLD §7).

The layer holds two adapters: the run-record index of T-061 — publishing the
compact immutable evidence index of a run into the ``dark-factory-runs``
repository (ADR-015 p.4) — and the real ``ExecutionPort`` adapter of T-092
(TD-022): isolated git worktrees per agent stage, file writes, command
execution and evidence collection over an operator-prepared local mirror.
"""

from dark_factory.execution.runs.errors import (
    EvidenceChainError,
    RunRecordImmutabilityError,
    RunRecordStoreError,
    RunRecordTooLargeError,
    UnsafeRunRecordError,
)
from dark_factory.execution.runs.layout import (
    RUNS_DIR_NAME,
    resolve_ref_path,
    run_dir,
    slug,
)
from dark_factory.execution.runs.models import (
    PublishOutcome,
    PublishResult,
    RetentionClass,
    RunEvidenceEntry,
    RunEvidenceIndex,
    RunRecordRef,
    RunStageUsage,
    RunUsageSummary,
)
from dark_factory.execution.runs.sanitize import (
    MAX_RUN_RECORD_BYTES,
    UnsafeValue,
    check_payload_size,
    describe_unsafe_values,
    find_unsafe_values,
)
from dark_factory.execution.runs.store import (
    DECISIONS_NAME,
    EVIDENCE_INDEX_NAME,
    MANIFEST_NAME,
    SNAPSHOT_NAME,
    STAGES_DIR_NAME,
    USAGE_NAME,
    RunRecordStore,
    build_usage_summary,
    read_local_evidence,
    render_decisions,
)
from dark_factory.execution.workspace import (
    WORKSPACE_COMMAND_TIMEOUT_ENV_VAR,
    WORKSPACE_MIRROR_ROOT_ENV_VAR,
    WORKSPACE_ROOT_ENV_VAR,
    UnsafeWorkspacePath,
    WorkspaceError,
    WorktreeExecution,
    WorktreeExecutionConfig,
)

__all__ = [
    "DECISIONS_NAME",
    "EVIDENCE_INDEX_NAME",
    "MANIFEST_NAME",
    "MAX_RUN_RECORD_BYTES",
    "RUNS_DIR_NAME",
    "SNAPSHOT_NAME",
    "STAGES_DIR_NAME",
    "USAGE_NAME",
    "WORKSPACE_COMMAND_TIMEOUT_ENV_VAR",
    "WORKSPACE_MIRROR_ROOT_ENV_VAR",
    "WORKSPACE_ROOT_ENV_VAR",
    "EvidenceChainError",
    "PublishOutcome",
    "PublishResult",
    "RetentionClass",
    "RunEvidenceEntry",
    "RunEvidenceIndex",
    "RunRecordImmutabilityError",
    "RunRecordRef",
    "RunRecordStore",
    "RunRecordStoreError",
    "RunRecordTooLargeError",
    "RunStageUsage",
    "RunUsageSummary",
    "UnsafeRunRecordError",
    "UnsafeValue",
    "UnsafeWorkspacePath",
    "WorkspaceError",
    "WorktreeExecution",
    "WorktreeExecutionConfig",
    "build_usage_summary",
    "check_payload_size",
    "describe_unsafe_values",
    "find_unsafe_values",
    "read_local_evidence",
    "render_decisions",
    "resolve_ref_path",
    "run_dir",
    "slug",
]
