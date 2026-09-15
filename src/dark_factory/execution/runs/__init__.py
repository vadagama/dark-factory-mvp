"""Run-record index published into the ``dark-factory-runs`` repository (T-061).

Single public surface of the package: the store, its models, the layout helpers
and the screening functions. Layout of the package:

- :mod:`~dark_factory.execution.runs.store` — ``RunRecordStore``: the
  deterministic, idempotent, immutable writer plus the derived views of a
  record (usage summary, decisions table);
- :mod:`~dark_factory.execution.runs.models` — the index models
  (``RunEvidenceIndex``, ``RunUsageSummary``, ``RunRecordRef``);
- :mod:`~dark_factory.execution.runs.layout` — partitioning and path safety;
- :mod:`~dark_factory.execution.runs.sanitize` — secret and size screening;
- :mod:`~dark_factory.execution.runs.errors` — value-free failures.

The CLI face is ``factory run publish`` (:mod:`dark_factory.cli.runs`).
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

__all__ = [
    "DECISIONS_NAME",
    "EVIDENCE_INDEX_NAME",
    "MANIFEST_NAME",
    "MAX_RUN_RECORD_BYTES",
    "RUNS_DIR_NAME",
    "SNAPSHOT_NAME",
    "STAGES_DIR_NAME",
    "USAGE_NAME",
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
