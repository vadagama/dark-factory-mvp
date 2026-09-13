"""Reference objects: repositories, change requests, artifacts and evidence (T-003)."""

from datetime import datetime

from pydantic import BaseModel, Field

from dark_factory.changes.enums import ChangeRequestStatus, EvidenceType, Provider


class RepositoryRef(BaseModel):
    """Provider-neutral product repository reference (ADR-019)."""

    provider: Provider
    slug: str = Field(min_length=1)


class ChangeRequestRef(BaseModel):
    """Single domain model of a GitHub PR / GitLab MR (ADR-019 p.2)."""

    repository: RepositoryRef
    number: int = Field(ge=1)
    url: str | None = None
    status: ChangeRequestStatus


class ArtifactRef(BaseModel):
    """Reference to a produced artifact: type, URI, revision, hash, producer (hld-mvp 5)."""

    artifact_type: str = Field(min_length=1)
    uri: str = Field(min_length=1)
    revision: str | None = None
    sha256: str | None = None
    producer: str | None = None


class Evidence(BaseModel):
    """Proof attached to a result: URI + checksum in run records (ADR-015 p.4).

    ``required``/``available`` back the completion invariant of ADR-009 p.9:
    a successful terminal status is forbidden while required evidence is
    unavailable.
    """

    id: str = Field(min_length=1)
    type: EvidenceType
    uri: str = Field(min_length=1)
    checksum: str | None = None
    produced_at: datetime | None = None
    required: bool = False
    available: bool = True
