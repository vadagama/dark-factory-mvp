"""Contract tests for ArtifactStorePort."""

import asyncio

from dark_factory.ports import ArtifactRef, ArtifactSpec, ArtifactStorePort


def _spec(content: bytes = b"report body", name: str = "report.md") -> ArtifactSpec:
    return ArtifactSpec(artifact_type="report", name=name, content=content, producer="quality")


def test_adapter_satisfies_protocol(artifact_store_port: ArtifactStorePort) -> None:
    assert isinstance(artifact_store_port, ArtifactStorePort)


def test_put_then_get_roundtrips_content(artifact_store_port: ArtifactStorePort) -> None:
    ref = asyncio.run(artifact_store_port.put(_spec()))
    assert isinstance(ref, ArtifactRef)
    assert asyncio.run(artifact_store_port.get(ref)) == b"report body"
    assert asyncio.run(artifact_store_port.exists(ref)) is True


def test_put_is_idempotent_by_content(artifact_store_port: ArtifactStorePort) -> None:
    first = asyncio.run(artifact_store_port.put(_spec()))
    second = asyncio.run(artifact_store_port.put(_spec()))
    assert second == first


def test_put_different_content_yields_different_ref(
    artifact_store_port: ArtifactStorePort,
) -> None:
    first = asyncio.run(artifact_store_port.put(_spec(b"v1")))
    second = asyncio.run(artifact_store_port.put(_spec(b"v2")))
    assert second != first
    assert asyncio.run(artifact_store_port.get(second)) == b"v2"
    assert asyncio.run(artifact_store_port.exists(first)) is True
