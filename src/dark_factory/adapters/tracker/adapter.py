"""``PlaneTrackerAdapter``: a self-hosted Plane behind ``TrackerPort`` (T-033).

Plane is the pilot tracker (ADR-013): tasks are read from it, factory statuses
and approval requests are published back to the issue as comments. The tracker
is a *source of intake*, never a source of truth for operational state — the
factory re-reads state before acting (ADR-013 p.2, ADR-016 p.8), and a webhook
only accelerates that.

Authoritative mappings, all documented because they are the adapter's public
conventions:

- ``external_ref`` is a Plane issue handle: the issue id or its project key
  (``PLANE-42``). ``get_change`` reads the issue detail endpoint; a 404 means
  "no such task" and returns ``None``, while a transport failure or an
  unexpected status raises ``PlaneAPIError`` — an unavailable tracker stays
  visible to the caller, which then degrades deliberately (``NoOpTracker``,
  FR-020).
- ``publish_status`` / ``request_approval`` address the issue by ``change_id``,
  the same handle: for tracker-sourced changes ``get_change`` returns the Plane
  issue id as ``Change.id``, so the change id is the tracker-side handle.
- Both writes are idempotent by ``idempotency_key`` (FR-017): the key travels in
  an invisible HTML-comment marker inside the comment body, and an existing
  comment carrying that marker suppresses a replay. The marker is stripped by
  the tracker UI, exactly like the source-control adapter's ones (ADR-019 p.4).
- Risk class comes from a ``risk:R2`` issue label; without a matching label it
  defaults to ``R1`` (the intake default used elsewhere in the factory) — the
  tracker may state a class, while lowering it stays a policy decision
  (ADR-011 p.5).
"""

from datetime import UTC, datetime
from typing import Final
from urllib.parse import quote

import httpx2

from dark_factory.adapters.tracker.client import PlaneClient
from dark_factory.adapters.tracker.config import PlaneConfig
from dark_factory.ports import Change, ChangeSource, Gate, RiskClass, TrackerPort

IDEMPOTENCY_MARKER_PREFIX: Final[str] = "dark-factory:idempotency:"
"""Marker namespace shared with the source-control adapter (ADR-019 p.4)."""

STATUS_COMMENT_TEMPLATE: Final[str] = "factory status: {status}"
"""Status comment vocabulary; the contract suite parses it back."""

APPROVAL_COMMENT_TEMPLATE: Final[str] = "factory approval requested: {gate}"
"""Approval comment vocabulary; the contract suite parses it back."""

DEFAULT_RISK_CLASS: Final[RiskClass] = RiskClass.R1
"""Risk class of a task whose issue carries no ``risk:`` label."""

RISK_LABEL_PREFIX: Final[str] = "risk:"
"""Label prefix carrying the risk class, e.g. ``risk:R2``."""


def idempotency_marker(idempotency_key: str) -> str:
    """Invisible HTML-comment marker that makes a tracker write replay-safe."""
    return f"<!-- {IDEMPOTENCY_MARKER_PREFIX}{idempotency_key} -->"


