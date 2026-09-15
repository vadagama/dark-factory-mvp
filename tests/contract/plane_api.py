"""In-memory Plane REST API emulator for the contract suite (T-033, ADR-013).

Serves exactly the endpoints the Plane tracker adapter uses, over
``httpx2.MockTransport``, from deterministic dict state: no network, no wall
clock, no random ids. State is fresh per emulator instance, so every test
starts from an empty workspace and seeds the issue it needs.

The emulator enforces one authentication rule — every API route must carry the
``X-API-Key`` token — so the adapter's auth plumbing is exercised without a
real Plane. Test-side helpers encode the adapter's documented conventions: the
invisible idempotency markers inside comments, the ``factory status: …`` /
``factory approval requested: …`` comment vocabulary, and the ``risk:R2``
labels the risk class is read from.
"""

import json
import re
from collections.abc import Iterable
from typing import Any, Final

import httpx2

from dark_factory.changes.run import Change
from dark_factory.ports import Gate

PLANE_API_BASE_URL = "https://plane.test"
PLANE_API_TOKEN = "plane-test-api-key"
WORKSPACE_SLUG = "small"
PROJECT_ID = "11111111-2222-3333-4444-555555555555"
ISSUE_KEY = "PLANE-42"
ISSUE_CREATED_AT = "2026-09-13T09:30:00+00:00"

_IDEMPOTENCY_MARKER: Final[re.Pattern[str]] = re.compile(r"<!-- dark-factory:idempotency:[^>]* -->")
_STATUS_COMMENT: Final[re.Pattern[str]] = re.compile(r"^factory status: (.+)$")
_APPROVAL_COMMENT: Final[re.Pattern[str]] = re.compile(r"^factory approval requested: (.+)$")


def visible_comment(body: str, /) -> str:
    """Comment text as the provider renders it: idempotency markers are HTML comments."""
    return _IDEMPOTENCY_MARKER.sub("", body).strip()


