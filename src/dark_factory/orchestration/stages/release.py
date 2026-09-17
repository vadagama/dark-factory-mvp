"""Pure release resolver of the wait-resolution protocol (T-092 S4, ADR-024 §7 S4).

The release stage parked in ``waiting`` — the durable external-wait checkpoint
of ADR-006 p.8 — is resumed by the driver
(:func:`~dark_factory.orchestration.runner.advance_run`) once the release
facts it waits for resolve (ADR-024 §7 S4: digest → GitOps-MR → Argo sync →
smoke → release evidence). This module turns the observed facts
(:class:`ReleaseObservation`: the digest observed on the deployment, the raw
Argo Application sync/health statuses, the smoke probes behind the
:class:`~dark_factory.quality.release.probes.SmokeProbe` seam) into the
:class:`StageResult` of that same attempt, deciding through the pure release
core (:func:`~dark_factory.quality.release.decision.evaluate_release`, T034)
and carrying the outcome as the additive ``StageResult.release`` evidence —
the stage-level counterpart of ``RunRecord.release`` (T034, FR-011/FR-013).

Deterministic and pure — no I/O, no provider calls, no clock beyond the
explicit ``now`` parameter (the same discipline as the neighboring
:mod:`~dark_factory.orchestration.stages.gates` resolver). Fail-closed: an
observation that does not carry the full release facts (the deployed digest
and both Argo statuses) never resolves the wait — partial data must not wake a
waiting attempt (ADR-006 p.8), and the builder refuses such an observation so
the driver replays the waiting checkpoint instead. A failed verification
carries the rollback signal: revert the GitOps commit that pinned the digest
(ADR-010, ADR-011 p.6).

The resolution result belongs to the waiting attempt: identity verbatim from
the checkpoint, so the durable store supersedes the waiting checkpoint of the
same attempt (ADR-006 p.8). What the *flow* then decides with the result is
its business, as in ``gates``.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from dark_factory.changes.enums import (
    Gate,
    GateStatus,
    ReleaseStatus,
    StageStatus,
    StopOutcome,
)
from dark_factory.changes.findings import GateResult
from dark_factory.changes.next_action import ReleaseAction, StopAction
from dark_factory.changes.release_records import SmokeProbeEvidence
from dark_factory.changes.run import Change, ChangeRun, StageResult
from dark_factory.quality.release.decision import (
    ReleaseObservation as DecisionObservation,
)
from dark_factory.quality.release.decision import (
    SmokeOutcome,
    evaluate_release,
    normalize_digest,
)
from dark_factory.quality.release.evidence import build_release_evidence


@dataclass(frozen=True, slots=True)
class ReleaseObservation:
    """Release facts observed for one waiting release attempt (T-092 S4).

    A value-level observation — the driver stays port-free (ADR-024 p.5), the
    composition root derives it from the GitOps/Argo/smoke seams. The wait
    resolves only when the deployed digest and both raw Argo statuses are
    present (:func:`release_resolved`); ``expected_digest`` may stay ``None``
    when the checkpoint's release evidence already pins it.
    ``application`` is optional Argo bookkeeping passed through to the
    evidence; ``verified_at`` overrides the resolver's ``now`` stamp when set.
    """

    expected_digest: str | None
    observed_digest: str | None
    argo_sync_raw: str | None
    argo_health_raw: str | None
    smoke: Sequence[SmokeProbeEvidence] = ()
    application: str | None = None
    verified_at: datetime | None = None


def _present(value: str | None) -> bool:
    """Presence of an observed value; blank strings count as absent (fail-closed)."""
    return value is not None and value.strip() != ""


def release_resolved(observation: ReleaseObservation | None) -> bool:
    """Whether the observation resolves the external wait of the release stage (T-092 S4).

    The wait resolves only on the full release facts: the digest observed on
    the deployment and both raw Argo Application statuses (sync, health).
    ``None`` (nothing observed) and partial observations are never a
    resolution — partial data must not wake the waiting attempt (fail-closed,
    ADR-006 p.8). The expected digest may be absent: the checkpoint's release
    evidence carries it (T034).
    """
    if observation is None:
        return False
    return (
        _present(observation.observed_digest)
        and _present(observation.argo_sync_raw)
        and _present(observation.argo_health_raw)
    )


def build_release_resolution(
    *,
    run: ChangeRun,
    change: Change,
    checkpoint: StageResult,
    observation: ReleaseObservation,
    input_revision: str,
    attempt_number: int,
    now: datetime,
) -> StageResult:
    """Turn a resolved release observation into the final result of the waiting attempt.

    The result carries the waiting attempt's identity verbatim from the
    checkpoint — stage, run, change, attempt number, pinned input revision —
    so the durable store supersedes the waiting checkpoint of the same attempt
    (ADR-006 p.8). ``run``/``change`` and the ``attempt_number``/
    ``input_revision`` arguments exist for driver uniformity with
    :func:`~dark_factory.orchestration.stages.gates.build_gate_resolution`;
    the identity comes from the checkpoint.

    The expected digest falls back to the checkpoint's release evidence when
    the observation does not name one — the promotion pinned it there (T034,
    FR-011). The decision core (``quality.release.decision``) then rules in
    its fixed order: ``released`` succeeds the stage with the release action
    and the release gate passed at the observed digest, anything else blocks
    the attempt with the decision's diagnostics and the rollback signal
    (ADR-010, ADR-011 p.6). The outcome is persisted as the additive
    ``StageResult.release`` evidence via
    :func:`~dark_factory.quality.release.evidence.build_release_evidence`,
    stamped with ``observation.verified_at`` or ``now``.

    Raises ``ValueError`` when the observation does not resolve the wait
    (the driver replays the waiting checkpoint instead).
    """
    if not release_resolved(observation):
        raise ValueError(
            f"observation does not resolve the external wait of stage "
            f"{checkpoint.stage.value}; the driver replays the waiting checkpoint instead"
        )

    expected_digest = observation.expected_digest
    if expected_digest is None and checkpoint.release is not None:
        expected_digest = checkpoint.release.expected_digest

    decision_observation = DecisionObservation(
        expected_digest=expected_digest,
        observed_digest=observation.observed_digest,
        argo_sync_raw=observation.argo_sync_raw,
        argo_health_raw=observation.argo_health_raw,
        smoke=SmokeOutcome.of(observation.smoke),
    )
    decision = evaluate_release(decision_observation)
    release = build_release_evidence(
        decision_observation,
        decision,
        verified_at=observation.verified_at if observation.verified_at is not None else now,
        application=observation.application,
    )
    gate_sha = normalize_digest(observation.observed_digest)
    if decision.status is ReleaseStatus.RELEASED:
        return StageResult(
            stage=checkpoint.stage,
            run_id=checkpoint.run_id,
            change_id=checkpoint.change_id,
            attempt_number=checkpoint.attempt_number,
            input_revision=checkpoint.input_revision,
            status=StageStatus.SUCCEEDED,
            next_action=ReleaseAction(
                reason="release verified: digest unchanged, argo synced and healthy, smoke passed"
            ),
            gate_results=[
                GateResult(
                    gate=Gate.RELEASE,
                    status=GateStatus.PASSED,
                    sha=gate_sha,
                    summary="release verification passed",
                )
            ],
            release=release,
            produced_at=now,
        )
    reason = decision.reason or "release verification failed (T034)"
    if decision.rollback_signal is not None:
        reason = f"{reason}; rollback: {decision.rollback_signal}"
    return StageResult(
        stage=checkpoint.stage,
        run_id=checkpoint.run_id,
        change_id=checkpoint.change_id,
        attempt_number=checkpoint.attempt_number,
        input_revision=checkpoint.input_revision,
        status=StageStatus.BLOCKED,
        next_action=StopAction(outcome=StopOutcome.BLOCKED, reason=reason),
        gate_results=[
            GateResult(
                gate=Gate.RELEASE,
                status=GateStatus.FAILED,
                sha=gate_sha,
                summary=decision.reason,
            )
        ],
        release=release,
        produced_at=now,
    )
