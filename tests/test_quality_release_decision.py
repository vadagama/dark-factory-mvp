"""Decision-core tests of the release verification (T034, US5, ADR-011 p.6).

The core is deterministic and pure: every branch of the fixed check order,
fail-closed behavior on incomplete data, and the rollback signal on every
failure are covered without any I/O or clock.
"""

from dark_factory.changes.enums import ReleaseStatus
from dark_factory.changes.run_records import SmokeProbeEvidence
from dark_factory.quality.release import (
    DIGEST_UNKNOWN,
    ArgoHealthStatus,
    ArgoSyncStatus,
    ReleaseObservation,
    SmokeOutcome,
    SmokeStatus,
    evaluate_release,
    normalize_digest,
    pre_smoke_failure,
)

EXPECTED = "sha256:1111111111111111111111111111111111111111111111111111111111111111"
OBSERVED = "sha256:2222222222222222222222222222222222222222222222222222222222222222"


def _passed_probes() -> list[SmokeProbeEvidence]:
    return [
        SmokeProbeEvidence(name="http-health", passed=True, detail="HTTP 200"),
        SmokeProbeEvidence(name="http-digest", passed=True, detail="digest found"),
    ]


def _released_observation() -> ReleaseObservation:
    return ReleaseObservation(
        expected_digest=EXPECTED,
        observed_digest=EXPECTED,
        argo_sync_raw="Synced",
        argo_health_raw="Healthy",
        smoke=_passed_probes(),
    )


class TestDigestImmutabilityFirst:
    def test_expected_digest_unknown_fails_closed(self) -> None:
        decision = evaluate_release(ReleaseObservation(expected_digest=None))

        assert decision.status is ReleaseStatus.RELEASE_FAILED
        assert decision.reason is not None
        assert "expected digest is unknown" in decision.reason
        assert "FR-011" in decision.reason
        assert decision.rollback_signal is not None

    def test_whitespace_only_expected_digest_counts_as_absent(self) -> None:
        decision = evaluate_release(ReleaseObservation(expected_digest="   "))

        assert decision.status is ReleaseStatus.RELEASE_FAILED

    def test_observed_digest_unknown_fails_closed(self) -> None:
        decision = evaluate_release(ReleaseObservation(expected_digest=EXPECTED))

        assert decision.status is ReleaseStatus.RELEASE_FAILED
        assert decision.reason is not None
        assert "observed" in decision.reason and "unknown" in decision.reason
        assert decision.rollback_signal is not None

    def test_digest_mismatch_stops_the_release_and_names_both_sides(self) -> None:
        decision = evaluate_release(
            ReleaseObservation(expected_digest=EXPECTED, observed_digest=OBSERVED)
        )

        assert decision.status is ReleaseStatus.RELEASE_FAILED
        assert decision.reason is not None
        assert EXPECTED in decision.reason
        assert OBSERVED in decision.reason
        assert decision.rollback_signal is not None

    def test_digest_check_runs_before_argo_and_smoke(self) -> None:
        """A digest mismatch wins even when Argo and smoke would fail too."""
        decision = evaluate_release(
            ReleaseObservation(
                expected_digest=EXPECTED,
                observed_digest=OBSERVED,
                argo_sync_raw="Progressing",
                argo_health_raw="Degraded",
                smoke=[SmokeProbeEvidence(name="http-health", passed=False, detail="HTTP 500")],
            )
        )

        assert decision.status is ReleaseStatus.RELEASE_FAILED
        assert decision.reason is not None
        assert "does not match" in decision.reason


