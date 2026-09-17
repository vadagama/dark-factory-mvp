"""Pure release resolver and the GitOps promotion executor of the release stage (T-092 S4)."""

import asyncio
from datetime import UTC, datetime

import pytest

from dark_factory.adapters.fakes.scm import FakeMergeRequests, FakeRepository
from dark_factory.changes.enums import (
    Gate,
    GateStatus,
    Provider,
    ReleaseStatus,
    Route,
    Stage,
    StageStatus,
    StopOutcome,
)
from dark_factory.changes.next_action import (
    ReleaseAction,
    StopAction,
    WaitForCIAction,
    WaitForInputAction,
)
from dark_factory.changes.refs import ChangeRequestRef, RepositoryRef
from dark_factory.changes.run import Change, ChangeRun, StageResult
from dark_factory.changes.run_records import ReleaseEvidence, SmokeProbeEvidence
from dark_factory.changes.usage import BudgetSnapshot
from dark_factory.orchestration.flow import expected_result_status
from dark_factory.orchestration.stages.context import StageContext, build_context
from dark_factory.orchestration.stages.release import (
    RELEASE_BRANCH_PREFIX,
    InnerStageExecutor,
    ReleaseObservation,
    ReleaseStageExecutor,
    build_release_resolution,
    release_resolved,
)
from tests.changes_factories import NOW, make_change, make_run

REVISION = "a1b2c3d"
EXPECTED_DIGEST = "sha256:3f7a1c9d"
OBSERVED_DIGEST = "sha256:3f7a1c9d"
MISMATCHED_DIGEST = "sha256:deadbeef"
GITOPS_REPOSITORY = RepositoryRef(provider=Provider.GITHUB, slug="small/gitops")


def _checkpoint(run: ChangeRun, change: Change) -> StageResult:
    """The waiting release checkpoint; its release section pins the expected digest.

    The evidence is the prior attempt's section — only ``expected_digest``
    feeds the resolver.
    """
    return StageResult(
        stage=Stage.RELEASE,
        run_id=run.id,
        change_id=change.id,
        attempt_number=1,
        input_revision=REVISION,
        status=StageStatus.WAITING,
        next_action=WaitForCIAction(reason="waiting for the release to roll out"),
        release=ReleaseEvidence(
            verified_at=NOW,
            decision=ReleaseStatus.RELEASE_FAILED,
            expected_digest=EXPECTED_DIGEST,
        ),
        produced_at=NOW,
    )


def _passing_probes() -> tuple[SmokeProbeEvidence, ...]:
    return (SmokeProbeEvidence(name="http-root", passed=True, detail="200"),)


def _build(observation: ReleaseObservation) -> StageResult:
    run = make_run()
    change = make_change()
    return build_release_resolution(
        run=run,
        change=change,
        checkpoint=_checkpoint(run, change),
        observation=observation,
        input_revision=REVISION,
        attempt_number=1,
        now=NOW,
    )


def test_nothing_observed_never_resolves_the_wait() -> None:
    assert release_resolved(None) is False


@pytest.mark.parametrize(
    ("observed_digest", "argo_sync_raw", "argo_health_raw", "resolved"),
    [
        (None, "Synced", "Healthy", False),
        (OBSERVED_DIGEST, None, "Healthy", False),
        (OBSERVED_DIGEST, "Synced", None, False),
        (OBSERVED_DIGEST, "Synced", "Healthy", True),
        ("", "Synced", "Healthy", False),
        ("  ", "Synced", "Healthy", False),
    ],
)
def test_release_resolved_truth_table(
    observed_digest: str | None,
    argo_sync_raw: str | None,
    argo_health_raw: str | None,
    resolved: bool,
) -> None:
    # A blank value is no observation: the wait resolves only on the full
    # release facts (fail-closed, ADR-006 p.8).
    observation = ReleaseObservation(
        expected_digest=None,
        observed_digest=observed_digest,
        argo_sync_raw=argo_sync_raw,
        argo_health_raw=argo_health_raw,
    )

    assert release_resolved(observation) is resolved


