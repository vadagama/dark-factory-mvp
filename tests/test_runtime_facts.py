"""Tests of the SCM-backed facts provider (T-092 S3, ADR-024 p.5).

The provider is binding, not logic: these tests pin the value mapping — the
change request facts, the pipeline verdict read at the observed head SHA and
the reviews as version-bound human decisions — and the honest absences (no
change request, no reported head SHA) that leave the wait parked and the
driver replaying the checkpoint.
"""

import asyncio
from datetime import UTC, datetime

import pytest

from dark_factory.adapters.fakes import FakeMergeRequests
from dark_factory.changes.enums import (
    ChangeRequestStatus,
    DecisionOutcome,
    DecisionSource,
    Gate,
    RiskClass,
    Route,
    Stage,
)
from dark_factory.changes.findings import Decision
from dark_factory.changes.refs import ChangeRequestRef, RepositoryRef
from dark_factory.changes.run import Change, ChangeRun
from dark_factory.orchestration.policy.risk import missing_control_points
from dark_factory.orchestration.stages.gates import gate_resolved
from dark_factory.ports import (
    ChangeRequestObservation,
    OpenChangeRequest,
    PipelineStatus,
)
from dark_factory.runtime.facts import ScmFactsProvider
from tests.changes_factories import make_change, make_contract, make_run

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


def _run_declaring(risk_class: RiskClass, route: Route = Route.STANDARD) -> ChangeRun:
    """A run on ``route`` whose approved contract declares ``risk_class`` (the effective input)."""
    contract = make_contract().model_copy(update={"risk_class": risk_class})
    return make_run(route).model_copy(update={"implementation_contract": contract})


def _change_with_an_approving_review() -> tuple[Change, FakeMergeRequests]:
    """A change whose request carries one approving human review at ``HEAD``."""
    change = _change_with_request(make_change())
    merge_requests = FakeMergeRequests()
    ref = asyncio.run(merge_requests.open(_open_request(change), idempotency_key="k1"))
    merge_requests.record_review(
        ref, author="octocat", state="approved", commit_sha=HEAD, submitted_at=NOW
    )
    return change, merge_requests


def test_an_r2_run_maps_the_planning_review_to_the_widened_planning_gate() -> None:
    # B1 (T-043 increment 1): on R2+ the planning gate is human, so the review of
    # the plan change request must decide Gate.PLANNING — otherwise the wait never
    # resolves and the ``solution`` control point is never closed.
    run = _run_declaring(RiskClass.R2)
    change, merge_requests = _change_with_an_approving_review()

    observed = _provider(merge_requests, StubPipelines())(run, Stage.PLANNING, change)

    assert observed is not None
    assert [decision.gate for decision in observed.approvals] == [Gate.PLANNING]
    assert gate_resolved(
        observed, stage=Stage.PLANNING, route=Route.STANDARD, risk_class=RiskClass.R2
    )
    assert (
        missing_control_points(
            Route.STANDARD,
            Stage.PLANNING,
            RiskClass.R2,
            observed.approvals,
            sha=observed.head_sha,
        )
        == frozenset()
    )


def test_an_r1_run_keeps_the_planning_review_on_the_fallback_merge_gate() -> None:
    # The widened ``planning`` gate is R2+ only: on R1 the stage is machine-gated,
    # so its review must not become a human decision.
    run = _run_declaring(RiskClass.R1)
    change, merge_requests = _change_with_an_approving_review()

    observed = _provider(merge_requests, StubPipelines())(run, Stage.PLANNING, change)

    assert observed is not None
    assert [decision.gate for decision in observed.approvals] == [Gate.REVIEW]
    assert not gate_resolved(
        observed, stage=Stage.PLANNING, route=Route.STANDARD, risk_class=RiskClass.R1
    )


@pytest.mark.parametrize(
    ("route", "declared", "expected_gate", "resolves"),
    [
        # quick keeps planning machine-gated on R1: the risk set adds only
        # ``ui``, which planning does not require, so a review there falls back
        # to the merge gate and authorizes nothing (T062 quick/standard matrix).
        (Route.QUICK, RiskClass.R1, Gate.REVIEW, False),
        # The widening is class-driven, not route-driven: R2 makes planning
        # human on quick too, even though the band refuses to carry an R2 change.
        (Route.QUICK, RiskClass.R2, Gate.PLANNING, True),
        (Route.STANDARD, RiskClass.R1, Gate.REVIEW, False),
        (Route.STANDARD, RiskClass.R2, Gate.PLANNING, True),
    ],
)
def test_the_planning_review_gate_follows_the_route_and_the_risk_class(
    route: Route, declared: RiskClass, expected_gate: Gate, resolves: bool
) -> None:
    # B1 (T-043 increment 1) pinned the R2 widening on standard; the matrix keeps
    # it honest across the routes a run may travel (T062 DoD).
    run = _run_declaring(declared, route)
    change, merge_requests = _change_with_an_approving_review()

    observed = _provider(merge_requests, StubPipelines())(run, Stage.PLANNING, change)

    assert observed is not None
    assert [decision.gate for decision in observed.approvals] == [expected_gate]
    assert (
        gate_resolved(observed, stage=Stage.PLANNING, route=route, risk_class=declared) is resolves
    )


@pytest.mark.parametrize("route", [Route.QUICK, Route.STANDARD])
@pytest.mark.parametrize("declared", [RiskClass.R1, RiskClass.R2])
def test_specification_and_review_keep_their_base_gates_across_the_matrix(
    route: Route, declared: RiskClass
) -> None:
    run = _run_declaring(declared, route)
    change, merge_requests = _change_with_an_approving_review()
    provider = _provider(merge_requests, StubPipelines())

    at_specification = provider(run, Stage.SPECIFICATION, change)
    at_review = provider(run, Stage.REVIEW_VERIFICATION, change)

    assert at_specification is not None
    assert [decision.gate for decision in at_specification.approvals] == [Gate.SPECIFICATION]
    assert at_review is not None
    assert [decision.gate for decision in at_review.approvals] == [Gate.REVIEW]


def test_the_specification_and_review_mappings_are_unchanged() -> None:
    run = _run_declaring(RiskClass.R1)
    change, merge_requests = _change_with_an_approving_review()
    provider = _provider(merge_requests, StubPipelines())

    at_specification = provider(run, Stage.SPECIFICATION, change)
    at_review = provider(run, Stage.REVIEW_VERIFICATION, change)

    assert at_specification is not None
    assert [decision.gate for decision in at_specification.approvals] == [Gate.SPECIFICATION]
    assert at_review is not None
    assert [decision.gate for decision in at_review.approvals] == [Gate.REVIEW]


def test_a_merged_request_resolves_the_stage_regardless_of_the_gate_mapping() -> None:
    # The observed merge is the human decision on the review stage and is checked
    # before the gate mapping (ADR-011 p.2), so it must not depend on the class.
    change = _change_with_request(make_change())
    merge_requests = FakeMergeRequests()
    ref = asyncio.run(merge_requests.open(_open_request(change), idempotency_key="k1"))
    asyncio.run(merge_requests.merge(ref, expected_sha=HEAD, idempotency_key="m1"))

    observed = _provider(merge_requests, StubPipelines())(
        _run_declaring(RiskClass.R2), Stage.REVIEW_VERIFICATION, change
    )

    assert observed is not None and observed.merged is True
    assert gate_resolved(
        observed,
        stage=Stage.REVIEW_VERIFICATION,
        route=Route.STANDARD,
        risk_class=RiskClass.R2,
    )
