"""Common value types shared by the port contracts (contracts/ports.md)."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import TracebackType
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from dark_factory.changes.enums import ChangeRequestStatus, Gate, Stage
from dark_factory.changes.refs import RepositoryRef


@dataclass(frozen=True)
class HealthStatus:
    """Result of a port health probe."""

    healthy: bool
    detail: str | None = None


@dataclass(frozen=True)
class PipelineStatus:
    """Observed CI pipeline state, provider-neutral (ADR-019)."""

    ref: str
    """The git ref the pipeline runs on."""

    status: str
    """One of: queued | in_progress | success | failure | canceled."""

    url: str | None = None


@dataclass(frozen=True)
class ReviewObservation:
    """One human review recorded on a change request, provider-neutral (ADR-019 p.2)."""

    review_id: str
    """Provider id of the review; unique per change request."""

    author: str
    """Login of the reviewer."""

    state: str
    """One of: approved | changes_requested | commented | dismissed | pending."""

    commit_sha: str | None = None
    """The SHA the review was submitted for (version-bound approval, ADR-009 p.7)."""

    submitted_at: datetime | None = None
    """Aware instant the review was submitted, when the provider reports one."""


@dataclass(frozen=True)
class ChangeRequestObservation:
    """Live provider facts of one change request, read-only (T-092 S3, ADR-019 p.2).

    The read model of ``MergeRequestPort.observe``: the state a waiting stage
    waits on, in one value. ``head_sha`` is the current head of the request —
    the SHA the pipeline verdict and the version-bound approvals bind to;
    a provider that does not report a head returns ``None`` and the observer
    degrades honestly (FR-009: no fact is fabricated).
    """

    status: ChangeRequestStatus
    """Live status of the request in the provider's own terms."""

    head_sha: str | None = None
    """Current head commit of the request, when the provider reports one."""

    merged_sha: str | None = None
    """The merge commit, once the request is merged."""

    reviews: tuple[ReviewObservation, ...] = ()
    """Human reviews on the request, in submission order."""


class OpenChangeRequest(BaseModel):
    """Input of ``MergeRequestPort.open``.

    ``change_id`` identifies the factory change and backs deduplication through
    ``MergeRequestPort.find_existing`` (FR-011); ``head_sha`` is the source
    branch head the change request is opened for and is verified by
    ``MergeRequestPort.merge``.
    """

    model_config = ConfigDict(frozen=True)

    repository: RepositoryRef
    change_id: str = Field(min_length=1)
    source_branch: str = Field(min_length=1)
    target_branch: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str | None = None
    head_sha: str = Field(min_length=1)


class StageJobRequest(BaseModel):
    """Input of ``CIPort.run_stage_job``: one CI job for a factory stage.

    ``ref`` is the branch or SHA the job runs against; the gate the job's
    result evaluates is the stage's base gate (``stage_gate``).
    """

    model_config = ConfigDict(frozen=True)

    repository: RepositoryRef
    stage: str = Field(min_length=1)
    ref: str = Field(min_length=1)


_STAGE_GATES: Final[Mapping[str, Gate]] = {
    Stage.SPECIFICATION.value: Gate.SPECIFICATION,
    Stage.PLANNING.value: Gate.PLANNING,
    Stage.CONSTRUCTION.value: Gate.CODE,
    Stage.REVIEW_VERIFICATION.value: Gate.VERIFICATION,
    Stage.RELEASE.value: Gate.RELEASE,
}


def stage_gate(stage: str) -> Gate:
    """Base gate a stage's CI job evaluates (vision 3.9).

    The base gate is the CI-evaluable representative of the stage;
    ``dark_factory.rules.gates`` remains the owner of the full stage→gate
    policy (routes add gates a CI job does not evaluate).
    """
    try:
        return _STAGE_GATES[Stage(stage).value]
    except ValueError:
        raise ValueError(f"unknown stage {stage!r}") from None


class ArtifactSpec(BaseModel):
    """Input of ``ArtifactStorePort.put``: a content-addressed artifact description."""

    model_config = ConfigDict(frozen=True)

    artifact_type: str = Field(min_length=1)
    name: str = Field(min_length=1)
    content: bytes
    producer: str | None = None


@dataclass
class Span:
    """Minimal telemetry span handle: a plain value object owned by the port.

    Context-manager compatible so callers use ``with port.span(...) as span:``.
    The OTLP adapter (T-060) yields this value while an OpenTelemetry span lives
    underneath it, so the handle stays a provider-free record of the name and
    attributes the caller passed.
    """

    name: str
    attributes: dict[str, str] = field(default_factory=dict)

    def __enter__(self) -> "Span":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Complete the span; the minimal handle records nothing on exit."""
