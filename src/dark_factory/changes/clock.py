"""The one wall clock of the core: timezone-aware UTC ``now``."""

from datetime import UTC, datetime

__all__ = ["utc_now"]


def utc_now() -> datetime:
    """Current time as an aware UTC ``datetime``."""
    return datetime.now(UTC)