def test_released_observation_succeeds_the_stage_with_the_release_action() -> None:
    run = make_run()
    change = make_change()
    checkpoint = _checkpoint(run, change)

    result = build_release_resolution(
        run=run,
        change=change,
        checkpoint=checkpoint,
        observation=ReleaseObservation(
            expected_digest=EXPECTED_DIGEST,
            observed_digest=OBSERVED_DIGEST,
            argo_sync_raw="Synced",
            argo_health_raw="Healthy",
            smoke=_passing_probes(),
        ),
        input_revision=REVISION,
        attempt_number=1,
        now=NOW,
    )

    assert result.status is StageStatus.SUCCEEDED
    # The (status, action) pairing the flow engine enforces holds for the result.
    assert result.status is expected_result_status(result.next_action)
    action = result.next_action
    assert isinstance(action, ReleaseAction)
    assert action.reason == (
        "release verified: digest unchanged, argo synced and healthy, smoke passed"
    )
    assert [(item.gate, item.status, item.sha) for item in result.gate_results] == [
        (Gate.RELEASE, GateStatus.PASSED, OBSERVED_DIGEST)
    ]
    evidence = result.release
    assert evidence is not None
    assert evidence.decision is ReleaseStatus.RELEASED
    assert evidence.expected_digest == EXPECTED_DIGEST
    assert evidence.observed_digest == OBSERVED_DIGEST
    assert evidence.argo_sync_status == "Synced"
    assert evidence.argo_health_status == "Healthy"
    assert [probe.name for probe in evidence.smoke] == ["http-root"]
    assert evidence.verified_at == NOW  # the resolver stamps ``now``
    assert result.produced_at == NOW
    # The result carries the waiting attempt's identity verbatim (ADR-006 p.8).
    assert (result.stage, result.run_id, result.change_id) == (
        checkpoint.stage,
        checkpoint.run_id,
        checkpoint.change_id,
    )
    assert result.attempt_number == checkpoint.attempt_number
    assert result.input_revision == checkpoint.input_revision


def test_a_failed_verification_blocks_with_the_rollback_signal() -> None:
    result = _build(
        ReleaseObservation(
            expected_digest=EXPECTED_DIGEST,
            observed_digest=MISMATCHED_DIGEST,
            argo_sync_raw="Synced",
            argo_health_raw="Healthy",
            smoke=_passing_probes(),
        )
    )

    assert result.status is StageStatus.BLOCKED
    assert result.status is expected_result_status(result.next_action)
    stop = result.next_action
    assert isinstance(stop, StopAction)
    assert stop.outcome is StopOutcome.BLOCKED
    assert "does not match the observed digest" in stop.reason
    assert "; rollback: " in stop.reason
    assert [(item.gate, item.status, item.sha) for item in result.gate_results] == [
        (Gate.RELEASE, GateStatus.FAILED, MISMATCHED_DIGEST)
    ]
    evidence = result.release
    assert evidence is not None
    assert evidence.decision is ReleaseStatus.RELEASE_FAILED
    assert evidence.observed_digest == MISMATCHED_DIGEST
    assert evidence.rollback_signal is not None
    # The gate summary carries the decision's diagnostics, not the stop reason.
    assert result.gate_results[0].summary == evidence.reason


def test_an_unresolved_observation_is_refused_by_the_builder() -> None:
    with pytest.raises(ValueError):
        _build(
            ReleaseObservation(
                expected_digest=EXPECTED_DIGEST,
                observed_digest=OBSERVED_DIGEST,
                argo_sync_raw="Synced",
                argo_health_raw=None,  # health not observed yet: partial
                smoke=_passing_probes(),
            )
        )


def test_expected_digest_falls_back_to_the_checkpoint_evidence() -> None:
    result = _build(
        ReleaseObservation(
            expected_digest=None,
            observed_digest=OBSERVED_DIGEST,
            argo_sync_raw="Synced",
            argo_health_raw="Healthy",
            smoke=_passing_probes(),
        )
    )

    evidence = result.release
    assert evidence is not None
    # The fallback fed the decision: the unchanged digest released the stage.
    assert evidence.decision is ReleaseStatus.RELEASED
    assert evidence.expected_digest == EXPECTED_DIGEST

    # An explicitly observed expected digest wins over the checkpoint's.
    override = _build(
        ReleaseObservation(
            expected_digest="sha256:promoted-2",
            observed_digest="sha256:promoted-2",
            argo_sync_raw="Synced",
            argo_health_raw="Healthy",
            smoke=_passing_probes(),
        )
    )
    assert override.release is not None
    assert override.release.expected_digest == "sha256:promoted-2"
    assert override.release.decision is ReleaseStatus.RELEASED


