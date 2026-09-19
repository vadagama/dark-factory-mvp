"""Value-level release facts of ``factory run advance`` (T-092 S4, ADR-024 §7 S4).

A release stage parked in ``waiting`` — the durable external-wait checkpoint —
is resumed by the driver
(:func:`dark_factory.orchestration.runner.advance_run`) once the release facts
it waits for resolve. The driver stays port-free (ADR-024 p.5): it consumes
only the value-level
:class:`~dark_factory.quality.release.decision.ReleaseObservation` (the
release core's own observation, the one ``factory release verify`` decides
on), and this module is the CLI seam that builds that observation from the command's
release options — the digest observed on the deployment, the raw Argo
Application statuses, the smoke probes behind the ``quality.release`` seams.

The provider is value-level because the MVP has no live Argo/Kubernetes client
(the same honest gap as ``factory release verify``, T034): the caller — the CI
job or the operator script — reads the deployment state and passes it in as
flags; a live reader is a later task behind the same seams.

Check discipline (FR-011, FR-013): the digest-immutability and Argo checks are
evaluated **before** any probe fires (:func:`pre_smoke_failure`), so a wrong
digest never sends smoke requests to a deployment that is not the promoted
one; the probes run only when the pre-smoke checks pass and smoke options are
configured. Partial observations never resolve the wait (fail-closed,
``stages.release.release_resolved``) — the checkpoint stays parked and the
checkpoint replay is the answer until the full facts are observed.

Like ``runtime.facts.ScmFactsProvider`` the provider is synchronous (the
driver is); the probe I/O is driven through a private loop. The observation is
read-only, and its failures are the caller's: a broken probe is data (one
failed ``SmokeProbeEvidence``), never an exception into the driver.
"""

import asyncio
from dataclasses import replace

from dark_factory.changes.enums import Stage
from dark_factory.changes.run import Change, ChangeRun
from dark_factory.cli.main import RunAdvanceArgs
from dark_factory.quality.release import (
    HttpDigestProbe,
    HttpHealthProbe,
    ReleaseObservation,
    SmokeProbe,
    pre_smoke_failure,
    run_smoke_probes,
)


class CliReleaseFactsProvider:
    """The release facts of one waiting release attempt, from the command's options.

    The values are the ``run advance`` release flags: ``observed_digest``,
    ``argo_sync`` and ``argo_health`` are the deployment facts,
    ``expected_digest`` the promoted digest (the XOR of
    ``--expected-digest``/``--digest-json``, resolved by the command — optional
    here, the decision core fails closed when absent), ``application`` the Argo
    Application bookkeeping passed through to the evidence. The smoke options
    build the MVP probe set: the health probe plus, when configured, the digest
    probe binding the workload to the expected digest (FR-011/T033).
    """

    def __init__(
        self,
        *,
        expected_digest: str | None,
        observed_digest: str | None,
        argo_sync: str | None,
        argo_health: str | None,
        smoke_url: str | None = None,
        smoke_digest_url: str | None = None,
        smoke_digest_header: str | None = None,
        application: str | None = None,
    ) -> None:
        self._expected_digest = expected_digest
        self._observed_digest = observed_digest
        self._argo_sync = argo_sync
        self._argo_health = argo_health
        self._smoke_url = smoke_url
        self._smoke_digest_url = smoke_digest_url
        self._smoke_digest_header = smoke_digest_header
        self._application = application

    @classmethod
    def from_options(cls, args: RunAdvanceArgs) -> "CliReleaseFactsProvider | None":
        """The provider over ``RunAdvanceArgs``' release options, or ``None``.

        ``None`` when no observation option is set at all: without observed
        facts the provider could only return non-resolving observations, and
        the command then behaves exactly as before S4 — the checkpoint replays.
        The expected digest alone does not build the provider: it feeds the
        promotion (the executor), not the observation.
        """
        if (
            args.observed_digest is None
            and args.argo_sync is None
            and args.argo_health is None
            and args.smoke_url is None
            and args.smoke_digest_url is None
            and args.smoke_digest_header is None
            and args.application is None
        ):
            return None
        return cls(
            expected_digest=args.expected_digest,
            observed_digest=args.observed_digest,
            argo_sync=args.argo_sync,
            argo_health=args.argo_health,
            smoke_url=args.smoke_url,
            smoke_digest_url=args.smoke_digest_url,
            smoke_digest_header=args.smoke_digest_header,
            application=args.application,
        )

    def __call__(self, run: ChangeRun, stage: Stage, change: Change) -> ReleaseObservation | None:
        """Observe the release facts of the waiting release attempt (synchronous).

        ``run`` and ``stage`` are part of the driver's protocol and are not
        consulted: the facts are the command's release options alone. The
        probes fire only when the pre-smoke checks pass (FR-011) and smoke
        options are configured; otherwise the observation carries no smoke
        evidence and the decision core reports the pre-smoke failure or the
        ``not_run`` smoke — fail-closed either way.
        """
        observation = ReleaseObservation(
            expected_digest=self._expected_digest,
            observed_digest=self._observed_digest,
            argo_sync_raw=self._argo_sync,
            argo_health_raw=self._argo_health,
            application=self._application,
        )
        if pre_smoke_failure(observation) is None:
            probes = self._probes()
            if probes:
                smoke = asyncio.run(run_smoke_probes(probes)).probes
                observation = replace(observation, smoke=smoke)
        return observation

    def _probes(self) -> list[SmokeProbe]:
        """MVP probe set from the smoke options; empty means no smoke at all."""
        probes: list[SmokeProbe] = []
        if self._smoke_url is not None:
            probes.append(HttpHealthProbe(self._smoke_url))
            if self._smoke_digest_url is not None:
                probes.append(
                    HttpDigestProbe(
                        self._smoke_digest_url,
                        self._expected_digest or "",
                        header=self._smoke_digest_header,
                    )
                )
        return probes
