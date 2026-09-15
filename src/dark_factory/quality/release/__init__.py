"""Release verification of the Factory Flow: is the change Released? (T034, US5, docs T-045).

This package implements the release check of ADR-011 p.6: a change counts as
**Released** only when, in the fixed order below, every check passed on the
promoted immutable digest —

1. digest immutability first (FR-011): the expected digest (the T033
   ``image-digest.json`` artifact) equals the digest observed on the
   deployment;
2. the Argo Application is ``Synced`` (ADR-010);
3. the Argo Application is ``Healthy``;
4. the smoke probes ran and passed (FR-013).

Any other outcome — including missing data — is ``release_failed``
(fail-closed, the same discipline as ``merge_context=None`` → manual in
T-026) and carries the rollback signal for Operations (docs T-045): revert
the GitOps commit that pinned the digest, Argo CD auto-sync then restores
the previous one (ADR-010). An unsuccessful smoke never translates the
change into Released.

Layout:

- :mod:`dark_factory.quality.release.decision` — the deterministic, pure
  core: ``evaluate_release`` over ``ReleaseObservation`` (no I/O, no clock);
- :mod:`dark_factory.quality.release.probes` — the I/O seam of the smoke
  check: the ``SmokeProbe`` protocol plus the MVP ``HttpHealthProbe`` /
  ``HttpDigestProbe`` (httpx2, fixed timeouts/retries, URLs never echoed,
  ADR-009);
- :mod:`dark_factory.quality.release.evidence` — assembly of the persisted
  ``ReleaseEvidence`` section of the run record (ADR-015 p.4).

The CLI face is ``factory release verify`` (``dark_factory.cli.release``):
it collects the expected digest (flag or ``image-digest.json``), the
observed deployment state (passed as values — the MVP has no live Argo
client, YAGNI) and the smoke targets, runs the probes and prints the
decision with its evidence; exit code 0 only for ``released``.
"""

from dark_factory.quality.release.decision import (
    DIGEST_UNKNOWN,
    ROLLBACK_SIGNAL,
    ArgoHealthStatus,
    ArgoSyncStatus,
    ReleaseDecision,
    ReleaseObservation,
    SmokeOutcome,
    SmokeStatus,
    evaluate_release,
    normalize_digest,
    pre_smoke_failure,
)
from dark_factory.quality.release.evidence import build_release_evidence
from dark_factory.quality.release.probes import (
    SMOKE_PROBE_ATTEMPTS,
    SMOKE_PROBE_RETRY_DELAY_SECONDS,
    SMOKE_PROBE_TIMEOUT_SECONDS,
    HttpDigestProbe,
    HttpHealthProbe,
    SmokeProbe,
    run_smoke_probes,
)

__all__ = [
    "DIGEST_UNKNOWN",
    "ROLLBACK_SIGNAL",
    "SMOKE_PROBE_ATTEMPTS",
    "SMOKE_PROBE_RETRY_DELAY_SECONDS",
    "SMOKE_PROBE_TIMEOUT_SECONDS",
    "ArgoHealthStatus",
    "ArgoSyncStatus",
    "HttpDigestProbe",
    "HttpHealthProbe",
    "ReleaseDecision",
    "ReleaseObservation",
    "SmokeOutcome",
    "SmokeProbe",
    "SmokeStatus",
    "build_release_evidence",
    "evaluate_release",
    "normalize_digest",
    "pre_smoke_failure",
    "run_smoke_probes",
]
