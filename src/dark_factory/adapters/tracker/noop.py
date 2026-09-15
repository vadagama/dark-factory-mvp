"""``NoOpTracker``: the documented stand-in when no tracker is wired (T-033).

ADR-013 p.5 keeps a no-op ``TrackerPort`` available so the pipeline runs before
a Plane instance exists for the deployment, and FR-020 requires that a missing
tracker never blocks CLI/Console. Every method therefore succeeds and reports
"nothing known": intake keeps working with CLI/Console-sourced changes, and
outbound status publishing is simply dropped.
"""

from dark_factory.ports import Change, Gate, TrackerPort


class NoOpTracker(TrackerPort):
    """Tracker that knows nothing and never fails (ADR-013 p.5, FR-020)."""

    async def get_change(self, external_ref: str, /) -> Change | None:
        """No tracker, no task: every external reference resolves to ``None``."""
        return None

    async def publish_status(self, change_id: str, status: str, *, idempotency_key: str) -> None:
        """Publish nothing; a status without a tracker is not an error."""
        return None

    async def request_approval(self, change_id: str, gate: Gate, *, idempotency_key: str) -> None:
        """Request nothing; approvals live in the factory, not in the tracker (T-035)."""
        return None
