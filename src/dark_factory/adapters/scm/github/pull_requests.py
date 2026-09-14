"""``MergeRequestPort`` over the GitHub pull-request API (T-024, ADR-019 p.3).

One domain type, ``ChangeRequestRef``, stands for a GitHub PR (ADR-019 p.2).
Behaviors mirror the fake the contract suite runs first:

- ``open`` is idempotent by ``idempotency_key`` (a replay returns the same PR)
  and additionally finds an existing PR from the same head branch first, so a
  crash between creating the PR and recording the key cannot open a duplicate.
  GitHub requires the head branch to exist, so ``open`` creates it at
  ``head_sha`` when it is missing — a safety net for the repository port's
  ``ensure_branch``, which the factory flow calls first.
- ``find_existing`` resolves ``change_id`` through an in-memory map, falling
  back to scanning repository PRs for the change marker embedded in the PR
  body at open time — the lookup survives a cold adapter (FR-011).
- ``add_comment`` is idempotent by ``idempotency_key``: the deterministic
  marker ``<!-- dark-factory:idempotency:<key> -->`` is posted with the body
  and replayed calls find it already present and post nothing. The marker is
  an HTML comment, invisible in the rendered PR.
- ``merge`` verifies the PR head against ``expected_sha`` (raising the shared
  ``HeadMismatchError`` on mismatch) before squashing; a replay of an already
  merged PR is a no-op.

Webhooks (the event-driven accelerator) and push/diff/rebase helpers are not
part of the port contract and are deferred (task list, ADR-019 p.3).
"""

from typing import Any, Final

from dark_factory.adapters.scm.github.client import GitHubClient
from dark_factory.ports import (
    ChangeRequestRef,
    ChangeRequestStatus,
    HeadMismatchError,
    MergeRequestPort,
    OpenChangeRequest,
    RepositoryRef,
)

_PER_PAGE: Final[int] = 100

CHANGE_MARKER_PREFIX: Final[str] = "<!-- dark-factory:change:"
CHANGE_MARKER_SUFFIX: Final[str] = " -->"
IDEMPOTENCY_MARKER_TEMPLATE: Final[str] = "<!-- dark-factory:idempotency:{key} -->"


def _change_marker(change_id: str) -> str:
    return f"{CHANGE_MARKER_PREFIX}{change_id}{CHANGE_MARKER_SUFFIX}"


def change_id_from_body(body: str, /) -> str | None:
    """The ``change_id`` embedded in a PR body, or ``None``."""
    start = body.find(CHANGE_MARKER_PREFIX)
    if start < 0:
        return None
    start += len(CHANGE_MARKER_PREFIX)
    end = body.find(CHANGE_MARKER_SUFFIX, start)
    return body[start:end] if end >= 0 else None


def _repo_key(repository: RepositoryRef) -> str:
    return repository.slug


