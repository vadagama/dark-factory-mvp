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
