"""Release decision of the Factory Flow: is the change Released? (T034, docs T-045, ADR-011 p.6).

Deterministic, pure-domain decision whether a deployed release may be marked
as Released: no harness/LLM calls, no I/O, no clock (same discipline as
``orchestration.policy.merge`` and ``orchestration.reconcile.rules``). The
input is observed data only: the expected immutable digest (the T033
``image-digest.json`` artifact), the digest observed on the deployment, the
Argo Application sync/health status and the smoke-probe results (FR-013) run
behind the :class:`~dark_factory.quality.release.probes.SmokeProbe` seam.
:class:`ReleaseObservation` is the one value-level carrier of those facts:
``factory release verify`` builds it from its flags, and the durable driver
(``orchestration.runner``, T-092 S4) receives it from the release facts
provider to resume a waiting release stage (``orchestration.stages.release``).

Fixed check order — the first triggered outcome wins (DoD T034):

1. **digest immutability first** (FR-011): the expected or observed digest
   unknown, or a mismatch between the two, stops the release — the deployed
   version is not the promoted one;
2. **Argo sync** (ADR-010): the Application must be ``Synced``;
3. **Argo health**: the Application must be ``Healthy``;
4. **smoke** (FR-013, ADR-011 p.6): the probes must have run and passed.

Fail-closed: missing data is a failed check — absent evidence never releases
(the same discipline as ``merge_context=None`` → manual in T-026). Every
failure carries the rollback signal: revert the GitOps commit that pinned the
digest and Argo CD auto-sync restores the previous one (ADR-010, ADR-011
p.6) — the Operation-facing diagnostics of docs T-045.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final

from dark_factory.changes.enums import ReleaseStatus
from dark_factory.changes.run_records import SmokeProbeEvidence

DIGEST_UNKNOWN: Final[str] = "unknown"
"""Placeholder in diagnostics for a digest side that was not observed."""


def _folded_raw(raw: str | None) -> str | None:
    """Casefolded raw observed value for member matching; absent → ``None``."""
    if raw is None:
        return None
    stripped = raw.strip()
    return stripped.casefold() or None


class ArgoSyncStatus(StrEnum):
    """Sync status of an Argo Application (ADR-010); canonical Argo wire values."""

    SYNCED = "Synced"
    OUT_OF_SYNC = "OutOfSync"
    UNKNOWN = "Unknown"

    @classmethod
    def from_raw(cls, raw: str | None) -> "ArgoSyncStatus":
        """Canonical status of a raw observed value.

        Matching is case-insensitive and whitespace-tolerant; an unrecognized
        or absent value maps to ``UNKNOWN`` — fail-closed, never an error.
        """
        folded = _folded_raw(raw)
        for member in cls:
            if folded == member.value.casefold():
                return member
        return ArgoSyncStatus.UNKNOWN


class ArgoHealthStatus(StrEnum):
    """Health status of an Argo Application; canonical Argo wire values."""

    HEALTHY = "Healthy"
    PROGRESSING = "Progressing"
    DEGRADED = "Degraded"
    SUSPENDED = "Suspended"
    MISSING = "Missing"
    UNKNOWN = "Unknown"

    @classmethod
    def from_raw(cls, raw: str | None) -> "ArgoHealthStatus":
        """Canonical status of a raw observed value; unrecognized → ``UNKNOWN``."""
        folded = _folded_raw(raw)
        for member in cls:
            if folded == member.value.casefold():
                return member
        return ArgoHealthStatus.UNKNOWN


class SmokeStatus(StrEnum):
    """Aggregated outcome of the smoke-probe set (FR-013, ADR-011 p.6)."""

    NOT_RUN = "not_run"
    PASSED = "passed"
    FAILED = "failed"


@dataclass(frozen=True)
class SmokeOutcome:
    """Smoke-probe results of one verification, as data (T034).

    An empty probe set is ``NOT_RUN`` — a release without smoke evidence is
    not Released (FR-013); one failed probe fails the whole set (ADR-011 p.6).
    """

    probes: tuple[SmokeProbeEvidence, ...] = ()

    @property
    def status(self) -> SmokeStatus:
        """Aggregated status: ``NOT_RUN`` when empty, ``FAILED`` on any failure."""
        if not self.probes:
            return SmokeStatus.NOT_RUN
        if any(not probe.passed for probe in self.probes):
            return SmokeStatus.FAILED
        return SmokeStatus.PASSED

    @classmethod
    def of(cls, probes: Sequence[SmokeProbeEvidence]) -> "SmokeOutcome":
        """Outcome over a probe-result sequence (convenience for callers/tests)."""
        return cls(probes=tuple(probes))


@dataclass(frozen=True)
class ReleaseObservation:
    """Facts of one release verification as observed by the caller (T034, T-092 S4).

    The digests are the promoted (expected) and the deployed (observed)
    immutable image digests; ``argo_sync_raw``/``argo_health_raw`` are the
    raw Argo Application status values (normalized only inside
    :func:`evaluate_release`, and recorded verbatim in the evidence);
    ``smoke`` is the raw probe evidence — empty when no probe ran at all
    (folded to :class:`SmokeOutcome` by :attr:`smoke_outcome`, so an empty
    set is ``not_run`` and fails closed, FR-013).

    The same value travels through the durable driver (ADR-024 p.5: the
    driver stays port-free, the release facts provider derives the
    observation from the GitOps/Argo/smoke seams). ``expected_digest`` may
    stay ``None`` there when the waiting checkpoint's release evidence
    already pins it; ``application`` is optional Argo bookkeeping
    (``namespace/name``) passed through to the evidence, and ``verified_at``
    overrides the resolver's ``now`` stamp when set. The decision core reads
    neither: they are transport for the evidence builder.
    """

    expected_digest: str | None = None
    observed_digest: str | None = None
    argo_sync_raw: str | None = None
    argo_health_raw: str | None = None
    smoke: Sequence[SmokeProbeEvidence] = ()
    application: str | None = None
    verified_at: datetime | None = None

    @property
    def smoke_outcome(self) -> SmokeOutcome:
        """The probe evidence folded into the aggregated smoke outcome (FR-013)."""
        return SmokeOutcome.of(self.smoke)


@dataclass(frozen=True)
class ReleaseDecision:
    """Frozen outcome of one release verification (T034, ADR-011 p.6).

    ``reason`` carries human-readable diagnostics naming the failed check and
    both digest sides on a mismatch. ``rollback_signal`` is the Operation
    remediation (revert the GitOps commit, ADR-010) — set exactly when the
    decision is ``release_failed``.
    """

    status: ReleaseStatus
    reason: str | None = None
    rollback_signal: str | None = None


ROLLBACK_SIGNAL: Final[str] = (
    "revert the GitOps commit that pinned this digest in the GitOps repository:"
    " Argo CD auto-sync then restores the previous digest (ADR-010, ADR-011 p.6)"
)
"""Operation-facing rollback instruction carried by every failed decision (docs T-045)."""


def normalize_digest(value: str | None) -> str | None:
    """Stripped digest value; empty and whitespace-only values count as absent."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _digest_failure(observation: ReleaseObservation) -> ReleaseDecision | None:
    """The FR-011 digest checks; the first failing check wins."""
    expected = normalize_digest(observation.expected_digest)
    observed = normalize_digest(observation.observed_digest)
    if expected is None:
        return _failed("cannot verify digest immutability: the expected digest is unknown (FR-011)")
    if observed is None:
        return _failed(
            "cannot verify digest immutability: the digest observed on the deployment "
            "is unknown (FR-011)"
        )
    if expected != observed:
        return _failed(
            f"expected digest {expected} does not match the observed digest {observed}: "
            "the release is stopped (FR-011)"
        )
    return None