class PlaneTrackerAdapter(TrackerPort):
    """Plane REST adapter implementing ``TrackerPort`` (ADR-013)."""

    def __init__(
        self,
        config: PlaneConfig,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        """Bind the adapter to ``config``; ``transport`` replaces the HTTP layer in tests."""
        self._config = config
        self._client = PlaneClient(config.base_url, api_key=config.api_key, transport=transport)

    async def get_change(self, external_ref: str, /) -> Change | None:
        """The change a Plane issue describes; ``None`` when the issue does not exist."""
        response = await self._client.request("GET", self._issue_path(external_ref))
        if response.status_code == 404:
            return None
        return self._to_change(external_ref, self._client.expect(response, 200).json())

    async def publish_status(self, change_id: str, status: str, *, idempotency_key: str) -> None:
        """Publish the factory status of a change as a comment on its issue (idempotent)."""
        await self._write_comment(
            change_id, STATUS_COMMENT_TEMPLATE.format(status=status), idempotency_key
        )

    async def request_approval(self, change_id: str, gate: Gate, *, idempotency_key: str) -> None:
        """Ask for a human decision on ``gate`` by commenting on the issue (idempotent)."""
        await self._write_comment(
            change_id, APPROVAL_COMMENT_TEMPLATE.format(gate=gate.value), idempotency_key
        )

    async def aclose(self) -> None:
        """Release the underlying HTTP connection pool."""
        await self._client.aclose()

    async def _write_comment(self, change_id: str, text: str, idempotency_key: str) -> None:
        """Post ``text`` once per ``idempotency_key``, skipping an issue comment that has it."""
        path = f"{self._issue_path(change_id)}comments/"
        marker = idempotency_marker(idempotency_key)
        existing = await self._client.get_json(path)
        if any(marker in _comment_body(comment) for comment in _as_list(existing)):
            return
        body = f"{text} {marker}"
        response = await self._client.request("POST", path, json={"comment_html": body})
        self._client.expect(response, 201)

    def _issue_path(self, handle: str) -> str:
        """Issue detail path of one Plane issue, with every segment percent-encoded."""
        workspace = quote(self._config.workspace_slug, safe="")
        project = quote(self._config.project_id, safe="")
        return f"/api/v1/workspaces/{workspace}/projects/{project}/issues/{quote(handle, safe='')}/"

    def _to_change(self, external_ref: str, payload: object) -> Change:
        """Domain ``Change`` of one Plane issue payload."""
        issue = payload if isinstance(payload, dict) else {}
        created_at = _parse_timestamp(issue.get("created_at"))
        extra: dict[str, object] = {}
        if created_at is not None:
            extra["created_at"] = created_at
        return Change(
            id=_text(issue.get("id")) or external_ref,
            title=_text(issue.get("name")) or external_ref,
            description=_text(issue.get("description_stripped")),
            source=ChangeSource.TRACKER,
            external_ref=external_ref,
            product=self._config.repository,
            risk_class=_risk_class(issue.get("labels")),
            **extra,
        )


def _as_list(payload: object) -> list[object]:
    """A JSON array payload as a Python list; anything else is treated as empty."""
    return list(payload) if isinstance(payload, list) else []


def _comment_body(comment: object) -> str:
    """Comment text as the provider stores it (Plane mirrors html into ``comment_stripped``)."""
    if not isinstance(comment, dict):
        return ""
    for key in ("comment_stripped", "comment_html"):
        value = comment.get(key)
        if isinstance(value, str):
            return value
    return ""


def _text(value: object) -> str | None:
    """A non-blank string field of a JSON payload, or ``None``."""
    if isinstance(value, str):
        return value.strip() or None
    return None


def _risk_class(labels: object) -> RiskClass:
    """Risk class of a ``risk:R2`` label; ``R1`` when no label states a known class."""
    for name in _label_names(labels):
        if not name.lower().startswith(RISK_LABEL_PREFIX):
            continue
        candidate = name[len(RISK_LABEL_PREFIX) :].strip().upper()
        try:
            return RiskClass(candidate)
        except ValueError:
            continue
    return DEFAULT_RISK_CLASS


def _label_names(labels: object) -> tuple[str, ...]:
    """Label names of a Plane issue payload (labels arrive expanded or as bare strings)."""
    if not isinstance(labels, list):
        return ()
    names: list[str] = []
    for label in labels:
        if isinstance(label, str):
            names.append(label)
        elif isinstance(label, dict):
            name = label.get("name")
            if isinstance(name, str):
                names.append(name)
    return tuple(names)


def _parse_timestamp(value: object) -> datetime | None:
    """Plane ISO-8601 timestamp (``…Z``) as an aware datetime; ``None`` when absent or broken."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
