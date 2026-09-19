"""``RepositoryPort`` over the GitHub REST API (T-024, ADR-019 p.3).

Branch/revision semantics mirror the fake the contract suite runs first:
``ensure_branch`` is idempotent by ``idempotency_key`` — a replay returns the
revision recorded at the first call even if the branch head moved since; a new
key on an existing branch creates no second branch and returns its current
head; a missing branch is created at ``from_revision``. ``get_revision``
resolves branches (and raw SHAs) through ``GET /repos/.../commits/{ref}``.

``publish_commit`` lands the workspace file set on the branch as one external
effect through the Git data API: a tree of the changes over the branch head's
tree, a commit object carrying the invisible idempotency marker in its
message, and a fast-forward move of the branch ref. Replay-dedup is
lookup-first, like ``MergeRequestPort.add_comment``: the commits of the branch
are scanned for the marker before anything is created, so a replay (or a cold
adapter after a crash between commit and bookkeeping) returns the recorded SHA
and creates no second commit. A missing branch is a ``KeyError`` (404 =
absent, the same convention as ``get_revision``).

The read methods (T082, ADR-035) use the contents API (``read_file``), the
recursive trees API of the ref's tree (``list_tree``) and the commits API
with its ``path`` filter (``list_commits``); an absent ref or path is a
``KeyError`` like everywhere else on the port.
"""

import base64
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Final

from dark_factory.adapters.scm.github.client import GitHubClient
from dark_factory.adapters.scm.github.pull_requests import IDEMPOTENCY_MARKER_TEMPLATE
from dark_factory.ports import CommitInfo, RepositoryPort, RepositoryRef

_PER_PAGE: Final[int] = 100


