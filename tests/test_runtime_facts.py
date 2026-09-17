"""Tests of the SCM-backed facts provider (T-092 S3, ADR-024 p.5).

The provider is binding, not logic: these tests pin the value mapping — the
change request facts, the pipeline verdict read at the observed head SHA and
the reviews as version-bound human decisions — and the honest absences (no
change request, no reported head SHA) that leave the wait parked and the
driver replaying the checkpoint.
"""

import asyncio
from datetime import UTC, datetime

from dark_factory.adapters.fakes import FakeMergeRequests
from dark_factory.changes.enums import (
    ChangeRequestStatus,
    DecisionOutcome,
    DecisionSource,
    Gate,
    Stage,
)
from dark_factory.changes.findings import Decision
from dark_factory.changes.refs import ChangeRequestRef, RepositoryRef
from dark_factory.changes.run import Change
from dark_factory.ports import (
    ChangeRequestObservation,
    OpenChangeRequest,
    PipelineStatus,
)
from dark_factory.runtime.facts import ScmFactsProvider
from tests.changes_factories import make_change, make_run

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
HEAD = "abc1234"


class StubPipelines:
    """``PipelinePort`` stub with one canned verdict per ref (the fake always queues)."""

    def __init__(self, **status_by_ref: str) -> None:
        self._status_by_ref = status_by_ref
        self.asked: list[str] = []

    async def status(self, repository: RepositoryRef, ref: str, /) -> PipelineStatus:
        self.asked.append(ref)
        return PipelineStatus(ref=ref, status=self._status_by_ref.get(ref, "queued"), url=None)


class HeadlessMergeRequests(FakeMergeRequests):
    """Fake whose requests report no head SHA (a provider that does not expose it)."""

    async def observe(self, cr: ChangeRequestRef, /) -> ChangeRequestObservation:
        observed = await super().observe(cr)
        return ChangeRequestObservation(
            status=observed.status,
            head_sha=None,
            merged_sha=None,
            reviews=observed.reviews,
        )


def _provider(merge_requests: FakeMergeRequests, pipelines: StubPipelines) -> ScmFactsProvider:
    return ScmFactsProvider(merge_requests, pipelines)


def _change_with_request(change: Change, number: int = 1) -> Change:
    ref = ChangeRequestRef(
        repository=change.product, number=number, status=ChangeRequestStatus.OPEN
    )
    return change.model_copy(update={"change_request": ref})


def _open_request(change: Change) -> OpenChangeRequest:
    return OpenChangeRequest(
        repository=change.product,
        change_id=change.id,
        source_branch="feat/x",
        target_branch="main",
        title="Add export button",
        head_sha=HEAD,
    )


def test_without_a_change_request_there_is_nothing_observed() -> None:
    change = make_change()  # the snapshot carries no change request
    pipelines = StubPipelines()

    observed = _provider(FakeMergeRequests(), pipelines)(make_run(), Stage.CONSTRUCTION, change)

    assert observed is None
    assert pipelines.asked == []


def test_the_change_request_is_found_by_change_id_when_the_snapshot_has_none() -> None:
    # FR-011 cold lookup: the checkpoint may outlive the process that opened
    # the request, so the provider falls back to the change id.
    change = make_change()
    merge_requests = FakeMergeRequests()
    asyncio.run(merge_requests.open(_open_request(change), idempotency_key="k1"))
    pipelines = StubPipelines(**{HEAD: "success"})

    observed = _provider(merge_requests, pipelines)(make_run(), Stage.CONSTRUCTION, change)

    assert observed is not None
    assert observed.head_sha == HEAD
    assert observed.merged is False
    assert observed.pipeline_status == "success"
    assert observed.approvals == ()
    assert pipelines.asked == [HEAD]


