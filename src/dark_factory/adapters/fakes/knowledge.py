"""In-memory fake of the knowledge port (real source providers arrive later)."""

from datetime import UTC, datetime
from hashlib import sha256

from dark_factory.ports import (
    ContextBundle,
    ContextRequest,
    ContextSource,
    KnowledgePort,
    SourceKind,
    build_bundle,
)


class FakeKnowledge(KnowledgePort):
    """In-memory ``KnowledgePort`` assembling a reproducible bundle from seeded sources.

    ``seed`` fills the store (``str`` content is hashed as UTF-8; seeding the
    same ``(kind, location)`` twice overwrites — the fake holds one snapshot of
    each source). ``collect`` hashes content with sha256 and stamps a fixed
    ``retrieved_at``, so equal seeds always build equal bundles. Collecting
    with nothing seeded yields the deterministic empty bundle (``sources == ()``,
    hash of the empty set) — an empty context is a valid reproducible result,
    not an error.
    """

    #: Fixed retrieval timestamp so seeded bundles stay byte-for-byte reproducible.
    RETRIEVED_AT = datetime(2026, 1, 1, tzinfo=UTC)

    def __init__(self) -> None:
        self._content: dict[tuple[SourceKind, str], tuple[str | None, bytes]] = {}

    def seed(
        self,
        kind: SourceKind,
        location: str,
        revision: str | None,
        content: str | bytes,
    ) -> None:
        """Register a source for ``collect`` (simulation hook, not part of the port)."""
        payload = content.encode("utf-8") if isinstance(content, str) else content
        self._content[(kind, location)] = (revision, payload)

    async def collect(self, request: ContextRequest, /) -> ContextBundle:
        sources = [
            ContextSource(
                kind=kind,
                location=location,
                revision=revision,
                content_hash=sha256(payload).hexdigest(),
                retrieved_at=self.RETRIEVED_AT,
            )
            for (kind, location), (revision, payload) in sorted(self._content.items())
        ]
        return build_bundle(change_id=request.change_id, run_id=request.run_id, sources=sources)
