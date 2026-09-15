"""Secret and size screening of run records before they enter Git (T-061).

ADR-015 p.4 requires the record protocol to sanitize secrets and personal data
before a record is committed, and to cap the record size (heavy evidence stays
in CI artifacts). Screening is deterministic and fail-closed: a matching value
blocks publication, and the diagnostic names only the JSON path and the pattern
kind — never the value itself (ADR-009).

The patterns are shape-based heuristics, not a substitute for the secret
hygiene of the callers: they catch the classic leaks (keys, tokens, JWT,
credentials embedded in a URI, `password=...` assignments) that a record would
otherwise persist into Git forever.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from dark_factory.execution.runs.errors import RunRecordTooLargeError

MAX_RUN_RECORD_BYTES: Final[int] = 512 * 1024
"""Maximum total size of one published record.

Larger payloads are not Git material: they belong in CI artifacts or object
storage behind ``ArtifactStorePort`` (ADR-009, ADR-015 p.4).
"""


@dataclass(frozen=True, slots=True)
class UnsafeValue:
    """One screened value: where it sits in the payload and which pattern matched."""

    path: str
    kind: str


_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    (
        "secret_assignment",
        re.compile(
            r"(?i)\b(?:api[_-]?key|access[_-]?token|token|password|passwd|secret)\b"
            r"[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9._~+/=-]{12,}"
        ),
    ),
    ("uri_userinfo", re.compile(r"://[^/\s:@]+:[^/\s:@]+@")),
)


def check_payload_size(total_bytes: int, /) -> None:
    """Reject a record whose serialized files exceed the size budget (ADR-015 p.4)."""
    if total_bytes > MAX_RUN_RECORD_BYTES:
        raise RunRecordTooLargeError(
            f"the run record is {total_bytes} bytes, above the {MAX_RUN_RECORD_BYTES}-byte limit"
        )


def find_unsafe_values(payload: object, /) -> tuple[UnsafeValue, ...]:
    """Every value of a JSON-mode payload matching a secret pattern, as data.

    The payload is walked structurally rather than screened as one text blob, so
    the caller gets a precise JSON path instead of an offset in a merged string.
    """
    found: list[UnsafeValue] = []
    _walk(payload, "", found)
    return tuple(found)


def describe_unsafe_values(values: tuple[UnsafeValue, ...], /) -> str:
    """Short value-free diagnostic listing the offending paths and kinds (ADR-009)."""
    return ", ".join(f"{value.path or '<root>'} [{value.kind}]" for value in values)


def _walk(node: object, path: str, found: list[UnsafeValue]) -> None:
    """Depth-first walk over the JSON-mode structure, recording unsafe strings."""
    if isinstance(node, str):
        kind = _match_kind(node)
        if kind is not None:
            found.append(UnsafeValue(path=path, kind=kind))
    elif isinstance(node, Mapping):
        for key, value in node.items():
            _walk(value, f"{path}.{key}" if path else str(key), found)
    elif isinstance(node, (list, tuple)):
        for index, value in enumerate(node):
            _walk(value, f"{path}[{index}]", found)


def _match_kind(value: str) -> str | None:
    """Kind of the first matching pattern, or ``None`` when the value is safe."""
    for kind, pattern in _PATTERNS:
        if pattern.search(value):
            return kind
    return None