def test_observed_verified_at_overrides_the_resolver_clock() -> None:
    verified_at = datetime(2026, 9, 13, 11, 30, 0, tzinfo=UTC)

    result = _build(
        ReleaseObservation(
            expected_digest=EXPECTED_DIGEST,
            observed_digest=OBSERVED_DIGEST,
            argo_sync_raw="Synced",
            argo_health_raw="Healthy",
            smoke=_passing_probes(),
            verified_at=verified_at,
        )
    )

    assert result.release is not None
    assert result.release.verified_at == verified_at
    assert result.produced_at == NOW


# --- GitOps promotion executor (T-092 S4, ADR-024 §7 S4) ---------------------


def _find_change_request(
    merge_requests: FakeMergeRequests, run_id: str = "run-001"
) -> ChangeRequestRef | None:
    """Sync read view over the async ``find_existing`` (tests are synchronous)."""
    return asyncio.run(merge_requests.find_existing(GITOPS_REPOSITORY, run_id))


def _release_context(*, change: Change | None = None, run_id: str = "run-001") -> StageContext:
    """The fixed context of one release attempt on the standard route (FR-001)."""
    return build_context(
        change=change if change is not None else make_change(),
        stage=Stage.RELEASE,
        route=Route.STANDARD,
        run_id=run_id,
        input_revision=REVISION,
        budget=BudgetSnapshot(),
    )


def _inner_recording(contexts: list[StageContext]) -> InnerStageExecutor:
    """An inner executor that records its context and returns one canned result."""

    def execute(context: StageContext) -> StageResult:
        contexts.append(context)
        return _canned_inner_result(context)

    return execute


def _canned_inner_result(context: StageContext) -> StageResult:
    return StageResult(
        stage=context.stage,
        run_id=context.run_id,
        change_id=context.change.id,
        attempt_number=context.attempt_number,
        input_revision=context.input_revision,
        status=StageStatus.WAITING,
        next_action=WaitForInputAction(reason="inner deterministic path"),
        produced_at=NOW,
    )


def _executor(
    *,
    expected_digest: str | None = EXPECTED_DIGEST,
    repository: FakeRepository | None = None,
    merge_requests: FakeMergeRequests | None = None,
    inner: InnerStageExecutor | None = None,
) -> ReleaseStageExecutor:
    return ReleaseStageExecutor(
        inner if inner is not None else _inner_recording([]),
        repository=repository if repository is not None else FakeRepository(),
        merge_requests=merge_requests if merge_requests is not None else FakeMergeRequests(),
        gitops_repository=GITOPS_REPOSITORY,
        expected_digest=expected_digest,
    )


def _promotion_branch() -> str:
    return f"{RELEASE_BRANCH_PREFIX}/run-001"


def _seed_gitops_main(repository: FakeRepository) -> None:
    """Seed the GitOps repository's default branch (the base the promotion forks from)."""
    asyncio.run(
        repository.ensure_branch(
            GITOPS_REPOSITORY, "main", from_revision="base", idempotency_key="seed-main"
        )
    )


def test_a_non_release_stage_delegates_to_inner_without_any_promotion() -> None:
    """The executor owns only the release stage; construction work is inner's business."""
    inner_contexts: list[StageContext] = []
    repository = FakeRepository()
    merge_requests = FakeMergeRequests()
    context = build_context(
        change=make_change(),
        stage=Stage.CONSTRUCTION,
        route=Route.STANDARD,
        run_id="run-001",
        input_revision=REVISION,
        budget=BudgetSnapshot(),
    )

    result = _executor(
        repository=repository,
        merge_requests=merge_requests,
        inner=_inner_recording(inner_contexts),
    )(context)

    assert result.status is StageStatus.WAITING
    assert inner_contexts == [context]
    assert repository.commits_of(GITOPS_REPOSITORY, _promotion_branch()) == ()
    assert _find_change_request(merge_requests) is None