def _parse_instant(value: Any) -> datetime | None:
    """ISO-8601 instant of the commits API (``Z`` suffix) → aware datetime, or ``None``."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class GitHubRepository(RepositoryPort):
    """``RepositoryPort`` backed by the Git refs and Git data APIs."""

    def __init__(self, client: GitHubClient) -> None:
        self._client = client
        self._branch_keys: dict[str, str] = {}

    async def get_revision(self, repository: RepositoryRef, ref: str, /) -> str:
        response = await self._client.request("GET", f"/repos/{repository.slug}/commits/{ref}")
        if response.status_code in (404, 409, 422):
            # GitHub answers 404 for an unknown repository, 422 for a ref that
            # does not resolve (a missing branch or sha) and 409 for a repository
            # without a single commit yet ("Git Repository is empty", found on
            # the M3 live run against a freshly created product repository) —
            # all are "absent" for the port contract (a missing branch is a KeyError).
            raise KeyError(f"no revision recorded for {repository.slug!r}@{ref!r}")
        return str(self._client.expect(response, 200).json()["sha"])

    async def ensure_branch(
        self,
        repository: RepositoryRef,
        branch: str,
        *,
        from_revision: str,
        idempotency_key: str,
    ) -> str:
        replayed = self._branch_keys.get(idempotency_key)
        if replayed is not None:
            return replayed
        ref_response = await self._client.request(
            "GET", f"/repos/{repository.slug}/git/ref/heads/{branch}"
        )
        if ref_response.status_code == 200:
            head = str(ref_response.json()["object"]["sha"])
        elif ref_response.status_code == 404:
            head = from_revision
            created = await self._client.request(
                "POST",
                f"/repos/{repository.slug}/git/refs",
                json={"ref": f"refs/heads/{branch}", "sha": from_revision},
            )
            self._client.expect(created, 201)
        else:
            self._client.expect(ref_response, 200)
        self._branch_keys[idempotency_key] = head
        return head

    async def publish_commit(
        self,
        repository: RepositoryRef,
        branch: str,
        changes: Mapping[str, bytes],
        /,
        *,
        message: str,
        idempotency_key: str,
    ) -> str:
        if not changes:
            raise ValueError("publish_commit requires a non-empty change set")
        marker = IDEMPOTENCY_MARKER_TEMPLATE.format(key=idempotency_key)
        replayed = await self._marker_commit(repository, branch, marker)
        if replayed is not None:
            return replayed
        head = await self._head(repository, branch)
        tree = await self._create_tree(repository, head["tree"], changes)
        created = await self._client.request(
            "POST",
            f"/repos/{repository.slug}/git/commits",
            json={"message": f"{message}\n\n{marker}", "tree": tree, "parents": [head["sha"]]},
        )
        commit_sha = str(self._client.expect(created, 201).json()["sha"])
        moved = await self._client.request(
            "PATCH",
            f"/repos/{repository.slug}/git/refs/heads/{branch}",
            json={"sha": commit_sha},
        )
        self._client.expect(moved, 200)
        return commit_sha

    async def read_file(self, repository: RepositoryRef, ref: str, path: str, /) -> bytes:
        response = await self._client.request(
            "GET", f"/repos/{repository.slug}/contents/{path}", params={"ref": ref}
        )
        if response.status_code == 404:
            raise KeyError(f"no file {path!r} at {repository.slug!r}@{ref!r}")
        data: dict[str, Any] = self._client.expect(response, 200).json()
        if isinstance(data, list) or data.get("type") != "file":
            raise KeyError(f"{path!r} at {repository.slug!r}@{ref!r} is not a file")
        encoding = data.get("encoding")
        content = str(data.get("content") or "")
        if encoding == "base64":
            return base64.b64decode(content)
        return content.encode("utf-8")

    async def list_tree(
        self, repository: RepositoryRef, ref: str, /, *, prefix: str = ""
    ) -> Sequence[str]:
        head = await self._head(repository, ref)
        if not head["tree"]:
            return ()
        response = await self._client.request(
            "GET",
            f"/repos/{repository.slug}/git/trees/{head['tree']}",
            params={"recursive": "1"},
        )
        data: dict[str, Any] = self._client.expect(response, 200).json()
        paths = [
            str(entry["path"])
            for entry in data.get("tree", [])
            if entry.get("type") == "blob" and str(entry["path"]).startswith(prefix)
        ]
        return tuple(sorted(paths))

    async def list_commits(
        self, repository: RepositoryRef, ref: str, /, *, path: str | None = None
    ) -> Sequence[CommitInfo]:
        params = {"sha": ref, "per_page": str(_PER_PAGE)}
        if path is not None:
            params["path"] = path
        response = await self._client.request(
            "GET", f"/repos/{repository.slug}/commits", params=params
        )
        if response.status_code in (404, 409, 422):
            # 409 — an empty repository (no commit yet): nothing recorded, like 404/422.
            raise KeyError(f"no revision recorded for {repository.slug!r}@{ref!r}")
        commits: list[CommitInfo] = []
        for item in self._client.expect(response, 200).json():
            info: dict[str, Any] = item.get("commit") or {}
            author: dict[str, Any] = info.get("author") or {}
            authored_at = _parse_instant(author.get("date"))
            commits.append(
                CommitInfo(
                    sha=str(item["sha"]),
                    message=str(info.get("message") or ""),
                    author=author.get("name") or (item.get("author") or {}).get("login"),
                    authored_at=authored_at,
                )
            )
        return tuple(commits)

    async def _marker_commit(
        self, repository: RepositoryRef, branch: str, marker: str
    ) -> str | None:
        """The commit on ``branch`` already carrying ``marker``, or ``None``.

        The scan survives a cold adapter: the marker lives in the provider-side
        commit message, not in adapter memory (FR-017). A missing branch yields
        an empty history here; the head lookup below reports the absence as a
        ``KeyError``.
        """
        response = await self._client.request(
            "GET",
            f"/repos/{repository.slug}/commits",
            params={"sha": branch, "per_page": str(_PER_PAGE)},
        )
        if response.status_code == 404:
            return None
        for commit in self._client.expect(response, 200).json():
            info: dict[str, Any] = commit.get("commit") or {}
            if marker in str(info.get("message") or ""):
                return str(commit["sha"])
        return None

    async def _head(self, repository: RepositoryRef, branch: str) -> dict[str, str]:
        """Head SHA and tree SHA of ``branch``; a missing branch is a ``KeyError``."""
        response = await self._client.request("GET", f"/repos/{repository.slug}/commits/{branch}")
        if response.status_code in (404, 422):
            # 422, not 404, is what GitHub answers for a ref that does not
            # resolve; both mean the branch is absent (see ``get_revision``).
            raise KeyError(f"no revision recorded for {repository.slug!r}@{branch!r}")
        data: dict[str, Any] = self._client.expect(response, 200).json()
        info: dict[str, Any] = data.get("commit") or {}
        tree: dict[str, Any] = info.get("tree") or {}
        return {"sha": str(data["sha"]), "tree": str(tree.get("sha") or "")}

    async def _create_tree(
        self, repository: RepositoryRef, base_tree: str, changes: Mapping[str, bytes]
    ) -> str:
        """One tree carrying ``changes`` over ``base_tree`` (empty base = new tree).

        The MVP transfer unit is text: the role tools write UTF-8 files, and the
        trees API takes file content as a string. Binary payloads are not
        expressible here (the fake passes bytes through; this adapter would
        need the blob API first).
        """
        entries = [
            {"path": path, "mode": "100644", "type": "blob", "content": content.decode("utf-8")}
            for path, content in sorted(changes.items())
        ]
        payload: dict[str, Any] = {"tree": entries}
        if base_tree:
            payload["base_tree"] = base_tree
        response = await self._client.request(
            "POST", f"/repos/{repository.slug}/git/trees", json=payload
        )
        return str(self._client.expect(response, 201).json()["sha"])
