"""In-memory fake of the tracker port (Plane adapter arrives with T-033)."""

from dark_factory.ports import Change, Gate, TrackerPort


class FakeTracker(TrackerPort):
    """In-memory ``TrackerPort``.

    ``get_change`` reads a table seeded with ``seed``; unknown external
    references resolve to ``None`` — tracker unavailability never blocks the
    caller (FR-020). ``publish_status`` and ``request_approval`` are idempotent
    by ``idempotency_key``: a replay appends nothing.
    """

    def __init__(self) -> None:
        self._changes: dict[str, Change] = {}
        self._statuses: dict[str, list[str]] = {}
        self._approvals: dict[str, list[Gate]] = {}
        self._status_keys: set[str] = set()
        self._approval_keys: set[str] = set()

    def seed(self, external_ref: str, change: Change) -> None:
        """Register a change for ``get_change`` lookups (simulation hook, not part of the port)."""
        self._changes[external_ref] = change

    async def get_change(self, external_ref: str, /) -> Change | None:
        return self._changes.get(external_ref)

    async def publish_status(self, change_id: str, status: str, *, idempotency_key: str) -> None:
        if idempotency_key in self._status_keys:
            return
        self._status_keys.add(idempotency_key)
        self._statuses.setdefault(change_id, []).append(status)

    async def request_approval(self, change_id: str, gate: Gate, *, idempotency_key: str) -> None:
        if idempotency_key in self._approval_keys:
            return
        self._approval_keys.add(idempotency_key)
        self._approvals.setdefault(change_id, []).append(gate)

    def statuses_of(self, change_id: str) -> tuple[str, ...]:
        """Read view of recorded statuses (not part of the port)."""
        return tuple(self._statuses.get(change_id, ()))

    def approvals_of(self, change_id: str) -> tuple[Gate, ...]:
        """Read view of recorded approval requests (not part of the port)."""
        return tuple(self._approvals.get(change_id, ()))
