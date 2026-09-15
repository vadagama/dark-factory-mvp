"""Deterministic partitioning of the ``dark-factory-runs`` repository (T-061).

ADR-015 p.4 fixes the partitioning shape (``YYYY/MM/project/run-id``) and
requires idempotent addressability; git-structure §8 fixes the concrete tree
``runs/<year>/<month>/<change-id>/<run-id>/``. The helpers here derive that path
from a record and refuse anything that is not a single safe path element: the
partitioning must stay deterministic and must never escape the runs root
(``..``, absolute paths, empty or dot-only ids).
"""

import re
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Final

from dark_factory.changes.run_records import RunRecord
from dark_factory.execution.runs.errors import RunRecordStoreError

RUNS_DIR_NAME: Final[str] = "runs"
"""Subdirectory of the runs repository holding the records (git-structure §8)."""

_SLUG_UNSAFE: Final[re.Pattern[str]] = re.compile(r"[^A-Za-z0-9._-]")
_MAX_SLUG_LENGTH: Final[int] = 120
_UNSAFE_PARTS: Final[frozenset[str]] = frozenset({"", ".", ".."})


def slug(text: str, /) -> str:
    """Single safe path element of an identifier (deterministic, non-escaping).

    ``chg:product:2026:0001`` becomes ``chg-product-2026-0001``: characters that
    are not portable in a path are replaced, so the partition stays one level
    deep and reproducible. An identifier that leaves nothing usable is rejected
    — a record without a safe address cannot be published (ADR-015 p.4). The
    rejected value is never echoed, because the id may carry user text.
    """
    candidate = _SLUG_UNSAFE.sub("-", text).strip("-")
    if not candidate or set(candidate) <= {"."} or len(candidate) > _MAX_SLUG_LENGTH:
        raise RunRecordStoreError(
            "the change or run id cannot be used as a path element of the runs repository"
        )
    return candidate


def run_dir(runs_root: Path, record: RunRecord, /) -> Path:
    """Partition directory of a record: ``runs/<year>/<month>/<change>/<run>``.

    Year and month come from the run creation time converted to UTC — never from
    the clock of the publishing process — so publishing the same record twice
    always addresses the same directory (idempotency, git-structure §8).
    """
    moment = _utc(record.run.created_at)
    parts = (
        RUNS_DIR_NAME,
        f"{moment.year:04d}",
        f"{moment.month:02d}",
        slug(record.change.id),
        slug(record.run.id),
    )
    if any(part in _UNSAFE_PARTS for part in parts):
        raise RunRecordStoreError("refusing to build an unsafe path in the runs repository")
    return runs_root.joinpath(*parts)


def resolve_ref_path(runs_root: Path, relative_path: str, /) -> Path:
    """Path of a published record from a ref: relative, safe, inside the runs root.

    A relative path can come back as data (from a rendered report or an API
    payload), so it is re-validated instead of being trusted; the caller never
    gets to read outside the repository.
    """
    relative = PurePosixPath(relative_path)
    if not relative.parts or relative.is_absolute():
        raise RunRecordStoreError("refusing to resolve an unsafe run-record path")
    if any(part in _UNSAFE_PARTS for part in relative.parts):
        raise RunRecordStoreError("refusing to resolve an unsafe run-record path")
    return runs_root.joinpath(*relative.parts)


def _utc(moment: datetime) -> datetime:
    """``moment`` in UTC; a naive timestamp is read as UTC (records are written in UTC)."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)