class TestArgoChecks:
    def test_out_of_sync_application_fails(self) -> None:
        decision = evaluate_release(
            ReleaseObservation(
                expected_digest=EXPECTED,
                observed_digest=EXPECTED,
                argo_sync_raw="OutOfSync",
                argo_health_raw="Healthy",
                smoke=_passed_probes(),
            )
        )

        assert decision.status is ReleaseStatus.RELEASE_FAILED
        assert decision.reason is not None
        assert "OutOfSync" in decision.reason and "ADR-010" in decision.reason

    def test_absent_sync_status_fails_closed(self) -> None:
        decision = evaluate_release(
            ReleaseObservation(expected_digest=EXPECTED, observed_digest=EXPECTED)
        )

        assert decision.status is ReleaseStatus.RELEASE_FAILED
        assert decision.reason is not None
        assert "unknown" in decision.reason

    def test_degraded_health_fails_after_synced_sync(self) -> None:
        decision = evaluate_release(
            ReleaseObservation(
                expected_digest=EXPECTED,
                observed_digest=EXPECTED,
                argo_sync_raw="Synced",
                argo_health_raw="Degraded",
                smoke=_passed_probes(),
            )
        )

        assert decision.status is ReleaseStatus.RELEASE_FAILED
        assert decision.reason is not None
        assert "Degraded" in decision.reason

    def test_progressing_health_fails(self) -> None:
        decision = evaluate_release(
            ReleaseObservation(
                expected_digest=EXPECTED,
                observed_digest=EXPECTED,
                argo_sync_raw="Synced",
                argo_health_raw="Progressing",
                smoke=_passed_probes(),
            )
        )

        assert decision.status is ReleaseStatus.RELEASE_FAILED

    def test_sync_check_runs_before_the_health_check(self) -> None:
        """Both Argo checks failing reports the sync failure first (ADR-010)."""
        decision = evaluate_release(
            ReleaseObservation(
                expected_digest=EXPECTED,
                observed_digest=EXPECTED,
                argo_sync_raw="OutOfSync",
                argo_health_raw="Degraded",
                smoke=_passed_probes(),
            )
        )

        assert decision.reason is not None
        assert "sync status" in decision.reason


class TestSmokeCheck:
    def test_missing_smoke_fails_closed(self) -> None:
        decision = evaluate_release(
            ReleaseObservation(
                expected_digest=EXPECTED,
                observed_digest=EXPECTED,
                argo_sync_raw="Synced",
                argo_health_raw="Healthy",
            )
        )

        assert decision.status is ReleaseStatus.RELEASE_FAILED
        assert decision.reason is not None
        assert "smoke was not run" in decision.reason

    def test_empty_probe_set_is_not_run(self) -> None:
        decision = evaluate_release(
            ReleaseObservation(
                expected_digest=EXPECTED,
                observed_digest=EXPECTED,
                argo_sync_raw="Synced",
                argo_health_raw="Healthy",
                smoke=(),
            )
        )

        assert decision.status is ReleaseStatus.RELEASE_FAILED
        assert decision.reason is not None
        assert "smoke was not run" in decision.reason

    def test_failing_smoke_does_not_release_and_names_the_probe(self) -> None:
        decision = evaluate_release(
            ReleaseObservation(
                expected_digest=EXPECTED,
                observed_digest=EXPECTED,
                argo_sync_raw="Synced",
                argo_health_raw="Healthy",
                smoke=[
                    SmokeProbeEvidence(name="http-health", passed=True, detail="HTTP 200"),
                    SmokeProbeEvidence(name="http-digest", passed=False, detail="digest not found"),
                ],
            )
        )

        assert decision.status is ReleaseStatus.RELEASE_FAILED
        assert decision.reason is not None
        assert "http-digest" in decision.reason
        assert "http-health" not in decision.reason
        assert decision.rollback_signal is not None

    def test_smoke_check_runs_after_argo_checks(self) -> None:
        """A smoke failure with an unhealthy Application reports the Argo status first."""
        decision = evaluate_release(
            ReleaseObservation(
                expected_digest=EXPECTED,
                observed_digest=EXPECTED,
                argo_sync_raw="Synced",
                argo_health_raw="Degraded",
                smoke=[SmokeProbeEvidence(name="http-health", passed=False, detail="HTTP 500")],
            )
        )

        assert decision.reason is not None
        assert "health status" in decision.reason


class TestReleased:
    def test_every_check_passing_releases_on_the_promoted_digest(self) -> None:
        decision = evaluate_release(_released_observation())

        assert decision.status is ReleaseStatus.RELEASED
        assert decision.reason is None
        assert decision.rollback_signal is None

    def test_raw_values_are_matched_case_insensitively(self) -> None:
        decision = evaluate_release(
            ReleaseObservation(
                expected_digest=f"  {EXPECTED}  ",
                observed_digest=EXPECTED,
                argo_sync_raw="  synced  ",
                argo_health_raw="HEALTHY",
                smoke=_passed_probes(),
            )
        )

        assert decision.status is ReleaseStatus.RELEASED

    def test_decision_is_deterministic(self) -> None:
        observation = _released_observation()

        assert evaluate_release(observation) == evaluate_release(observation)


