"""In-memory GitHub REST API emulator for the contract suite (ADR-019 p.6).

Serves exactly the endpoints the GitHub adapter uses, over
``httpx2.MockTransport``, from deterministic dict state: no network, no wall
clock, no random ids. State is fresh per emulator instance, so every test
starts from the same repository (``main`` at ``DEFAULT_HEAD``).

The emulator enforces one authentication rule — API routes (everything except
the installation-token exchange) must carry the installation token as a bearer
— so the adapter's auth plumbing is exercised without real keys. Test-side
helpers (``parse_job_ref``, ``visible_comment``, ``commit_journal``,
``commit_files``) encode the adapter's documented conventions: the job-ref
format, the invisible idempotency markers inside PR comments and commit
messages, and the Git data API (trees, commit objects, ref updates) behind
``publish_commit``.
"""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Final

import httpx2

from dark_factory.ports import ArtifactRef

OWNER = "small"
REPO = "pilot"
SLUG = f"{OWNER}/{REPO}"
GITHUB_API_BASE_URL = "https://api.github.test"
GITHUB_INSTALLATION_TOKEN = "gh-test-installation-token"
DEFAULT_HEAD = "9f8e7d6"

_IDEMPOTENCY_MARKER: Final[re.Pattern[str]] = re.compile(r"<!-- dark-factory:idempotency:[^>]* -->")


def visible_comment(body: str, /) -> str:
    """Comment text as the provider renders it: idempotency markers are HTML comments."""
    return _IDEMPOTENCY_MARKER.sub("", body).strip()


def parse_job_ref(job_ref: str, /) -> tuple[str, str, int]:
    """``(slug, stage, run id)`` from the adapter's ``github:<slug>:<stage>:<run>`` ref."""
    slug, stage, run_id = job_ref.removeprefix("github:").rsplit(":", 2)
    return slug, stage, int(run_id)


@dataclass
class _Commit:
    """Provider-side state of one commit object created through the Git data API."""

    sha: str
    message: str
    tree: str
    parents: tuple[str, ...]


@dataclass
class _Pull:
    """Provider-side state of one pull request."""

    number: int
    head_branch: str
    head_sha: str
    body: str
    merged: bool = False
    closed: bool = False
    comments: list[str] = field(default_factory=list)


@dataclass
class _Review:
    """Provider-side state of one review submitted on a pull request."""

    review_id: int
    author: str
    state: str
    commit_sha: str | None
    submitted_at: str | None


@dataclass
class _Run:
    """Provider-side state of one workflow run created by a dispatch."""

    id: int
    head_sha: str
    event: str


@dataclass
class _CheckRun:
    """Provider-side state of one check run reported on a head SHA."""

    name: str
    head_sha: str
    status: str
    conclusion: str | None
    summary: str | None