def test_release_without_an_expected_digest_blocks_before_any_effect() -> None:
    """Promoting "something" would be an invented release: honest blocked, no effects."""
    repository = FakeRepository()
    merge_requests = FakeMergeRequests()
    context = _release_context()

    for expected_digest in (None, "", "   "):
        result = _executor(
            expected_digest=expected_digest,
            repository=repository,
            merge_requests=merge_requests,
        )(context)

        assert result.status is StageStatus.BLOCKED
        assert result.status is expected_result_status(result.next_action)
        stop = result.next_action
        assert isinstance(stop, StopAction)
        assert "expected digest" in stop.reason
        assert all(item.status is GateStatus.PENDING for item in result.gate_results)

    assert repository.commits_of(GITOPS_REPOSITORY, _promotion_branch()) == ()
    assert _find_change_request(merge_requests) is None


def test_release_promotes_the_digest_and_parks_waiting() -> None:
    """One commit pins the digest, the promotion MR is opened, the stage waits."""
    repository = FakeRepository()
    _seed_gitops_main(repository)
    merge_requests = FakeMergeRequests()
    context = _release_context()

    result = _executor(repository=repository, merge_requests=merge_requests)(context)

    branch = _promotion_branch()
    commits = repository.commits_of(GITOPS_REPOSITORY, branch)
    assert len(commits) == 1
    files = repository.commit_files(commits[0])
    assert files == {"releases/run-001/digest": f"{EXPECTED_DIGEST}\n".encode()}
    change_request = _find_change_request(merge_requests)
    assert change_request is not None
    assert change_request.number == 1
    assert result.status is StageStatus.WAITING
    assert result.status is expected_result_status(result.next_action)
    action = result.next_action
    assert isinstance(action, WaitForCIAction)
    assert action.change_request == change_request
    assert EXPECTED_DIGEST in action.reason
    assert all(item.status is GateStatus.PENDING for item in result.gate_results)
    assert [(item.artifact_type, item.revision, item.producer) for item in result.artifacts] == [
        ("change_request", commits[0], "ci_cd")
    ]
    assert (result.run_id, result.change_id, result.attempt_number, result.input_revision) == (
        context.run_id,
        context.change.id,
        context.attempt_number,
        context.input_revision,
    )


def test_release_promotion_replays_into_the_same_effects() -> None:
    """A retry of the same operation re-plays one commit and one MR (FR-017)."""
    repository = FakeRepository()
    _seed_gitops_main(repository)
    merge_requests = FakeMergeRequests()
    context = _release_context()
    execute = _executor(repository=repository, merge_requests=merge_requests)

    first = execute(context)
    second = execute(context)

    assert len(repository.commits_of(GITOPS_REPOSITORY, _promotion_branch())) == 1
    first_action = first.next_action
    second_action = second.next_action
    assert isinstance(first_action, WaitForCIAction)
    assert isinstance(second_action, WaitForCIAction)
    assert first_action.change_request is not None
    assert second_action.change_request == first_action.change_request
    assert _find_change_request(merge_requests) is not None


def test_a_new_digest_lands_a_new_commit_on_the_same_branch() -> None:
    """The digest is part of the commit key: a re-promotion pins the new digest."""
    repository = FakeRepository()
    _seed_gitops_main(repository)
    merge_requests = FakeMergeRequests()
    context = _release_context()

    _executor(repository=repository, merge_requests=merge_requests)(context)
    repromoted = _executor(
        expected_digest="sha256:promoted-2",
        repository=repository,
        merge_requests=merge_requests,
    )(context)

    commits = repository.commits_of(GITOPS_REPOSITORY, _promotion_branch())
    assert len(commits) == 2
    assert repository.commit_files(commits[1]) == {
        "releases/run-001/digest": b"sha256:promoted-2\n"
    }
    action = repromoted.next_action
    assert isinstance(action, WaitForCIAction)
    # The promotion MR already exists: it is found, never opened twice (FR-011).
    assert action.change_request is not None
    assert action.change_request.number == 1


def test_a_failing_gitops_provider_blocks_without_provider_text() -> None:
    """Provider errors become retryable blocked attempts with the type name only (ADR-009)."""

    class ExplodingRepository(FakeRepository):
        async def get_revision(self, repository, ref, /):
            raise RuntimeError("http://secret-scm.example/token")

    result = _executor(repository=ExplodingRepository())(_release_context())

    assert result.status is StageStatus.BLOCKED
    stop = result.next_action
    assert isinstance(stop, StopAction)
    assert "RuntimeError" in stop.reason
    assert "secret-scm" not in stop.reason
    assert all(item.status is GateStatus.PENDING for item in result.gate_results)