class GitHubPullRequests(MergeRequestPort):
    """``MergeRequestPort`` backed by the pull-request and issue-comment APIs."""

    def __init__(self, client: GitHubClient) -> None:
        self._client = client
        self._by_change: dict[tuple[str, str], int] = {}
        self._open_keys: dict[str, ChangeRequestRef] = {}

    async def open(self, request: OpenChangeRequest, *, idempotency_key: str) -> ChangeRequestRef:
        replayed = self._open_keys.get(idempotency_key)
        if replayed is not None:
            # The fake builds the replayed ref from the record's live status.
            data = await self._get_pull(replayed.repository, replayed.number)
            return self._ref(replayed.repository, data) if data is not None else replayed
        existing = await self._find_by_head(request.repository, request.source_branch)
        if existing is not None:
            return self._record(request.repository, request.change_id, existing, idempotency_key)
        await self._ensure_head(request.repository, request.source_branch, request.head_sha)
        body = "\n\n".join(
            part for part in (request.description, _change_marker(request.change_id)) if part
        )
        created = await self._client.request(
            "POST",
            f"/repos/{request.repository.slug}/pulls",
            json={
                "title": request.title,
                "head": request.source_branch,
                "base": request.target_branch,
                "body": body,
            },
        )
        data = self._client.expect(created, 201).json()
        return self._record(request.repository, request.change_id, data, idempotency_key)

    async def find_existing(
        self, repository: RepositoryRef, change_id: str, /
    ) -> ChangeRequestRef | None:
        number = self._by_change.get((_repo_key(repository), change_id))
        if number is not None:
            data = await self._get_pull(repository, number)
            if data is not None:
                return self._ref(repository, data)
        for data in await self._list_pulls(repository):
            if change_id_from_body(data.get("body") or "") == change_id:
                return self._record(repository, change_id, data, idempotency_key=None)
        return None

    async def add_comment(self, cr: ChangeRequestRef, body: str, *, idempotency_key: str) -> None:
        marker = IDEMPOTENCY_MARKER_TEMPLATE.format(key=idempotency_key)
        existing = await self._client.request(
            "GET", f"/repos/{cr.repository.slug}/issues/{cr.number}/comments"
        )
        for comment in self._client.expect(existing, 200).json():
            if marker in (comment.get("body") or ""):
                return
        posted = await self._client.request(
            "POST",
            f"/repos/{cr.repository.slug}/issues/{cr.number}/comments",
            json={"body": f"{body}\n\n{marker}"},
        )
        self._client.expect(posted, 201)

    async def merge(self, cr: ChangeRequestRef, *, expected_sha: str, idempotency_key: str) -> None:
        data = await self._get_pull(cr.repository, cr.number)
        if data is None:
            raise KeyError(f"unknown change request {cr.repository.slug!r}#{cr.number}")
        head = str((data.get("head") or {}).get("sha") or "")
        if head != expected_sha:
            raise HeadMismatchError(
                f"head of {cr.repository.slug}#{cr.number} is {head}, expected {expected_sha}"
            )
        if data.get("merged"):
            return
        merged = await self._client.request(
            "PUT",
            f"/repos/{cr.repository.slug}/pulls/{cr.number}/merge",
            json={"merge_method": "squash"},
        )
        self._client.expect(merged, 200)

    async def _ensure_head(self, repository: RepositoryRef, branch: str, head_sha: str) -> None:
        """Create the head branch at ``head_sha`` when it does not exist yet."""
        response = await self._client.request(
            "GET", f"/repos/{repository.slug}/git/ref/heads/{branch}"
        )
        if response.status_code == 404:
            created = await self._client.request(
                "POST",
                f"/repos/{repository.slug}/git/refs",
                json={"ref": f"refs/heads/{branch}", "sha": head_sha},
            )
            self._client.expect(created, 201)
        else:
            self._client.expect(response, 200)

    async def _find_by_head(self, repository: RepositoryRef, branch: str) -> dict[str, Any] | None:
        pulls = await self._list_pulls(repository, state="open")
        for data in pulls:
            if (data.get("head") or {}).get("ref") == branch:
                return data
        return None

    async def _get_pull(self, repository: RepositoryRef, number: int) -> dict[str, Any] | None:
        response = await self._client.request("GET", f"/repos/{repository.slug}/pulls/{number}")
        if response.status_code == 404:
            return None
        data: dict[str, Any] = self._client.expect(response, 200).json()
        return data

    async def _list_pulls(
        self, repository: RepositoryRef, *, state: str = "all"
    ) -> list[dict[str, Any]]:
        response = await self._client.request(
            "GET",
            f"/repos/{repository.slug}/pulls",
            params={"state": state, "per_page": str(_PER_PAGE)},
        )
        if response.status_code == 404:
            return []
        return list(self._client.expect(response, 200).json())

    def _record(
        self,
        repository: RepositoryRef,
        change_id: str,
        data: dict[str, Any],
        idempotency_key: str | None,
    ) -> ChangeRequestRef:
        ref = self._ref(repository, data)
        self._by_change[(_repo_key(repository), change_id)] = ref.number
        if idempotency_key is not None:
            self._open_keys[idempotency_key] = ref
        return ref

    def _ref(self, repository: RepositoryRef, data: dict[str, Any]) -> ChangeRequestRef:
        return ChangeRequestRef(
            repository=repository,
            number=int(data["number"]),
            url=data.get("html_url"),
            status=_status(data),
        )


def _status(data: dict[str, Any]) -> ChangeRequestStatus:
    if data.get("merged"):
        return ChangeRequestStatus.MERGED
    if data.get("state") == "closed":
        return ChangeRequestStatus.CLOSED
    return ChangeRequestStatus.OPEN
