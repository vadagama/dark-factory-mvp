"""Contract tests for MergeRequestPort."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime

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
SUBMITTED_AT = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)


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


def test_observe_reports_the_live_head_and_state(
    merge_request_port: MergeRequestPort, repository: RepositoryRef
) -> None:
    ref = asyncio.run(merge_request_port.open(_open_request(repository), idempotency_key="k1"))

    observed = asyncio.run(merge_request_port.observe(ref))

    assert observed.status is ChangeRequestStatus.OPEN
    assert observed.head_sha == HEAD
    assert observed.merged_sha is None
    assert observed.reviews == ()


def test_observe_reflects_the_merge(
    merge_request_port: MergeRequestPort, repository: RepositoryRef
) -> None:
    ref = asyncio.run(merge_request_port.open(_open_request(repository), idempotency_key="k1"))
    asyncio.run(merge_request_port.merge(ref, expected_sha=HEAD, idempotency_key="m1"))

    observed = asyncio.run(merge_request_port.observe(ref))

    assert observed.status is ChangeRequestStatus.MERGED
    # The head stays the SHA the gates and approvals bind to (FR-009);
    # the merge commit is reported separately.
    assert observed.head_sha == HEAD
    assert observed.merged_sha is not None


def test_observe_lists_submitted_reviews(
    merge_request_port: MergeRequestPort,
    review_seeder: Callable[..., object],
    repository: RepositoryRef,
) -> None:
    ref = asyncio.run(merge_request_port.open(_open_request(repository), idempotency_key="k1"))
    review_seeder(
        ref, author="octocat", state="approved", commit_sha=HEAD, submitted_at=SUBMITTED_AT
    )
    review_seeder(
        ref, author="hubot", state="changes_requested", commit_sha=HEAD, submitted_at=SUBMITTED_AT
    )
    review_seeder(
        ref, author="ghost", state="commented", commit_sha=HEAD, submitted_at=SUBMITTED_AT
    )

    observed = asyncio.run(merge_request_port.observe(ref))

    assert [(review.author, review.state, review.commit_sha) for review in observed.reviews] == [
        ("octocat", "approved", HEAD),
        ("hubot", "changes_requested", HEAD),
        ("ghost", "commented", HEAD),
    ]
    assert all(review.review_id for review in observed.reviews)
    assert all(review.submitted_at == SUBMITTED_AT for review in observed.reviews)


def test_observe_of_an_unknown_change_request_is_a_key_error(
    merge_request_port: MergeRequestPort, repository: RepositoryRef
) -> None:
    stale = ChangeRequestRef(repository=repository, number=999, status=ChangeRequestStatus.OPEN)

    with pytest.raises(KeyError):
        asyncio.run(merge_request_port.observe(stale))
