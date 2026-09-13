"""Unit tests of ContextBundle assembly: reproducibility and canonical order (T-012)."""

from datetime import UTC, datetime, timedelta

import pytest

from dark_factory.context.bundle import (
    CONTEXT_SCHEMA_VERSION,
    ContextBundle,
    ContextSource,
    SourceKind,
    build_bundle,
)

RETRIEVED_AT = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)


def _source(
    *,
    kind: SourceKind = SourceKind.REPO,
    location: str = "src/dark_factory",
    revision: str | None = "abc123",
    content_hash: str = "a" * 64,
    retrieved_at: datetime = RETRIEVED_AT,
) -> ContextSource:
    return ContextSource(
        kind=kind,
        location=location,
        revision=revision,
        content_hash=content_hash,
        retrieved_at=retrieved_at,
    )


def _adr_source() -> ContextSource:
    return _source(kind=SourceKind.ADR, location="docs/adr/ADR-001.md", revision=None)


def test_equal_inputs_build_equal_bundles() -> None:
    sources = [_source(), _adr_source()]
    first = build_bundle(change_id="chg-001", run_id="run-001", sources=sources)
    second = build_bundle(change_id="chg-001", run_id="run-001", sources=list(sources))
    assert second == first
    assert second.bundle_hash == first.bundle_hash
    assert first.bundle_hash != ""


def test_empty_bundle_is_deterministic() -> None:
    first = build_bundle(change_id="chg-001", run_id="run-001", sources=[])
    second = build_bundle(change_id="chg-001", run_id="run-001", sources=[])
    assert first.sources == ()
    assert second == first


def test_source_order_does_not_affect_hash() -> None:
    sources = [_source(), _adr_source()]
    first = build_bundle(change_id="chg-001", run_id="run-001", sources=sources)
    second = build_bundle(change_id="chg-001", run_id="run-001", sources=list(reversed(sources)))
    assert second.bundle_hash == first.bundle_hash
    assert second == first  # sources are normalized into the canonical order


def test_bundle_normalizes_sources_into_canonical_order() -> None:
    bundle = ContextBundle(
        change_id="chg-001", run_id="run-001", sources=(_source(), _adr_source()), bundle_hash="h"
    )
    assert bundle.sources == (_adr_source(), _source())  # "adr" sorts before "repo"


def test_bundle_carries_schema_version() -> None:
    bundle = build_bundle(change_id="chg-001", run_id="run-001", sources=[_source()])
    assert bundle.schema_version == CONTEXT_SCHEMA_VERSION


def test_different_content_changes_the_hash() -> None:
    base = build_bundle(change_id="chg-001", run_id="run-001", sources=[_source()])
    changed = build_bundle(
        change_id="chg-001", run_id="run-001", sources=[_source(content_hash="b" * 64)]
    )
    assert changed.bundle_hash != base.bundle_hash


def test_different_revision_changes_the_hash() -> None:
    base = build_bundle(change_id="chg-001", run_id="run-001", sources=[_source()])
    changed = build_bundle(
        change_id="chg-001", run_id="run-001", sources=[_source(revision="def456")]
    )
    assert changed.bundle_hash != base.bundle_hash


def test_retrieved_at_does_not_affect_the_hash() -> None:
    base = build_bundle(change_id="chg-001", run_id="run-001", sources=[_source()])
    later = build_bundle(
        change_id="chg-001",
        run_id="run-001",
        sources=[_source(retrieved_at=RETRIEVED_AT + timedelta(hours=1))],
    )
    assert later.bundle_hash == base.bundle_hash


def test_exact_duplicate_is_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate context source"):
        build_bundle(
            change_id="chg-001",
            run_id="run-001",
            sources=[
                _source(),
                # Same identity, different retrieved_at: still an exact duplicate,
                # because the identity ignores the volatile timestamp.
                _source(retrieved_at=RETRIEVED_AT + timedelta(hours=1)),
            ],
        )
