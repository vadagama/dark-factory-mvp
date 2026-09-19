"""Artifact service over the fake repository (T082-T084, ADR-035)."""

import asyncio

import pytest

from dark_factory.adapters.fakes import FakeRepository
from dark_factory.context.artifacts import ArtifactKind
from dark_factory.orchestration.artifacts import (
    ArtifactConflictError,
    ArtifactNotFoundError,
    ArtifactService,
    edit_effect_key,
)
from dark_factory.orchestration.stages.agent import branch_name
from tests.changes_factories import make_change

REQ = ".factory/changes/2026/CHG-0001-export/spec/requirements/REQ-001-export.md"
INTENT = ".factory/changes/2026/CHG-0001-export/intent.md"
V1 = (
    "---\nschema: s\nid: req:pilot:export\ntype: requirement\ntitle: Export\nproduct: pilot\n"
    "status: proposed\nchange: chg-001\n---\n\n# Export\n\n- AC-1: a\n- AC-2: b\n"
)
V2 = V1.replace("- AC-2: b\n", "- AC-2: b (rounded)\n")


def _seeded() -> tuple[FakeRepository, ArtifactService, str, str]:
    repository = FakeRepository()
    change = make_change()
    branch = branch_name(change.id)
    asyncio.run(
        repository.ensure_branch(change.product, "main", from_revision="base", idempotency_key="m")
    )
    asyncio.run(
        repository.ensure_branch(change.product, branch, from_revision="base", idempotency_key="b")
    )
    first = asyncio.run(
        repository.publish_commit(
            change.product,
            branch,
            {REQ: V1.encode(), INTENT: b"intent\n", "src/app.py": b"x"},
            message="spec",
            idempotency_key="c1",
        )
    )
    second = asyncio.run(
        repository.publish_commit(
            change.product, branch, {REQ: V2.encode()}, message="spec v2", idempotency_key="c2"
        )
    )
    return repository, ArtifactService(repository), first, second


def test_tree_lists_only_changeset_text_artifacts_with_kinds() -> None:
    _repository, service, _first, second = _seeded()
    tree = service.tree(make_change())
    assert tree.exists and tree.revision == second
    assert [(node.path, node.kind) for node in tree.nodes] == [
        (INTENT, ArtifactKind.SPEC),
        (REQ, ArtifactKind.SPEC),
    ]


def test_tree_without_a_branch_is_distinguishable_from_an_empty_branch() -> None:
    repository = FakeRepository()
    change = make_change()
    service = ArtifactService(repository)
    assert not service.tree(change).exists
    assert service.head(change) is None
    asyncio.run(
        repository.ensure_branch(
            change.product, branch_name(change.id), from_revision="base", idempotency_key="b"
        )
    )
    tree = service.tree(change)
    assert tree.exists and tree.nodes == ()


def test_get_parses_properties_anchors_and_reads_older_revisions() -> None:
    _repository, service, first, second = _seeded()
    change = make_change()
    document = service.get(change, REQ)
    assert document.revision == second
    assert document.kind is ArtifactKind.SPEC
    assert document.properties is not None and document.properties.values["title"] == "Export"
    assert "AC-2" in document.anchors and "req:pilot:export" in document.anchors
    assert document.body.strip().startswith("# Export")
    older = service.get(change, REQ, revision=first)
    assert older.content == V1
    with pytest.raises(ArtifactNotFoundError):
        service.get(change, "missing.md")
    with pytest.raises(ArtifactNotFoundError):
        ArtifactService(FakeRepository()).get(change, REQ)


def test_versions_and_diff_follow_the_commits_of_the_path() -> None:
    _repository, service, first, second = _seeded()
    change = make_change()
    assert [v.revision for v in service.versions(change, REQ)] == [second, first]
    assert [v.revision for v in service.versions(change, INTENT)] == [first]
    diff = service.diff(change, REQ, from_revision=first, to_revision=second)
    assert diff.added == 1 and diff.removed == 1
    assert "+- AC-2: b (rounded)" in diff.unified
    with pytest.raises(ArtifactNotFoundError):
        service.diff(change, "nope.md", from_revision=first, to_revision=second)


def test_write_lands_one_commit_and_is_idempotent_by_key() -> None:
    repository, service, _first, second = _seeded()
    change = make_change()
    result = service.write(
        change, REQ, V2 + "\n- AC-3: c\n", base_revision=second, actor="alice", idempotency_key="k1"
    )
    assert result.previous_revision == second and result.revision != second
    assert service.get(change, REQ).content.endswith("- AC-3: c\n")
    # A retried save (same content, same base, same key) lands nothing new.
    replay = service.write(
        change,
        REQ,
        V2 + "\n- AC-3: c\n",
        base_revision=second,
        actor="alice",
        idempotency_key="k1",
    )
    assert replay.revision == result.revision
    assert len(repository.commits_of(change.product, branch_name(change.id))) == 3


def test_write_refuses_a_conflicting_base_but_allows_an_unrelated_head_move() -> None:
    _repository, service, first, second = _seeded()
    change = make_change()
    # The operator edited REQ from `first`, but REQ changed in `second`: conflict.
    with pytest.raises(ArtifactConflictError) as excinfo:
        service.write(change, REQ, "edited", base_revision=first, actor="alice")
    assert excinfo.value.head_revision == second
    # INTENT did not change between `first` and `second`: the head move is unrelated.
    moved = service.write(change, INTENT, "intent v2\n", base_revision=first, actor="alice")
    assert moved.previous_revision == second


def test_write_of_unchanged_content_is_a_no_op_and_creates_the_branch_when_missing() -> None:
    _repository, service, _first, second = _seeded()
    change = make_change()
    same = service.write(change, REQ, V2, base_revision=second, actor="alice")
    assert same.revision == second and same.previous_revision == second
    fresh = FakeRepository()
    asyncio.run(
        fresh.ensure_branch(change.product, "main", from_revision="base", idempotency_key="m")
    )
    created = ArtifactService(fresh).write(
        change, INTENT, "new\n", base_revision=None, actor="alice"
    )
    assert created.previous_revision == "base"
    assert (
        asyncio.run(fresh.get_revision(change.product, branch_name(change.id))) == created.revision
    )


def test_edit_effect_key_is_stable_per_content_and_base() -> None:
    assert edit_effect_key("c", "p", "x", "r1") == edit_effect_key("c", "p", "x", "r1")
    assert edit_effect_key("c", "p", "x", "r1") != edit_effect_key("c", "p", "y", "r1")
