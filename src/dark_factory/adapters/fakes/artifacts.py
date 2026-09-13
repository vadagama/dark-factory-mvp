"""In-memory fake of the artifact store port (S3/MinIO adapter is a DC concern)."""

from hashlib import sha256

from dark_factory.ports import ArtifactRef, ArtifactSpec, ArtifactStorePort


class FakeArtifactStore(ArtifactStorePort):
    """In-memory content-addressed ``ArtifactStorePort``.

    ``put`` takes no ``idempotency_key``: deduplication is by content — the same
    spec always yields the same ``ArtifactRef`` and never a second copy
    (FR-017 via content addressing).
    """

    def __init__(self) -> None:
        self._blobs: dict[str, bytes] = {}

    async def put(self, spec: ArtifactSpec, /) -> ArtifactRef:
        digest = sha256(spec.content).hexdigest()
        self._blobs[digest] = spec.content
        return ArtifactRef(
            artifact_type=spec.artifact_type,
            uri=f"mem://{spec.name}/{digest}",
            sha256=digest,
            producer=spec.producer,
        )

    async def get(self, ref: ArtifactRef, /) -> bytes:
        if ref.sha256 is None:
            raise ValueError(f"artifact {ref.uri!r} carries no sha256; cannot resolve content")
        try:
            return self._blobs[ref.sha256]
        except KeyError:
            raise KeyError(f"artifact {ref.sha256} is not stored") from None

    async def exists(self, ref: ArtifactRef, /) -> bool:
        return ref.sha256 is not None and ref.sha256 in self._blobs