class TestFailClosedOnIncompleteData:
    def test_everything_unknown_is_release_failed(self) -> None:
        decision = evaluate_release(ReleaseObservation())

        assert decision.status is ReleaseStatus.RELEASE_FAILED
        assert decision.rollback_signal is not None

    def test_good_smoke_cannot_compensate_an_unknown_digest(self) -> None:
        decision = evaluate_release(
            ReleaseObservation(smoke=_passed_probes(), argo_sync_raw="Synced")
        )

        assert decision.status is ReleaseStatus.RELEASE_FAILED


class TestArgoStatusNormalization:
    def test_exact_wire_values_match(self) -> None:
        assert ArgoSyncStatus.from_raw("Synced") is ArgoSyncStatus.SYNCED
        assert ArgoSyncStatus.from_raw("OutOfSync") is ArgoSyncStatus.OUT_OF_SYNC
        assert ArgoSyncStatus.from_raw("Unknown") is ArgoSyncStatus.UNKNOWN

    def test_case_and_whitespace_are_tolerated(self) -> None:
        assert ArgoSyncStatus.from_raw(" synced ") is ArgoSyncStatus.SYNCED
        assert ArgoSyncStatus.from_raw("OUTOFSYNC") is ArgoSyncStatus.OUT_OF_SYNC

    def test_unrecognized_and_absent_values_map_to_unknown(self) -> None:
        assert ArgoSyncStatus.from_raw("Bogus") is ArgoSyncStatus.UNKNOWN
        assert ArgoSyncStatus.from_raw(None) is ArgoSyncStatus.UNKNOWN
        assert ArgoSyncStatus.from_raw("") is ArgoSyncStatus.UNKNOWN

    def test_health_status_normalizes_the_same_way(self) -> None:
        assert ArgoHealthStatus.from_raw("healthy") is ArgoHealthStatus.HEALTHY
        assert ArgoHealthStatus.from_raw("Progressing") is ArgoHealthStatus.PROGRESSING
        assert ArgoHealthStatus.from_raw("totally-bogus") is ArgoHealthStatus.UNKNOWN
        assert ArgoHealthStatus.from_raw(None) is ArgoHealthStatus.UNKNOWN


class TestSmokeOutcome:
    def test_status_not_run_when_empty(self) -> None:
        assert SmokeOutcome().status is SmokeStatus.NOT_RUN

    def test_status_failed_on_any_failing_probe(self) -> None:
        outcome = SmokeOutcome.of(
            [
                SmokeProbeEvidence(name="a", passed=True),
                SmokeProbeEvidence(name="b", passed=False),
            ]
        )

        assert outcome.status is SmokeStatus.FAILED

    def test_status_passed_when_all_probes_pass(self) -> None:
        outcome = SmokeOutcome.of([SmokeProbeEvidence(name="a", passed=True)])

        assert outcome.status is SmokeStatus.PASSED


class TestNormalizeDigest:
    def test_none_stays_none(self) -> None:
        assert normalize_digest(None) is None

    def test_whitespace_only_counts_as_absent(self) -> None:
        assert normalize_digest("   ") is None

    def test_value_is_stripped(self) -> None:
        assert normalize_digest(f"  {EXPECTED}  ") == EXPECTED


def test_unknown_placeholder_is_stable() -> None:
    assert DIGEST_UNKNOWN == "unknown"


class TestPreSmokeFailure:
    def test_none_when_digest_and_argo_checks_pass(self) -> None:
        observation = ReleaseObservation(
            expected_digest=EXPECTED,
            observed_digest=EXPECTED,
            argo_sync_raw="Synced",
            argo_health_raw="Healthy",
        )

        assert pre_smoke_failure(observation) is None

    def test_digest_failure_stops_before_the_probes(self) -> None:
        observation = ReleaseObservation(expected_digest=EXPECTED, observed_digest=OBSERVED)

        failure = pre_smoke_failure(observation)

        assert failure is not None
        assert failure.status is ReleaseStatus.RELEASE_FAILED
        assert failure.reason is not None and "does not match" in failure.reason

    def test_argo_failure_stops_before_the_probes(self) -> None:
        observation = ReleaseObservation(
            expected_digest=EXPECTED,
            observed_digest=EXPECTED,
            argo_sync_raw="Synced",
            argo_health_raw="Degraded",
        )

        failure = pre_smoke_failure(observation)

        assert failure is not None
        assert failure.reason is not None and "Degraded" in failure.reason
