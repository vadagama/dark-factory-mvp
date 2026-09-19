"""Contract tests for RepositoryPort."""

import asyncio
from collections.abc import Callable

import pytest

from dark_factory.ports import RepositoryPort, RepositoryRef

REVISION = "abc1234"
CHANGES = {"docs/note.md": b"produced\n"}
MORE_CHANGES = {"docs/note.md": b"produced\n", "src/app.py": b"print('hi')\n"}


def test_adapter_satisfies_protocol(
    repository_port: RepositoryPort, repository: RepositoryRef
) -> None:
    assert isinstance(repository_port, RepositoryPort)
    assert not isinstance(object(), RepositoryPort)


def test_get_revision_of_a_missing_ref_is_a_keyerror(
    repository_port: RepositoryPort, repository: RepositoryRef
) -> None:
    # The real GitHub answers 422 for a ref that does not resolve on
    # /commits/{ref} and 404 for an unknown repository; both are "absent" for
    # the port contract (ScmRevision falls back to the base ref on it).
    with pytest.raises(KeyError):
        asyncio.run(repository_port.get_revision(repository, "factory/missing-branch"))


def test_ensure_branch_creates_branch_at_from_revision(
    repository_port: RepositoryPort, repository: RepositoryRef
) -> None:
    head = asyncio.run(
        repository_port.ensure_branch(
            repository, "feat/x", from_revision=REVISION, idempotency_key="k1"
        )
    )
    assert head == REVISION
    assert asyncio.run(repository_port.get_revision(repository, "feat/x")) == REVISION


def test_ensure_branch_replays_same_key_to_same_revision(
    repository_port: RepositoryPort, repository: RepositoryRef
) -> None:
    first = asyncio.run(
        repository_port.ensure_branch(
            repository, "feat/x", from_revision=REVISION, idempotency_key="k1"
        )
    )
    second = asyncio.run(
        repository_port.ensure_branch(
            repository, "feat/x", from_revision=REVISION, idempotency_key="k1"
        )
    )
    assert first == second == REVISION


def test_ensure_branch_new_key_on_existing_branch_creates_no_duplicate(
    repository_port: RepositoryPort, repository: RepositoryRef
) -> None:
    first = asyncio.run(
        repository_port.ensure_branch(
            repository, "feat/x", from_revision=REVISION, idempotency_key="k1"
        )
    )
    second = asyncio.run(
        repository_port.ensure_branch(
            repository, "feat/x", from_revision="def5678", idempotency_key="k2"
        )
    )
    assert second == first
    assert asyncio.run(repository_port.get_revision(repository, "feat/x")) == REVISION


# --- publish_commit (TD-024) ----------------------------------------------


def _prepared_branch(repository_port: RepositoryPort, repository: RepositoryRef) -> None:
    asyncio.run(
        repository_port.ensure_branch(
            repository, "feat/x", from_revision=REVISION, idempotency_key="branch-1"
        )
    )


def test_publish_commit_moves_the_branch_head(
    repository_port: RepositoryPort,
    repository: RepositoryRef,
    commit_journal: Callable[[str], tuple[str, ...]],
) -> None:
    _prepared_branch(repository_port, repository)

    sha = asyncio.run(
        repository_port.publish_commit(
            repository, "feat/x", CHANGES, message="factory: t", idempotency_key="c1"
        )
    )

    assert asyncio.run(repository_port.get_revision(repository, "feat/x")) == sha
    assert commit_journal("feat/x") == (sha,)


def test_publish_commit_carries_the_file_set(
    repository_port: RepositoryPort,
    repository: RepositoryRef,
    commit_files: Callable[[str], dict[str, bytes]],
) -> None:
    _prepared_branch(repository_port, repository)

    sha = asyncio.run(
        repository_port.publish_commit(
            repository, "feat/x", CHANGES, message="factory: t", idempotency_key="c1"
        )
    )

    assert commit_files(sha) == CHANGES


def test_publish_commit_replays_same_key_to_same_sha(
    repository_port: RepositoryPort,
    repository: RepositoryRef,
    commit_journal: Callable[[str], tuple[str, ...]],
) -> None:
    _prepared_branch(repository_port, repository)

    first = asyncio.run(
        repository_port.publish_commit(
            repository, "feat/x", CHANGES, message="factory: t", idempotency_key="c1"
        )
    )
    second = asyncio.run(
        repository_port.publish_commit(
            repository, "feat/x", CHANGES, message="factory: t", idempotency_key="c1"
        )
    )

    assert first == second
    assert commit_journal("feat/x") == (first,)  # one commit, not two (FR-017)


