"""Models of the compact run-record index published to ``dark-factory-runs`` (T-061).

ADR-015 p.4 fixes what a record contains: the ``RunManifest`` (the
cross-repository versioning protocol, p.5), the final ``StageResult``, the
approvals/decisions and the evidence references — URI plus checksum, never the
heavy payload (screenshots, logs, SBOM stay in CI artifacts behind
``ArtifactStorePort``). The models here are the *index* view of one record: what
the repository layout stores next to the self-contained ``RunRecord``
(:mod:`dark_factory.changes.run_records`).

``RunEvidenceIndex`` deliberately does not reuse ``EvidenceIndex`` of
:mod:`dark_factory.context.sdd.models`: that one is the verification index of a
ChangeSet under ``.factory/``, this one is the audit index of a finished run.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from dark_factory.changes.run import SCHEMA_VERSION, SchemaVersion
from dark_factory.changes.usage import BudgetSnapshot, Usage


class RetentionClass(StrEnum):
    """Retention class of one evidence entry (ADR-015 p.4, ADR-009 p.9).

    ``audit`` evidence gates terminal statuses and must outlive the longest
    human approval/retry/audit window (ADR-009 p.9); ``standard`` evidence
    shares the retention of the CI artifact it points at.
    """

    AUDIT = "audit"
    STANDARD = "standard"


class PublishOutcome(StrEnum):
    """Result of publishing one run record (idempotent write, ADR-015 p.4)."""

    CREATED = "created"
    UNCHANGED = "unchanged"


class RunEvidenceEntry(BaseModel):
    """URI, checksum and provenance of one evidence item of a run.

    ``checksum`` digests the referenced payload, never the payload itself: the
    entry stays reproducible from the immutable ``StageResult`` and keeps the
    repository compact (git-structure §8).
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    type: str = Field(min_length=1)
    uri: str = Field(min_length=1)
    checksum: str | None = None
    media_type: str | None = None
    producer: str | None = None
    created_at: datetime | None = None
    retention_class: RetentionClass = RetentionClass.STANDARD
    required: bool = False
    available: bool = True


class RunEvidenceIndex(BaseModel):
    """Compact evidence index of one run (``evidence-index.json``, ADR-015 p.4)."""

    model_config = ConfigDict(frozen=True)

    schema_version: SchemaVersion = SCHEMA_VERSION
    change_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    entries: tuple[RunEvidenceEntry, ...] = ()


class RunStageUsage(BaseModel):
    """Usage of one stage attempt inside the run usage summary."""

    model_config = ConfigDict(frozen=True)

    stage: str = Field(min_length=1)
    attempt_number: int = Field(ge=1)
    usage: Usage


class RunUsageSummary(BaseModel):
    """Token/cost summary of a run (``usage.json``, ADR-015 p.4/p.5).

    Aggregates are derived from the immutable ``StageResult``s; the budget
    snapshot is carried along because it survives between CI jobs (hld-mvp 8).
    """

    model_config = ConfigDict(frozen=True)

    schema_version: SchemaVersion = SCHEMA_VERSION
    run_id: str = Field(min_length=1)
    budget: BudgetSnapshot
    stages: tuple[RunStageUsage, ...] = ()
    totals: Usage = Field(default_factory=Usage)


class RunRecordRef(BaseModel):
    """Immutable address of a published record inside the runs repository.

    ``change_id`` + ``run_id`` + the relative path address the record
    idempotently (git-structure §8); ``digest`` is the sha256 of the record
    snapshot, so a reader can tell whether what it holds is what is on disk.
    """

    model_config = ConfigDict(frozen=True)

    change_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    relative_path: str = Field(min_length=1)
    digest: str = Field(min_length=1)


class PublishResult(BaseModel):
    """Outcome of a publish attempt: the address plus whether it was written."""

    model_config = ConfigDict(frozen=True)

    outcome: PublishOutcome
    ref: RunRecordRef
