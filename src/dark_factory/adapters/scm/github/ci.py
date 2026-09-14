"""``CIPort`` over GitHub Actions and the Checks API (T-024, ADR-019 p.3).

``run_stage_job`` dispatches the configured workflow (``GitHubConfig.workflow_id``,
by default the T-031 ``factory.yml`` template) on the requested ref and returns
a deterministic job ref ``github:<slug>:<stage>:<run id>``. The head SHA is
resolved first so run lookups and gate reads key on the commit, not the ref
name. The workflow is expected to report its check run under the name
``dark-factory/<stage>`` on that SHA; ``gate_status`` maps its conclusion:

- ``success`` → ``GateStatus.PASSED``; ``failure``/``timed_out``/
  ``action_required``/``startup_failure`` → ``FAILED``;
- ``neutral``/``skipped`` → ``SKIPPED`` (the check did not apply);
- ``cancelled``/``stale`` → ``FAILED`` (a cancelled check must not pass as
  skipped); anything still queued or in progress → ``PENDING``.

Gate result and artifacts resolve through the job context recorded at dispatch
(in-memory, like the fake); an unknown job ref raises ``KeyError``.
"""

from dataclasses import dataclass
from typing import Any, Final

from dark_factory.adapters.scm.github.client import GitHubClient
from dark_factory.ports import (
    ArtifactRef,
    CIPort,
    GateResult,
    GateStatus,
    StageJobRequest,
    stage_gate,
)

CHECK_RUN_NAME_TEMPLATE: Final[str] = "dark-factory/{stage}"
"""Check-run name the dispatched workflow reports for its stage (T-031)."""

_FAILURE_CONCLUSIONS: Final[frozenset[str]] = frozenset(
    {"failure", "timed_out", "action_required", "startup_failure"}
)
_SKIPPED_CONCLUSIONS: Final[frozenset[str]] = frozenset({"neutral", "skipped"})


@dataclass(frozen=True, slots=True)
class _JobContext:
    """Dispatch context of one job ref, recorded at ``run_stage_job``."""

    slug: str
    stage: str
    sha: str
    run_id: int


class GitHubCI(CIPort):
    """``CIPort`` backed by Actions dispatches and the Checks API."""

    def __init__(self, client: GitHubClient, *, workflow_id: str) -> None:
        self._client = client
        self._workflow_id = workflow_id
        self._jobs: dict[str, _JobContext] = {}
        self._job_keys: dict[str, str] = {}

    async def run_stage_job(self, request: StageJobRequest, *, idempotency_key: str) -> str:
        replayed = self._job_keys.get(idempotency_key)
        if replayed is not None:
            return replayed
        sha = await self._resolve_ref(request.repository.slug, request.ref)
        dispatched = await self._client.request(
            "POST",
            f"/repos/{request.repository.slug}/actions/workflows/{self._workflow_id}/dispatches",
            json={"ref": request.ref, "inputs": {"stage": request.stage}},
        )
        self._client.expect(dispatched, 204)
        run_id = await self._latest_run_id(request.repository.slug, sha)
        job_ref = f"github:{request.repository.slug}:{request.stage}:{run_id}"
        self._jobs[job_ref] = _JobContext(
            slug=request.repository.slug, stage=request.stage, sha=sha, run_id=run_id
        )
        self._job_keys[idempotency_key] = job_ref
        return job_ref

    async def gate_status(self, job_ref: str, /) -> GateResult:
        context = self._job(job_ref)
        for run in await self._client.check_runs(context.slug, context.sha):
            if run.get("name") == CHECK_RUN_NAME_TEMPLATE.format(stage=context.stage):
                return _gate_result(run, context)
        return GateResult(
            gate=stage_gate(context.stage), status=GateStatus.PENDING, sha=context.sha
        )

    async def artifacts(self, job_ref: str, /) -> list[ArtifactRef]:
        context = self._job(job_ref)
        response = await self._client.request(
            "GET", f"/repos/{context.slug}/actions/runs/{context.run_id}/artifacts"
        )
        data = self._client.expect(response, 200).json()
        return [
            ArtifactRef(
                artifact_type=str(artifact["name"]),
                uri=str(artifact["archive_download_url"]),
                revision=context.sha,
            )
            for artifact in data.get("artifacts", [])
        ]

    def _job(self, job_ref: str) -> _JobContext:
        try:
            return self._jobs[job_ref]
        except KeyError:
            raise KeyError(f"unknown job ref {job_ref!r}") from None

    async def _resolve_ref(self, slug: str, ref: str) -> str:
        response = await self._client.request("GET", f"/repos/{slug}/commits/{ref}")
        if response.status_code == 404:
            raise KeyError(f"no revision recorded for {slug!r}@{ref!r}")
        return str(self._client.expect(response, 200).json()["sha"])

    async def _latest_run_id(self, slug: str, sha: str) -> int:
        """Run id of the newest dispatch on ``sha``.

        Two dispatches on the same SHA are assumed sequential for the MVP: a
        concurrent one could be picked here. Gate reads are unaffected — they
        route by the stage-named check run, not by this id.
        """
        response = await self._client.request(
            "GET", f"/repos/{slug}/actions/runs", params={"head_sha": sha}
        )
        runs = list(self._client.expect(response, 200).json().get("workflow_runs", []))
        if not runs:
            raise KeyError(f"no workflow run recorded for {slug!r}@{sha!r}")
        return int(max(runs, key=lambda run: int(run["id"]))["id"])


def _gate_result(run: dict[str, Any], context: _JobContext) -> GateResult:
    summary = (run.get("output") or {}).get("summary")
    if run.get("status") != "completed":
        status = GateStatus.PENDING
    elif (run.get("conclusion") or "") in _FAILURE_CONCLUSIONS:
        status = GateStatus.FAILED
    elif (run.get("conclusion") or "") in _SKIPPED_CONCLUSIONS:
        status = GateStatus.SKIPPED
    elif run.get("conclusion") == "success":
        status = GateStatus.PASSED
    else:
        # cancelled/stale and any unknown conclusion fail closed.
        status = GateStatus.FAILED
    return GateResult(
        gate=stage_gate(context.stage),
        status=status,
        sha=context.sha,
        summary=str(summary) if summary else None,
    )