def test_publish_commit_new_key_creates_a_second_commit_on_top(
    repository_port: RepositoryPort,
    repository: RepositoryRef,
    commit_journal: Callable[[str], tuple[str, ...]],
    commit_files: Callable[[str], dict[str, bytes]],
) -> None:
    _prepared_branch(repository_port, repository)
    first = asyncio.run(
        repository_port.publish_commit(
            repository, "feat/x", CHANGES, message="factory: t", idempotency_key="c1"
        )
    )

    second = asyncio.run(
        repository_port.publish_commit(
            repository,
            "feat/x",
            {"src/app.py": b"print('hi')\n"},
            message="factory: t",
            idempotency_key="c2",
        )
    )

    assert second != first
    assert asyncio.run(repository_port.get_revision(repository, "feat/x")) == second
    assert commit_journal("feat/x") == (first, second)
    assert commit_files(second) == MORE_CHANGES  # the second commit keeps the first's files


def test_publish_commit_rejects_an_empty_change_set(
    repository_port: RepositoryPort,
    repository: RepositoryRef,
    commit_journal: Callable[[str], tuple[str, ...]],
) -> None:
    _prepared_branch(repository_port, repository)

    with pytest.raises(ValueError):
        asyncio.run(
            repository_port.publish_commit(
                repository, "feat/x", {}, message="m", idempotency_key="c1"
            )
        )
    assert commit_journal("feat/x") == ()


def test_publish_commit_requires_an_existing_branch(
    repository_port: RepositoryPort, repository: RepositoryRef
) -> None:
    with pytest.raises(KeyError):
        asyncio.run(
            repository_port.publish_commit(
                repository, "feat/missing", CHANGES, message="m", idempotency_key="c1"
            )
        )


# --- read model of the document artifacts (T082, ADR-035) -----------------------------


def _publish(repository_port: RepositoryPort, repository: RepositoryRef) -> tuple[str, str]:
    """Two commits on ``feat/docs``: the second changes one file and adds another."""
    asyncio.run(
        repository_port.ensure_branch(
            repository, "feat/docs", from_revision=REVISION, idempotency_key="rb-1"
        )
    )
    first = asyncio.run(
        repository_port.publish_commit(
            repository,
            "feat/docs",
            {".factory/changes/2026/CHG-1/spec/requirements/REQ-001.md": b"# v1\n"},
            message="spec v1",
            idempotency_key="rc-1",
        )
    )
    second = asyncio.run(
        repository_port.publish_commit(
            repository,
            "feat/docs",
            {
                ".factory/changes/2026/CHG-1/spec/requirements/REQ-001.md": b"# v2\n",
                ".factory/changes/2026/CHG-1/intent.md": b"intent\n",
            },
            message="spec v2",
            idempotency_key="rc-2",
        )
    )
    return first, second


def test_read_file_returns_the_bytes_at_a_branch_or_a_sha(
    repository_port: RepositoryPort, repository: RepositoryRef
) -> None:
    first, _second = _publish(repository_port, repository)
    path = ".factory/changes/2026/CHG-1/spec/requirements/REQ-001.md"
    assert asyncio.run(repository_port.read_file(repository, "feat/docs", path)) == b"# v2\n"
    assert asyncio.run(repository_port.read_file(repository, first, path)) == b"# v1\n"


def test_read_file_of_a_missing_path_or_ref_is_a_keyerror(
    repository_port: RepositoryPort, repository: RepositoryRef
) -> None:
    _publish(repository_port, repository)
    with pytest.raises(KeyError):
        asyncio.run(repository_port.read_file(repository, "feat/docs", "nope.md"))
    with pytest.raises(KeyError):
        asyncio.run(repository_port.read_file(repository, "feat/missing", "intent.md"))


def test_list_tree_filters_by_prefix_and_sorts(
    repository_port: RepositoryPort, repository: RepositoryRef
) -> None:
    _publish(repository_port, repository)
    paths = asyncio.run(
        repository_port.list_tree(repository, "feat/docs", prefix=".factory/changes/")
    )
    assert list(paths) == [
        ".factory/changes/2026/CHG-1/intent.md",
        ".factory/changes/2026/CHG-1/spec/requirements/REQ-001.md",
    ]
    assert (
        list(asyncio.run(repository_port.list_tree(repository, "feat/docs", prefix="src/"))) == []
    )
    with pytest.raises(KeyError):
        asyncio.run(repository_port.list_tree(repository, "feat/missing"))


def test_list_commits_newest_first_and_filtered_by_path(
    repository_port: RepositoryPort, repository: RepositoryRef
) -> None:
    first, second = _publish(repository_port, repository)
    everything = asyncio.run(repository_port.list_commits(repository, "feat/docs"))
    assert [commit.sha for commit in everything][:2] == [second, first]
    assert everything[0].message.startswith("spec v2")
    only_intent = asyncio.run(
        repository_port.list_commits(
            repository, "feat/docs", path=".factory/changes/2026/CHG-1/intent.md"
        )
    )
    assert [commit.sha for commit in only_intent] == [second]
    untouched = asyncio.run(repository_port.list_commits(repository, "feat/docs", path="none.md"))
    assert list(untouched) == []
    with pytest.raises(KeyError):
        asyncio.run(repository_port.list_commits(repository, "feat/missing"))
