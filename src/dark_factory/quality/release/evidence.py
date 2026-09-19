"""Assembly of the release evidence from an observation and its decision (T034).

The bridge between the pure decision core (``quality.release.decision``) and
the persisted run-record section (``RunRecord.release``): digests are
normalized the same way the decision saw them, the raw Argo statuses are
recorded verbatim as observed, and the smoke results are copied from the
observation. The evidence stays an honest index of what was seen at
verification time (ADR-015 p.4) — nothing is recomputed here.
"""

from datetime import datetime

from dark_factory.changes.run_records import ReleaseEvidence
from dark_factory.quality.release.decision import (
    ReleaseDecision,
    ReleaseObservation,
    normalize_digest,
)


def build_release_evidence(
    observation: ReleaseObservation,
    decision: ReleaseDecision,
    *,
    verified_at: datetime,
    application: str | None = None,
) -> ReleaseEvidence:
    """Build the ``RunRecord.release`` section from one verification (T034).

    ``verified_at`` is supplied by the caller (the CLI stamps the wall-clock
    moment of the verification — the only clock touch of the flow). The
    target ``application`` is optional bookkeeping (``namespace/name`` of the
    Argo Application); an empty value counts as absent.
    """
    application_clean = application.strip() if application is not None else None
    return ReleaseEvidence(
        verified_at=verified_at,
        decision=decision.status,
        reason=decision.reason,
        expected_digest=normalize_digest(observation.expected_digest),
        observed_digest=normalize_digest(observation.observed_digest),
        argo_sync_status=observation.argo_sync_raw,
        argo_health_status=observation.argo_health_raw,
        application=application_clean or None,
        smoke=tuple(observation.smoke),
        rollback_signal=decision.rollback_signal,
    )
