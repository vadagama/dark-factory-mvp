"""Contract tests for MergeRequestPort."""

import asyncio
from collections.abc import Callable

import pytest

from dark_factory.ports import (
    ChangeRequestRef,
    ChangeRequestStatus,
    HeadMismatchError,
    MergeRequestPort,
    OpenChangeRequest,
    Provider,
    RepositoryRef,
)

HEAD = "abc1234"


def _open_request(repository: RepositoryRef, change_id: str = "chg-001") -> OpenChangeRequest:
    return OpenChangeRequest(
        repository=repository,
        change_id=change_id,
        source_branch="feat/x",
        target_branch="main",
        title="Add feature",
        head_sha=HEAD,
    )


def test_adapter_satisfies_protocol(merge_request_port: MergeRequestPort) -> None:
    assert isinstance(merge_request_port, MergeRequestPort)


def test_open_returns_open_change_request(
    merge_request_port: MergeRequestPort, repository: RepositoryRef
) -> None:
    ref = asyncio.run(merge_request_port.open(_open_request(repository), idempotency_key="k1"))
    assert ref.repository == repository
    assert ref.number >= 1
    assert ref.status is ChangeRequestStatus.OPEN


def test_open_replays_same_key_to_same_change_request(
    merge_request_port: MergeRequestPort, repository: RepositoryRef
) -> None:
    first = asyncio.run(merge_request_port.open(_open_request(repository), idempotency_key="k1"))
    second = asyncio.run(merge_request_port.open(_open_request(repository), idempotency_key="k1"))
    assert second == first


def test_find_existing_searches_by_repository_and_change_id(
    merge_request_port: MergeRequestPort, repository: RepositoryRef
) -> None:
    opened = asyncio.run(merge_request_port.open(_open_request(repository), idempotency_key="k1"))
    assert asyncio.run(merge_request_port.find_existing(repository, "chg-001")) == opened
    other = RepositoryRef(provider=Provider.GITHUB, slug="other/repo")
    assert asyncio.run(merge_request_port.find_existing(other, "chg-001")) is None
    assert asyncio.run(merge_request_port.find_existing(repository, "chg-999")) is None


def test_add_comment_is_idempotent_by_key(
    merge_request_port: MergeRequestPort,
    comment_journal: Callable[[ChangeRequestRef], tuple[str, ...]],
    repository: RepositoryRef,
) -> None:
    ref = asyncio.run(merge_request_port.open(_open_request(repository), idempotency_key="k1"))
    asyncio.run(merge_request_port.add_comment(ref, "first", idempotency_key="c1"))
    asyncio.run(merge_request_port.add_comment(ref, "first", idempotency_key="c1"))
    assert comment_journal(ref) == ("first",)
    asyncio.run(merge_request_port.add_comment(ref, "second", idempotency_key="c2"))
    assert comment_journal(ref) == ("first", "second")


def test_merge_verifies_expected_sha(
    merge_request_port: MergeRequestPort, repository: RepositoryRef
) -> None:
    ref = asyncio.run(merge_request_port.open(_open_request(repository), idempotency_key="k1"))
    with pytest.raises(HeadMismatchError):
        asyncio.run(merge_request_port.merge(ref, expected_sha="deadbeef", idempotency_key="m1"))
    current = asyncio.run(merge_request_port.find_existing(repository, "chg-001"))
    assert current is not None and current.status is ChangeRequestStatus.OPEN
    asyncio.run(merge_request_port.merge(ref, expected_sha=HEAD, idempotency_key="m1"))
    merged = asyncio.run(merge_request_port.find_existing(repository, "chg-001"))
    assert merged is not None and merged.status is ChangeRequestStatus.MERGED


def test_merge_replay_is_a_no_op(
    merge_request_port: MergeRequestPort, repository: RepositoryRef
) -> None:
    ref = asyncio.run(merge_request_port.open(_open_request(repository), idempotency_key="k1"))
    asyncio.run(merge_request_port.merge(ref, expected_sha=HEAD, idempotency_key="m1"))
    asyncio.run(merge_request_port.merge(ref, expected_sha=HEAD, idempotency_key="m1"))
    merged = asyncio.run(merge_request_port.find_existing(repository, "chg-001"))
    assert merged is not None and merged.status is ChangeRequestStatus.MERGED