def test_the_observation_carries_the_change_request_facts_and_the_pipeline_verdict() -> None:
    change = _change_with_request(make_change())
    merge_requests = FakeMergeRequests()
    asyncio.run(merge_requests.open(_open_request(change), idempotency_key="k1"))
    pipelines = StubPipelines(**{HEAD: "in_progress"})

    observed = _provider(merge_requests, pipelines)(make_run(), Stage.CONSTRUCTION, change)

    assert observed is not None
    assert observed.head_sha == HEAD
    assert observed.merged is False
    assert observed.pipeline_status == "in_progress"
    assert pipelines.asked == [HEAD]


def test_a_merged_change_request_is_observed_as_merged() -> None:
    change = _change_with_request(make_change())
    merge_requests = FakeMergeRequests()
    ref = asyncio.run(merge_requests.open(_open_request(change), idempotency_key="k1"))
    asyncio.run(merge_requests.merge(ref, expected_sha=HEAD, idempotency_key="m1"))
    pipelines = StubPipelines()

    observed = _provider(merge_requests, pipelines)(make_run(), Stage.REVIEW_VERIFICATION, change)

    assert observed is not None
    assert observed.merged is True


def test_reviews_become_version_bound_human_decisions() -> None:
    change = _change_with_request(make_change())
    merge_requests = FakeMergeRequests()
    ref = asyncio.run(merge_requests.open(_open_request(change), idempotency_key="k1"))
    merge_requests.record_review(
        ref, author="octocat", state="approved", commit_sha=HEAD, submitted_at=NOW
    )
    merge_requests.record_review(
        ref, author="hubot", state="changes_requested", commit_sha=HEAD, submitted_at=NOW
    )
    # Not decisions or not bound to a SHA: neither authorizes anything.
    merge_requests.record_review(ref, author="ghost", state="commented", commit_sha=HEAD)
    merge_requests.record_review(ref, author="nobody", state="approved", submitted_at=NOW)
    pipelines = StubPipelines()

    observed = _provider(merge_requests, pipelines)(make_run(), Stage.REVIEW_VERIFICATION, change)

    assert observed is not None
    assert observed.approvals == (
        Decision(
            id="review:review-1",
            gate=Gate.REVIEW,
            outcome=DecisionOutcome.APPROVED,
            decided_by=DecisionSource.HUMAN,
            decided_at=NOW,
            commit_sha=HEAD,
        ),
        Decision(
            id="review:review-2",
            gate=Gate.REVIEW,
            outcome=DecisionOutcome.REJECTED,
            decided_by=DecisionSource.HUMAN,
            decided_at=NOW,
            commit_sha=HEAD,
        ),
    )


def test_a_headless_change_request_yields_no_pipeline_fact() -> None:
    # FR-009: without a head SHA there is nothing the verdict could bind to,
    # so none is fabricated — the observation degrades, the wait stays parked.
    change = _change_with_request(make_change())
    merge_requests = HeadlessMergeRequests()
    asyncio.run(merge_requests.open(_open_request(change), idempotency_key="k1"))
    pipelines = StubPipelines(**{HEAD: "success"})

    observed = _provider(merge_requests, pipelines)(make_run(), Stage.CONSTRUCTION, change)

    assert observed is not None
    assert observed.head_sha is None
    assert observed.pipeline_status is None
    assert pipelines.asked == []


def test_reobserving_the_same_reviews_yields_the_same_decisions() -> None:
    # The decision ids are namespaced by the provider review id, so a repeated
    # observation deduplicates in the decision store instead of duplicating.
    change = _change_with_request(make_change())
    merge_requests = FakeMergeRequests()
    ref = asyncio.run(merge_requests.open(_open_request(change), idempotency_key="k1"))
    merge_requests.record_review(
        ref, author="octocat", state="approved", commit_sha=HEAD, submitted_at=NOW
    )
    provider = _provider(merge_requests, StubPipelines())

    first = provider(make_run(), Stage.REVIEW_VERIFICATION, change)
    second = provider(make_run(), Stage.REVIEW_VERIFICATION, change)

    assert first is not None and second is not None
    assert second.approvals == first.approvals
