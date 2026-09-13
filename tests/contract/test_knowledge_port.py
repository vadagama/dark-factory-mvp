"""Contract tests for KnowledgePort."""

import asyncio
from hashlib import sha256

from dark_factory.ports import ContextBundle, ContextRequest, KnowledgePort, SourceKind


def _request() -> ContextRequest:
    return ContextRequest(change_id="chg-001", run_id="run-001")


def test_adapter_satisfies_protocol(knowledge_port: KnowledgePort) -> None:
    assert isinstance(knowledge_port, KnowledgePort)


def test_collect_is_deterministic(seeded_knowledge_port: KnowledgePort) -> None:
    first = asyncio.run(seeded_knowledge_port.collect(_request()))
    second = asyncio.run(seeded_knowledge_port.collect(_request()))
    assert second == first


def test_collect_returns_versioned_bundle(seeded_knowledge_port: KnowledgePort) -> None:
    bundle = asyncio.run(seeded_knowledge_port.collect(_request()))
    assert isinstance(bundle, ContextBundle)
    assert bundle.schema_version == 1
    assert bundle.change_id == "chg-001"
    assert bundle.run_id == "run-001"


def test_collect_records_provenance(seeded_knowledge_port: KnowledgePort) -> None:
    bundle = asyncio.run(seeded_knowledge_port.collect(_request()))
    repo = next(source for source in bundle.sources if source.kind is SourceKind.REPO)
    assert repo.location == "src/dark_factory"
    assert repo.revision == "abc123"
    assert repo.content_hash == sha256(b"factory source").hexdigest()


def test_collect_without_sources_is_an_empty_bundle(knowledge_port: KnowledgePort) -> None:
    bundle = asyncio.run(knowledge_port.collect(_request()))
    assert bundle.sources == ()
    assert bundle.bundle_hash != ""
