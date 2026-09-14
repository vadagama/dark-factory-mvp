"""``PipelinePort`` over the GitHub Checks API (T-024, ADR-019 p.3).

``status`` reports the provider-neutral pipeline state for a ref from its
check runs: anything not completed keeps the pipeline ``in_progress``; once
all runs completed, a failing conclusion wins over a cancelled one, and only
otherwise-successful runs report ``success``. A ref without check runs (or an
unknown one) reports ``queued`` without a URL — the same default the in-memory
fake reports, so the contract suite sees identical behavior.
"""

from typing import Any, Final

from dark_factory.adapters.scm.github.client import GitHubClient
from dark_factory.ports import PipelinePort, PipelineStatus, RepositoryRef

_FAILURE_CONCLUSIONS: Final[frozenset[str]] = frozenset(
    {"failure", "timed_out", "action_required", "startup_failure"}
)
_CANCELLED_CONCLUSION: Final[str] = "cancelled"


class GitHubPipelines(PipelinePort):
    """``PipelinePort`` backed by the Checks API."""

    def __init__(self, client: GitHubClient) -> None:
        self._client = client

    async def status(self, repository: RepositoryRef, ref: str, /) -> PipelineStatus:
        runs = await self._client.check_runs(repository.slug, ref)
        if not runs:
            return PipelineStatus(ref=ref, status="queued", url=None)
        pending = next((run for run in runs if run.get("status") != "completed"), None)
        if pending is not None:
            return PipelineStatus(ref=ref, status="in_progress", url=_url(pending))
        failed = next(
            (run for run in runs if (run.get("conclusion") or "") in _FAILURE_CONCLUSIONS), None
        )
        if failed is not None:
            return PipelineStatus(ref=ref, status="failure", url=_url(failed))
        cancelled = next(
            (run for run in runs if (run.get("conclusion") or "") == _CANCELLED_CONCLUSION), None
        )
        if cancelled is not None:
            return PipelineStatus(ref=ref, status="canceled", url=_url(cancelled))
        return PipelineStatus(ref=ref, status="success", url=_url(runs[0]))


def _url(run: dict[str, Any]) -> str | None:
    url = run.get("html_url")
    return str(url) if url else None
