"""Contract tests for RepositoryPort."""

import asyncio

from dark_factory.ports import RepositoryPort, RepositoryRef

REVISION = "abc1234"


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
