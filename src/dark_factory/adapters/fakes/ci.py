"""In-memory fake of the CI port (the GitHub Actions adapter is the real P0 provider)."""

from collections.abc import Sequence
from dataclasses import dataclass, field

from dark_factory.ports import (
    ArtifactRef,
    CIPort,
    GateResult,
    GateStatus,
    StageJobRequest,
    stage_gate,
)


@dataclass
class _Job:
    """Provider-side state of one stage job dispatched by the fake."""

    request: StageJobRequest
    gate: GateResult
    artifacts: list[ArtifactRef] = field(default_factory=list)


class FakeCI(CIPort):
    """In-memory ``CIPort``.

    - ``run_stage_job`` is idempotent by ``idempotency_key``: a replay returns
      the job ref recorded at the first call and dispatches nothing.
    - ``gate_status`` is deterministic: an unseeded job reports a pending gate
      for the dispatched ref; ``seed_outcome`` fixes a terminal outcome.
    - ``artifacts`` returns what ``seed_artifacts`` recorded, empty by default.
    """

    def __init__(self) -> None:
        self._jobs: dict[str, _Job] = {}
        self._job_keys: dict[str, str] = {}
        self._numbers: dict[tuple[str, str], int] = {}

    async def run_stage_job(self, request: StageJobRequest, *, idempotency_key: str) -> str:
        replayed = self._job_keys.get(idempotency_key)
        if replayed is not None:
            return replayed
        key = (request.repository.slug, request.stage)
        number = self._numbers.get(key, 0) + 1
        self._numbers[key] = number
        job_ref = f"fake:{request.repository.slug}:{request.stage}:{number}"
        self._jobs[job_ref] = _Job(
            request=request,
            gate=GateResult(
                gate=stage_gate(request.stage), status=GateStatus.PENDING, sha=request.ref
            ),
        )
        self._job_keys[idempotency_key] = job_ref
        return job_ref

    async def gate_status(self, job_ref: str, /) -> GateResult:
        return self._job(job_ref).gate

    async def artifacts(self, job_ref: str, /) -> list[ArtifactRef]:
        return list(self._job(job_ref).artifacts)

    def seed_outcome(self, job_ref: str, *, status: GateStatus, summary: str | None = None) -> None:
        """Fix the terminal gate outcome of a dispatched job."""
        job = self._job(job_ref)
        job.gate = job.gate.model_copy(update={"status": status, "summary": summary})

    def seed_artifacts(self, job_ref: str, artifacts: Sequence[ArtifactRef]) -> None:
        """Record the artifacts the job is expected to have produced."""
        self._job(job_ref).artifacts.extend(artifacts)

    def dispatch_count(self) -> int:
        """Number of dispatched jobs; a replay is counted only once."""
        return len(self._jobs)

    def _job(self, job_ref: str) -> _Job:
        try:
            return self._jobs[job_ref]
        except KeyError:
            raise KeyError(f"unknown job ref {job_ref!r}") from None
