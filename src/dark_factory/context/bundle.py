"""ContextBundle: the versioned material set handed to agents (plan T-012, FR-001).

A bundle freezes which sources a run sees — repository materials, ``specs/``
(before T-020) or ``openspec/`` (after), the constitution, ADRs and the
engineering pack — each with provenance: location, pinned revision and content
hash. Assembly is reproducible (DoD T-012): ``bundle_hash`` is a sha256 over
the canonical serialization of the hash-relevant source identities, so equal
inputs always build an equal bundle regardless of insertion order;
``retrieved_at`` is volatile bookkeeping and never enters the hash.
"""

import json
from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

CONTEXT_SCHEMA_VERSION: Final = 1
"""Schema version of the context bundle contract; breaking changes bump it (ADR-015 p.3)."""

# Literal contract type of CONTEXT_SCHEMA_VERSION; keep the two in sync.
type ContextSchemaVersion = Literal[1]


class SourceKind(StrEnum):
    """Category of a context source (plan T-012).

    One ``SPEC`` kind covers ``specs/`` before T-020 and ``openspec/`` after:
    the ``location`` distinguishes the two layouts. ``EVIDENCE`` covers
    collected acceptance evidence (T-013); its ``location`` is the artifact
    path/URI and its ``content_hash`` is computed by the collector.
    """

    REPO = "repo"
    SPEC = "spec"
    CONSTITUTION = "constitution"
    ADR = "adr"
    ENGINEERING_PACK = "engineering_pack"
    EVIDENCE = "evidence"


class ContextSource(BaseModel):
    """One context source with provenance (plan T-012).

    ``revision`` pins the source (git sha, document version) and
    ``content_hash`` is the sha256 hex digest of the retrieved content.
    ``retrieved_at`` is volatile bookkeeping and is excluded from
    ``ContextBundle.bundle_hash``.
    """

    model_config = ConfigDict(frozen=True)

    kind: SourceKind
    location: str = Field(min_length=1)
    revision: str | None = None
    content_hash: str = Field(min_length=1)
    retrieved_at: datetime


def _identity(source: ContextSource) -> tuple[str, str, str, str]:
    """Hash-relevant identity of a source: exactly what ``bundle_hash`` covers."""
    return (source.kind.value, source.location, source.revision or "", source.content_hash)


def canonical_order(sources: Iterable[ContextSource]) -> tuple[ContextSource, ...]:
    """Sort sources into the canonical bundle order (by their hash-relevant identity)."""
    return tuple(sorted(sources, key=_identity))


def compute_bundle_hash(sources: Iterable[ContextSource]) -> str:
    """Reproducible sha256 over the canonical serialization of the source identities."""
    payload = json.dumps(sorted(_identity(source) for source in sources), separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


class ContextBundle(BaseModel):
    """Versioned, reproducible set of context sources for one run (plan T-012).

    Build through :func:`build_bundle`, which rejects duplicates and computes
    ``bundle_hash``. ``sources`` is normalized into the canonical order, so
    insertion order never affects equality or the hash.
    """

    model_config = ConfigDict(frozen=True)

    schema_version: ContextSchemaVersion = CONTEXT_SCHEMA_VERSION
    change_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    sources: tuple[ContextSource, ...] = ()
    bundle_hash: str = Field(min_length=1)

    @field_validator("sources", mode="after")
    @classmethod
    def _canonicalize_sources(cls, sources: tuple[ContextSource, ...]) -> tuple[ContextSource, ...]:
        return canonical_order(sources)


def build_bundle(*, change_id: str, run_id: str, sources: Iterable[ContextSource]) -> ContextBundle:
    """Assemble a reproducible ``ContextBundle`` from the given sources.

    An exact duplicate — a source whose hash-relevant identity
    ``(kind, location, revision, content_hash)`` is already present, even with
    a different ``retrieved_at`` — is a ``ValueError``, not a silent merge:
    a bundle is one snapshot of each source. The hash covers the canonical
    serialization of the sorted identities only; ``retrieved_at`` never enters
    it.
    """
    items = list(sources)
    seen: set[tuple[str, str, str, str]] = set()
    for identity in (_identity(source) for source in items):
        if identity in seen:
            raise ValueError(f"duplicate context source {identity!r}")
        seen.add(identity)
    return ContextBundle(
        change_id=change_id,
        run_id=run_id,
        sources=tuple(items),
        bundle_hash=compute_bundle_hash(items),
    )