class GitHubApiEmulator:
    """Stateful subset of the GitHub REST API the adapter talks to."""

    def __init__(self, *, token: str = GITHUB_INSTALLATION_TOKEN) -> None:
        self._token = token
        self.branches: dict[str, str] = {"main": DEFAULT_HEAD}
        self.commits: dict[str, _Commit] = {}
        self.trees: dict[str, dict[str, bytes]] = {}
        self._next_object_id = 0
        self._pulls: dict[int, _Pull] = {}
        self._next_pull_number = 1
        self._reviews: dict[int, list[_Review]] = {}
        self._next_review_id = 1
        self._check_runs: list[_CheckRun] = []
        self._next_check_run_id = 1
        self._runs: dict[int, _Run] = {}
        self._next_run_id = 1
        self._artifacts: dict[int, list[dict[str, Any]]] = {}
        self.installation_token_requests = 0
        self.last_installation_jwt: str | None = None

    # --- test-side views and seeds ----------------------------------------

    def comments_of(self, number: int, /) -> tuple[str, ...]:
        """Raw comment bodies of a pull request (markers included)."""
        return tuple(self._pulls[number].comments)

    def commit_journal(self, branch: str, /) -> tuple[str, ...]:
        """Commit SHAs on ``branch``, oldest first (parent walk over commit objects).

        A branch head that is a raw SHA without a commit object (a branch
        created at a revision the emulator holds no commit for) ends the walk,
        so the journal counts only commits created through the Git data API.
        """
        shas: list[str] = []
        current: str | None = self.branches.get(branch)
        while current is not None and current in self.commits:
            shas.append(current)
            current = self.commits[current].parents[0] if self.commits[current].parents else None
        return tuple(reversed(shas))

    def commit_files(self, sha: str, /) -> dict[str, bytes]:
        """The file set a commit carries (the tree it points at)."""
        try:
            return dict(self.trees[self.commits[sha].tree])
        except KeyError:
            raise KeyError(f"unknown commit {sha!r}") from None

    def run_count(self) -> int:
        """Number of workflow runs; dispatches are their only origin."""
        return len(self._runs)

    def seed_check_run(
        self,
        run_id: int,
        /,
        *,
        name: str,
        conclusion: str,
        summary: str | None = None,
    ) -> None:
        """Report a completed check run on the head SHA of a dispatched run."""
        run = self._runs[run_id]
        self._check_runs.append(
            _CheckRun(
                name=name,
                head_sha=run.head_sha,
                status="completed",
                conclusion=conclusion,
                summary=summary,
            )
        )

    def seed_artifacts(self, run_id: int, /, refs: Sequence[ArtifactRef]) -> None:
        """Attach artifacts to a dispatched run; the adapter maps them back."""
        self._artifacts[run_id] = [
            {"id": index, "name": ref.artifact_type, "archive_download_url": ref.uri}
            for index, ref in enumerate(refs, start=1)
        ]

    def seed_review(
        self,
        number: int,
        /,
        *,
        author: str,
        state: str,
        commit_sha: str | None = None,
        submitted_at: str | None = None,
    ) -> int:
        """Submit one review on a pull request; returns its provider review id.

        ``state`` is the GitHub API vocabulary (``APPROVED``,
        ``CHANGES_REQUESTED``, ...); the adapter maps it to the port's
        provider-neutral lowercase value.
        """
        if number not in self._pulls:
            raise KeyError(f"unknown pull request #{number}")
        review_id = self._next_review_id
        self._next_review_id += 1
        self._reviews.setdefault(number, []).append(
            _Review(
                review_id=review_id,
                author=author,
                state=state,
                commit_sha=commit_sha,
                submitted_at=submitted_at,
            )
        )
        return review_id

    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self._handle)

    # --- request handling --------------------------------------------------

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if path.startswith("/app/installations/"):
            return self._installation_token(request)
        if request.headers.get("Authorization") != f"Bearer {self._token}":
            return _json_response(401, {"message": "Bad credentials"})
        if path.startswith(f"/repos/{SLUG}/"):
            return self._repo_route(request, path.removeprefix(f"/repos/{SLUG}/"))
        return _json_response(404, {"message": "Not Found"})

    def _installation_token(self, request: httpx2.Request) -> httpx2.Response:
        if not request.headers.get("Authorization", "").startswith("Bearer "):
            return _json_response(401, {"message": "Bad credentials"})
        self.installation_token_requests += 1
        self.last_installation_jwt = request.headers["Authorization"].removeprefix("Bearer ")
        return _json_response(201, {"token": self._token, "expires_at": "2026-09-14T13:00:00Z"})

    def _repo_route(self, request: httpx2.Request, route: str) -> httpx2.Response:
        method = request.method
        if route == "commits" and method == "GET":
            return self._commits_list(request.url.params)
        if route.startswith("commits/") and route.endswith("/check-runs"):
            ref = route.removeprefix("commits/").removesuffix("/check-runs")
            return self._check_runs_route(ref)
        if route.startswith("commits/"):
            return self._commit_route(route.removeprefix("commits/"))
        if route.startswith("git/ref/heads/"):
            return self._git_ref_route(route.removeprefix("git/ref/heads/"))
        if route == "git/refs" and method == "POST":
            return self._git_refs_create(_body(request))
        if route.startswith("git/refs/heads/") and method == "PATCH":
            return self._git_ref_update(route.removeprefix("git/refs/heads/"), _body(request))
        if route == "git/trees" and method == "POST":
            return self._git_trees_create(_body(request))
        if route == "git/commits" and method == "POST":
            return self._git_commits_create(_body(request))
        if route == "pulls" and method == "GET":
            return self._pulls_list(request.url.params)
        if route == "pulls" and method == "POST":
            return self._pulls_create(_body(request))
        if route.startswith("pulls/"):
            return self._pull_route(request, route.removeprefix("pulls/"))
        if route.startswith("issues/") and route.endswith("/comments"):
            number = int(route.removeprefix("issues/").removesuffix("/comments"))
            return self._comments_route(request, number)
        if route == "actions/runs" and method == "GET":
            return self._runs_list(request.url.params)
        if route.startswith("actions/runs/") and route.endswith("/artifacts"):
            run_id = int(route.removeprefix("actions/runs/").removesuffix("/artifacts"))
            return self._artifacts_route(run_id)
        if route.startswith("actions/workflows/") and route.endswith("/dispatches"):
            return self._dispatch_route(_body(request))
        return _json_response(404, {"message": "Not Found"})

    def _commit_route(self, ref: str) -> httpx2.Response:
        sha = self._resolve_ref(ref)
        if sha is None:
            # The real GitHub answers 422 (not 404) for a ref that does not
            # resolve on this endpoint; the adapter maps it to KeyError like
            # the 404 of an unknown repository.
            return _json_response(422, {"message": "No commit found for SHA: " + ref})
        commit = self.commits.get(sha)
        if commit is not None:
            return _json_response(200, self._commit_json(commit))
        # A raw SHA the emulator holds no commit object for (a branch seeded at
        # a revision): ``get_revision`` reads only ``sha``, tree-less is honest.
        return _json_response(200, {"sha": sha})

    def _commits_list(self, params: httpx2.QueryParams) -> httpx2.Response:
        ref = params.get("sha") or "main"
        head = self._resolve_ref(ref)
        if head is None:
            return _json_response(404, {"message": "No commit found for SHA: " + ref})
        commits: list[dict[str, Any]] = []
        current: str | None = head
        while current is not None and current in self.commits:
            commit = self.commits[current]
            commits.append(self._commit_json(commit))
            current = commit.parents[0] if commit.parents else None
        return _json_response(200, commits)

    def _commit_json(self, commit: _Commit) -> dict[str, Any]:
        return {
            "sha": commit.sha,
            "commit": {"message": commit.message, "tree": {"sha": commit.tree}},
            "parents": [{"sha": parent} for parent in commit.parents],
        }

    def _git_ref_update(self, branch: str, body: dict[str, Any]) -> httpx2.Response:
        if branch not in self.branches:
            return _json_response(404, {"message": "Not Found"})
        sha = str(body.get("sha") or "")
        if not self._is_descendant(sha, self.branches[branch]):
            return _json_response(422, {"message": "Update is not a fast forward"})
        self.branches[branch] = sha
        ref = {"ref": f"refs/heads/{branch}", "object": {"sha": sha, "type": "commit"}}
        return _json_response(200, ref)

    def _git_trees_create(self, body: dict[str, Any]) -> httpx2.Response:
        base_tree = str(body.get("base_tree") or "")
        if base_tree and base_tree not in self.trees:
            return _json_response(422, {"message": "Base tree does not exist"})
        entries: dict[str, bytes] = dict(self.trees.get(base_tree, {}))
        for entry in body.get("tree", []):
            if entry.get("type", "blob") != "blob":
                return _json_response(422, {"message": "Unsupported tree entry type"})
            entries[str(entry["path"])] = str(entry.get("content") or "").encode("utf-8")
        tree_sha = self._next_sha()
        self.trees[tree_sha] = entries
        return _json_response(201, {"sha": tree_sha})

    def _git_commits_create(self, body: dict[str, Any]) -> httpx2.Response:
        tree = str(body.get("tree") or "")
        if tree not in self.trees:
            return _json_response(422, {"message": "Tree does not exist"})
        parents = tuple(str(parent) for parent in body.get("parents", []))
        if not parents:
            return _json_response(422, {"message": "parents is required"})
        if any(self._resolve_ref(parent) is None for parent in parents):
            return _json_response(422, {"message": "parents is invalid"})
        sha = self._next_sha()
        self.commits[sha] = _Commit(
            sha=sha, message=str(body.get("message") or ""), tree=tree, parents=parents
        )
        return _json_response(201, self._commit_json(self.commits[sha]))

    def _git_ref_route(self, branch: str) -> httpx2.Response:
        sha = self.branches.get(branch)
        if sha is None:
            return _json_response(404, {"message": "Not Found"})
        return _json_response(
            200, {"ref": f"refs/heads/{branch}", "object": {"sha": sha, "type": "commit"}}
        )

    def _git_refs_create(self, body: dict[str, Any]) -> httpx2.Response:
        branch = str(body.get("ref", "")).removeprefix("refs/heads/")
        if branch in self.branches:
            return _json_response(422, {"message": "Reference already exists"})
        self.branches[branch] = str(body["sha"])
        ref = {"ref": f"refs/heads/{branch}", "object": {"sha": body["sha"], "type": "commit"}}
        return _json_response(201, ref)

    def _pulls_list(self, params: httpx2.QueryParams) -> httpx2.Response:
        state = params.get("state", "open")
        head = params.get("head")
        owner_prefix = f"{OWNER}:"
        pulls = [self._pull_json(pull) for pull in self._pulls.values()]
        if state == "open":
            pulls = [pull for pull in pulls if pull["state"] == "open"]
        if head is not None:
            branch = head.removeprefix(owner_prefix)
            pulls = [pull for pull in pulls if pull["head"]["ref"] == branch]
        return _json_response(200, pulls)

    def _pulls_create(self, body: dict[str, Any]) -> httpx2.Response:
        branch = str(body["head"])
        for pull in self._pulls.values():
            if pull.head_branch == branch and not pull.closed and not pull.merged:
                return _json_response(422, {"message": "A pull request already exists"})
        number = self._next_pull_number
        self._next_pull_number += 1
        self._pulls[number] = _Pull(
            number=number,
            head_branch=branch,
            head_sha=self._resolve_ref(branch) or "",
            body=str(body.get("body") or ""),
        )
        return _json_response(201, self._pull_json(self._pulls[number]))

    def _pull_route(self, request: httpx2.Request, route: str) -> httpx2.Response:
        if route.endswith("/merge"):
            return self._pull_merge(int(route.removesuffix("/merge")))
        if route.endswith("/reviews") and request.method == "GET":
            return self._pull_reviews(int(route.removesuffix("/reviews")))
        pull = self._pulls.get(int(route))
        if pull is None:
            return _json_response(404, {"message": "Not Found"})
        return _json_response(200, self._pull_json(pull))

    def _pull_reviews(self, number: int) -> httpx2.Response:
        if number not in self._pulls:
            return _json_response(404, {"message": "Not Found"})
        reviews = [self._review_json(review) for review in self._reviews.get(number, [])]
        return _json_response(200, reviews)

    def _pull_merge(self, number: int) -> httpx2.Response:
        pull = self._pulls.get(number)
        if pull is None:
            return _json_response(404, {"message": "Not Found"})
        if pull.merged or pull.closed:
            return _json_response(405, {"message": "Pull Request is not mergeable"})
        pull.merged = True
        return _json_response(200, {"merged": True, "sha": pull.head_sha})

    def _comments_route(self, request: httpx2.Request, number: int) -> httpx2.Response:
        pull = self._pulls.get(number)
        if pull is None:
            return _json_response(404, {"message": "Not Found"})
        if request.method == "GET":
            comments = [
                {"id": index, "body": body} for index, body in enumerate(pull.comments, start=1)
            ]
            return _json_response(200, comments)
        body = str(_body(request).get("body") or "")
        pull.comments.append(body)
        return _json_response(201, {"id": len(pull.comments), "body": body})

    def _check_runs_route(self, ref: str) -> httpx2.Response:
        sha = self._resolve_ref(ref)
        if sha is None:
            return _json_response(404, {"message": "Not Found"})
        runs = [self._check_run_json(run) for run in self._check_runs if run.head_sha == sha]
        return _json_response(200, {"total_count": len(runs), "check_runs": runs})

    def _dispatch_route(self, body: dict[str, Any]) -> httpx2.Response:
        sha = self._resolve_ref(str(body.get("ref") or ""))
        if sha is None:
            return _json_response(422, {"message": "No ref found for: " + str(body.get("ref"))})
        run_id = self._next_run_id
        self._next_run_id += 1
        self._runs[run_id] = _Run(id=run_id, head_sha=sha, event="workflow_dispatch")
        return httpx2.Response(status_code=204)

    def _runs_list(self, params: httpx2.QueryParams) -> httpx2.Response:
        head_sha = params.get("head_sha")
        runs = [self._run_json(run) for run in self._runs.values()]
        if head_sha is not None:
            runs = [run for run in runs if run["head_sha"] == head_sha]
        return _json_response(200, {"total_count": len(runs), "workflow_runs": runs})

    def _artifacts_route(self, run_id: int) -> httpx2.Response:
        if run_id not in self._runs:
            return _json_response(404, {"message": "Not Found"})
        artifacts = self._artifacts.get(run_id, [])
        return _json_response(200, {"total_count": len(artifacts), "artifacts": artifacts})

    def _resolve_ref(self, ref: str) -> str | None:
        """The commit SHA of a branch name or a raw (7-40 hex chars) SHA."""
        if ref in self.branches:
            return self.branches[ref]
        if re.fullmatch(r"[0-9a-f]{7,40}", ref):
            return ref
        return None

    def _next_sha(self) -> str:
        """Deterministic 40-hex SHA of the next Git object (no wall clock, no random)."""
        self._next_object_id += 1
        return f"{self._next_object_id:040x}"

    def _is_descendant(self, sha: str, ancestor: str) -> bool:
        """Whether ``sha`` can land on ``ancestor`` by a fast-forward ref update."""
        seen: set[str] = set()
        frontier: list[str] = [sha]
        while frontier:
            current = frontier.pop()
            if current == ancestor:
                return True
            if current in seen:
                continue
            seen.add(current)
            commit = self.commits.get(current)
            if commit is not None:
                frontier.extend(commit.parents)
        return False

    def _pull_json(self, pull: _Pull) -> dict[str, Any]:
        return {
            "id": pull.number,
            "number": pull.number,
            "state": "closed" if (pull.merged or pull.closed) else "open",
            "merged": pull.merged,
            "merge_commit_sha": pull.head_sha if pull.merged else None,
            "title": f"Change {pull.number}",
            "head": {"ref": pull.head_branch, "sha": pull.head_sha},
            "base": {"ref": "main"},
            "html_url": f"https://github.example/{SLUG}/pull/{pull.number}",
            "body": pull.body,
        }

    def _review_json(self, review: _Review) -> dict[str, Any]:
        return {
            "id": review.review_id,
            "user": {"login": review.author},
            "state": review.state,
            "commit_id": review.commit_sha,
            "submitted_at": review.submitted_at,
        }

    def _run_json(self, run: _Run) -> dict[str, Any]:
        return {
            "id": run.id,
            "name": "factory",
            "head_sha": run.head_sha,
            "event": run.event,
            "status": "queued",
            "html_url": f"https://github.example/{SLUG}/actions/runs/{run.id}",
        }

    def _check_run_json(self, run: _CheckRun) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self._next_check_run_id,
            "name": run.name,
            "head_sha": run.head_sha,
            "status": run.status,
            "conclusion": run.conclusion,
            "html_url": f"https://github.example/{SLUG}/checks",
            "output": {},
        }
        self._next_check_run_id += 1
        if run.summary is not None:
            payload["output"] = {"summary": run.summary}
        return payload


def _body(request: httpx2.Request) -> dict[str, Any]:
    return dict(json.loads(request.content or b"{}"))


def _json_response(status_code: int, payload: Any) -> httpx2.Response:
    return httpx2.Response(status_code=status_code, json=payload)