def _argo_failure(observation: ReleaseObservation) -> ReleaseDecision | None:
    """The ADR-010 Argo Application checks, in the fixed sync-then-health order."""
    sync = ArgoSyncStatus.from_raw(observation.argo_sync_raw)
    if sync is not ArgoSyncStatus.SYNCED:
        return _failed(
            f"argo application sync status is {_raw_or_unknown(observation.argo_sync_raw)}, "
            f"not {ArgoSyncStatus.SYNCED.value!r} (ADR-010)"
        )
    health = ArgoHealthStatus.from_raw(observation.argo_health_raw)
    if health is not ArgoHealthStatus.HEALTHY:
        return _failed(
            f"argo application health status is {_raw_or_unknown(observation.argo_health_raw)}, "
            f"not {ArgoHealthStatus.HEALTHY.value!r} (ADR-010)"
        )
    return None


def pre_smoke_failure(observation: ReleaseObservation) -> ReleaseDecision | None:
    """Decision of the checks that precede the smoke probes, when one failed (T034).

    ``None`` means the digest immutability (FR-011) and the Argo checks
    (ADR-010) all passed — the caller may run the smoke probes. A returned
    decision stops the release before any probe runs: a wrong digest must
    not fire probes against a deployment that is not the promoted one.
    """
    return _digest_failure(observation) or _argo_failure(observation)


def evaluate_release(observation: ReleaseObservation) -> ReleaseDecision:
    """Decide one release verification from the observed facts (deterministic, T034).

    Checks run in the fixed order of the module docstring and the first
    triggered outcome wins; pure — the observation is neither mutated nor
    persisted, and identical facts yield an identical decision.
    """
    pre_smoke = pre_smoke_failure(observation)
    if pre_smoke is not None:
        return pre_smoke
    smoke = observation.smoke_outcome
    if smoke.status is SmokeStatus.NOT_RUN:
        return _failed(
            "smoke was not run: the released status requires a passing smoke check "
            "(FR-013, ADR-011 p.6)"
        )
    if smoke.status is SmokeStatus.FAILED:
        names = ", ".join(probe.name for probe in smoke.probes if not probe.passed)
        return _failed(f"smoke probes failed: {names} (ADR-011 p.6)")
    return ReleaseDecision(status=ReleaseStatus.RELEASED)


def _failed(reason: str) -> ReleaseDecision:
    """A failed decision with the fixed rollback signal (ADR-010, ADR-011 p.6)."""
    return ReleaseDecision(
        status=ReleaseStatus.RELEASE_FAILED,
        reason=reason,
        rollback_signal=ROLLBACK_SIGNAL,
    )


def _raw_or_unknown(raw: str | None) -> str:
    """Quoted raw value for diagnostics; ``unknown`` placeholder when absent."""
    if raw is None:
        return DIGEST_UNKNOWN
    stripped = raw.strip()
    return repr(stripped) if stripped else DIGEST_UNKNOWN
