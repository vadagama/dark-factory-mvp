"""Pure release resolver of the wait-resolution protocol (T-092 S4, ADR-024 §7 S4)."""

from datetime import UTC, datetime

import pytest

from dark_factory.changes.enums import (
    Gate,
    GateStatus,
    ReleaseStatus,
    Stage,
    StageStatus,
    StopOutcome,
)
from dark_factory.changes.next_action import ReleaseAction, StopAction, WaitForCIAction
from dark_factory.changes.run import Change, ChangeRun, StageResult
from dark_factory.changes.run_records import ReleaseEvidence, SmokeProbeEvidence
from dark_factory.orchestration.flow import expected_result_status
from dark_factory.orchestration.stages.release import (
    ReleaseObservation,
    build_release_resolution,
    release_resolved,
)
from tests.changes_factories import NOW, make_change, make_run

REVISION = "a1b2c3d"
EXPECTED_DIGEST = "sha256:3f7a1c9d"
OBSERVED_DIGEST = "sha256:3f7a1c9d"
MISMATCHED_DIGEST = "sha256:deadbeef"


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