class PlaneApiEmulator:
    """Stateful subset of the Plane REST API the tracker adapter talks to."""

    def __init__(self, *, token: str = PLANE_API_TOKEN) -> None:
        self._token = token
        self._issues: dict[str, dict[str, Any]] = {}
        self._handles: dict[str, str] = {}
        self._comments: dict[str, list[dict[str, Any]]] = {}
        self._requests: list[tuple[str, str]] = []
        self._issues_prefix = f"/api/v1/workspaces/{WORKSPACE_SLUG}/projects/{PROJECT_ID}/issues/"

    # --- test-side seeds and views -----------------------------------------

    def seed_issue(
        self,
        issue_id: str,
        /,
        *,
        key: str,
        name: str,
        description: str | None = None,
        labels: Iterable[str] = (),
        created_at: str = ISSUE_CREATED_AT,
    ) -> None:
        """Create an issue addressable by its id and by its project key."""
        self._issues[issue_id] = {
            "id": issue_id,
            "name": name,
            "description_stripped": description,
            "labels": [
                {"id": f"label-{index}", "name": label}
                for index, label in enumerate(labels, start=1)
            ],
            "created_at": created_at,
            "project": PROJECT_ID,
            "sequence_id": len(self._issues) + 1,
            "project_identifier": key,
        }
        self._handles[issue_id] = issue_id
        self._handles[key] = issue_id
        self._comments.setdefault(issue_id, [])

    def seed_change(self, external_ref: str, change: Change, /) -> None:
        """Seed the issue a ``Change`` maps to (used as the binding's seed hook)."""
        self.seed_issue(
            change.id,
            key=external_ref,
            name=change.title,
            description=change.description,
            labels=(f"risk:{change.risk_class.value}",),
            created_at=change.created_at.isoformat(),
        )

    def comments_of(self, handle: str, /) -> tuple[str, ...]:
        """Raw comment bodies of an issue as stored (markers included); unknown handle → empty."""
        issue_id = self._handles.get(handle)
        if issue_id is None:
            return ()
        return tuple(
            _string_of(comment.get("comment_stripped")) or ""
            for comment in self._comments[issue_id]
        )

    def visible_comments_of(self, handle: str, /) -> tuple[str, ...]:
        """Comment bodies as the tracker UI shows them (markers stripped)."""
        return tuple(visible_comment(body) for body in self.comments_of(handle))

    def statuses_of(self, handle: str, /) -> tuple[str, ...]:
        """Published statuses of an issue in order; a replayed write adds nothing."""
        statuses: list[str] = []
        for body in self.visible_comments_of(handle):
            match = _STATUS_COMMENT.match(body)
            if match is not None:
                statuses.append(match.group(1))
        return tuple(statuses)

    def approvals_of(self, handle: str, /) -> tuple[Gate, ...]:
        """Approval gates requested on an issue in order; a replayed write adds nothing."""
        gates: list[Gate] = []
        for body in self.visible_comments_of(handle):
            match = _APPROVAL_COMMENT.match(body)
            if match is not None:
                gates.append(Gate(match.group(1)))
        return tuple(gates)

    def issue_of(self, handle: str, /) -> dict[str, Any] | None:
        """Stored issue payload, for asserting what the adapter read."""
        issue_id = self._handles.get(handle)
        return None if issue_id is None else self._issues[issue_id]

    def request_paths(self) -> tuple[str, ...]:
        """Paths of every request the emulator saw, percent-encoded exactly as sent."""
        return tuple(path for _, path in self._requests)

    def transport(self) -> httpx2.MockTransport:
        """Transport serving this emulator to an ``httpx2`` client."""
        return httpx2.MockTransport(self._handle)

    # --- request handling --------------------------------------------------

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        # The journal keeps the wire form: ``url.path`` decodes percent-escapes,
        # while the encoding the adapter applied is what tests assert on.
        self._requests.append((request.method, request.url.raw_path.decode()))
        if request.headers.get("X-API-Key") != self._token:
            return _json_response(401, {"detail": "Authentication credentials were not provided."})
        if not path.startswith(self._issues_prefix):
            return _json_response(404, {"detail": "Not found."})
        route = path.removeprefix(self._issues_prefix)
        if route.endswith("/comments/"):
            return self._comments_route(request, route.removesuffix("/comments/"))
        return self._issue_route(request, route.removesuffix("/"))

    def _issue_route(self, request: httpx2.Request, handle: str) -> httpx2.Response:
        issue_id = self._handles.get(handle)
        if issue_id is None:
            return _json_response(404, {"detail": "Issue not found."})
        if request.method == "GET":
            return _json_response(200, self._issues[issue_id])
        return _json_response(405, {"detail": "Method not allowed."})

    def _comments_route(self, request: httpx2.Request, handle: str) -> httpx2.Response:
        issue_id = self._handles.get(handle)
        if issue_id is None:
            return _json_response(404, {"detail": "Issue not found."})
        if request.method == "GET":
            return _json_response(200, list(self._comments[issue_id]))
        html = _string_of(_body(request).get("comment_html"))
        if html is None:
            return _json_response(400, {"detail": "comment_html is required."})
        comment = {
            "id": len(self._comments[issue_id]) + 1,
            "comment_html": html,
            "comment_stripped": html,
            "actor": "automation",
        }
        self._comments[issue_id].append(comment)
        return _json_response(201, comment)


def _string_of(value: object) -> str | None:
    """A non-blank string field of a JSON payload, or ``None``."""
    if isinstance(value, str):
        return value.strip() or None
    return None


def _body(request: httpx2.Request) -> dict[str, Any]:
    return dict(json.loads(request.content or b"{}"))


def _json_response(status_code: int, payload: Any) -> httpx2.Response:
    return httpx2.Response(status_code=status_code, json=payload)
