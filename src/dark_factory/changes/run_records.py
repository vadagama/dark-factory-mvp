"""Run records: versioned envelope and JSON/YAML round-trips (T-003).

A run record is the compact immutable evidence index persisted to the
``dark-factory-runs`` repository (ADR-015 p.4) and the durable form of stage
results between CI jobs (ADR-005 p.3). ``schema_version`` marks the versioned
contract; serialization goes through JSON mode so ``Decimal`` and ``datetime``
survive both JSON and YAML.

The release evidence models live in :mod:`dark_factory.changes.release_records`
(a leaf module, so ``run`` can reference them too — T-092 S4) and are
re-exported here for the T034 import path.
"""

import yaml
from pydantic import BaseModel, Field, field_validator

from dark_factory.changes.findings import Decision
from dark_factory.changes.release_records import (
    ReleaseEvidence as ReleaseEvidence,
)
from dark_factory.changes.release_records import (
    SmokeProbeEvidence as SmokeProbeEvidence,
)
from dark_factory.changes.run import (
    SCHEMA_VERSION,
    Change,
    ChangeRun,
    SchemaVersion,
    StageResult,
    completion_violations,
)


class RunManifest(BaseModel):
    """Cross-repository versioning protocol of a run (ADR-015 p.5).

    A run record references exact revisions of all inputs; otherwise the run
    cannot be reproduced. Mutable refs are rejected: a value that resolves to
    ``latest`` pins nothing, so it must never enter the record.
    """

    factory_version: str = Field(min_length=1)
    factory_commit: str = Field(min_length=1)
    pack_name: str | None = None
    pack_version: str | None = None
    blueprint_version: str | None = None
    product_commit: str = Field(min_length=1)
    gitops_commit: str | None = None
    okf_revision: str | None = None

    @field_validator(
        "factory_version",
        "factory_commit",
        "pack_version",
        "blueprint_version",
        "product_commit",
        "gitops_commit",
        "okf_revision",
    )
    @classmethod
    def _ref_is_immutable(cls, value: str | None) -> str | None:
        if value is not None and value.strip().lower() == "latest":
            raise ValueError("run records reference exact revisions, not 'latest' (ADR-015 p.5)")
        return value


class RunRecord(BaseModel):
    """Compact evidence index of a run: manifest, change, run state, results, decisions."""

    schema_version: SchemaVersion = SCHEMA_VERSION
    manifest: RunManifest
    change: Change
    run: ChangeRun
    stage_results: list[StageResult] = []
    decisions: list[Decision] = []
    release: ReleaseEvidence | None = None
    """Release verification section (T034, ADR-011 p.6); ``None`` for records
    written before T034 and for non-release runs."""

    def completion_violations(self) -> list[str]:
        """Completion invariants (ADR-009 p.9) evaluated over this record."""
        return completion_violations(self.run, self.stage_results)


def to_json(model: BaseModel) -> str:
    """Serialize any domain model to pretty JSON text."""
    return model.model_dump_json(indent=2)


def from_json[T: BaseModel](cls: type[T], text: str) -> T:
    """Parse JSON text produced by :func:`to_json` back into ``cls``."""
    return cls.model_validate_json(text)


def to_yaml(model: BaseModel) -> str:
    """Serialize any domain model to YAML text via its JSON-mode dump."""
    return yaml.safe_dump(model.model_dump(mode="json"), sort_keys=False, allow_unicode=True)


def from_yaml[T: BaseModel](cls: type[T], text: str) -> T:
    """Parse YAML text produced by :func:`to_yaml` back into ``cls``."""
    return cls.model_validate(yaml.safe_load(text))
