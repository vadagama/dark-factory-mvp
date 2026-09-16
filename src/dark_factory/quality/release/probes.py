"""Smoke probes of one release verification (T034, FR-013, ADR-011 p.6).

The probe set is the I/O seam of the release check: network calls live only
behind the :class:`SmokeProbe` protocol, the decision core
(``quality.release.decision``) consumes probe results as data, and every
probe is an injectable object — the whole verification is testable without
network (the same seam discipline as ``ReconcileObserver`` in
``orchestration.reconcile``).

MVP probe set (the minimum that binds "deployed and alive" to the digest):

- :class:`HttpHealthProbe` — GET the target URL, pass on any 2xx response;
- :class:`HttpDigestProbe` — GET a version endpoint and require the expected
  immutable digest to appear in a response header or the body (binds the
  running workload to the promoted digest, FR-011/T033).

Timeouts, attempts and retry delays are fixed constants in code (MVP policy,
not configuration): a probe that cannot complete within them fails the
release — fail-closed. Probe diagnostics never echo the probe URL, response
bodies or raw exception text (ADR-009): statuses and exception class names
only, like the GitHub adapter's client.
"""

import asyncio
from collections.abc import Sequence
from typing import Final, Protocol, runtime_checkable

import httpx2

from dark_factory.changes.run_records import SmokeProbeEvidence
from dark_factory.quality.release.decision import SmokeOutcome

SMOKE_PROBE_ATTEMPTS: Final[int] = 3
"""Fixed number of attempts per probe; the last failure is the probe outcome."""

SMOKE_PROBE_TIMEOUT_SECONDS: Final[float] = 5.0
"""Fixed per-request timeout of one probe attempt."""

SMOKE_PROBE_RETRY_DELAY_SECONDS: Final[float] = 1.0
"""Fixed delay between probe attempts (retries target transient unavailability,
e.g. the pod still settling right after the Argo sync)."""


def _validated_attempts(attempts: int) -> int:
    """The probe attempt count, validated to leave at least one attempt.

    A probe reports the last recorded failure as its outcome, so the retry loop
    needs at least one attempt to run; :data:`SMOKE_PROBE_ATTEMPTS` satisfies
    that by construction, but the count is injectable, hence the check.

    Raises:
        ValueError: If ``attempts`` is below 1.
    """
    if attempts < 1:
        raise ValueError(f"attempts must be >= 1, got {attempts}")
    return attempts


@runtime_checkable
class SmokeProbe(Protocol):
    """One smoke probe against the deployed release (I/O seam, T034).

    ``name`` identifies the probe in results and diagnostics; ``run`` executes
    the probe once (retries are the implementation's concern) and returns the
    result as data — it must never raise for an *outcome* failure, only for a
    broken probe, which the runner turns into a failed result.
    """

    @property
    def name(self) -> str: ...

    async def run(self) -> SmokeProbeEvidence: ...


async def run_smoke_probes(probes: Sequence[SmokeProbe]) -> SmokeOutcome:
    """Run the probe sequence and collect the results (T034).

    A probe that raises is recorded as a failed result with the exception
    class name (no raw text, ADR-009) and never aborts the sequence: one
    broken probe must not mask the outcomes of the others — the release
    decision sees every probe result as data. No probes → ``NOT_RUN``
    (fail-closed upstream, FR-013).
    """
    results: list[SmokeProbeEvidence] = []
    for probe in probes:
        try:
            results.append(await probe.run())
        except Exception as exc:
            # A broken probe is data, not a crash (BLE is not in the selected set).
            results.append(
                SmokeProbeEvidence(
                    name=probe.name,
                    passed=False,
                    detail=f"probe error: {type(exc).__name__}",
                )
            )
    return SmokeOutcome(probes=tuple(results))


class HttpHealthProbe:
    """GET ``url`` and pass on any 2xx response (T034, FR-013).

    Non-2xx statuses and transport errors are retried up to
    ``SMOKE_PROBE_ATTEMPTS`` with the fixed delay; the last failure becomes
    the probe ``detail``. The URL is never part of any detail (ADR-009).
    """

    def __init__(
        self,
        url: str,
        *,
        name: str = "http-health",
        transport: httpx2.AsyncBaseTransport | None = None,
        attempts: int = SMOKE_PROBE_ATTEMPTS,
        timeout_seconds: float = SMOKE_PROBE_TIMEOUT_SECONDS,
        retry_delay_seconds: float = SMOKE_PROBE_RETRY_DELAY_SECONDS,
    ) -> None:
        self._url = url
        self._name = name
        self._transport = transport
        self._attempts = _validated_attempts(attempts)
        self._timeout_seconds = timeout_seconds
        self._retry_delay_seconds = retry_delay_seconds

    @property
    def name(self) -> str:
        """Probe name used in results and diagnostics."""
        return self._name

    async def run(self) -> SmokeProbeEvidence:
        """Execute the probe: GET with fixed attempts/timeout; 2xx passes."""
        async with httpx2.AsyncClient(
            timeout=self._timeout_seconds, transport=self._transport
        ) as client:
            last_error: str | None = None
            for attempt in range(1, self._attempts + 1):
                try:
                    response = await client.get(self._url)
                except httpx2.HTTPError as exc:
                    # Exception text may embed the URL/host — class name only (ADR-009).
                    last_error = f"attempt {attempt}: {type(exc).__name__}"
                else:
                    if 200 <= response.status_code < 300:
                        return SmokeProbeEvidence(
                            name=self._name,
                            passed=True,
                            detail=f"HTTP {response.status_code}",
                        )
                    last_error = f"attempt {attempt}: HTTP {response.status_code}"
                if attempt < self._attempts:
                    await asyncio.sleep(self._retry_delay_seconds)
        # attempts >= 1 is enforced in __init__, so a failure was always
        # recorded; the guard is explicit (an assert vanishes under python -O).
        if last_error is None:
            raise RuntimeError("smoke probe ran no attempt: attempts must be >= 1")
        return SmokeProbeEvidence(name=self._name, passed=False, detail=last_error)


class HttpDigestProbe:
    """GET a version endpoint and require the expected digest in the response (T034).

    The probe binds the running workload to the promoted immutable digest
    (FR-011, T033): with ``header`` set, that response header must contain
    the digest string; otherwise the response body must. A non-2xx response
    is retried like :class:`HttpHealthProbe` — the digest is only judged on a
    successful response.
    """

    def __init__(
        self,
        url: str,
        digest: str,
        *,
        header: str | None = None,
        name: str = "http-digest",
        transport: httpx2.AsyncBaseTransport | None = None,
        attempts: int = SMOKE_PROBE_ATTEMPTS,
        timeout_seconds: float = SMOKE_PROBE_TIMEOUT_SECONDS,
        retry_delay_seconds: float = SMOKE_PROBE_RETRY_DELAY_SECONDS,
    ) -> None:
        self._url = url
        self._digest = digest
        self._header = header
        self._name = name
        self._transport = transport
        self._attempts = _validated_attempts(attempts)
        self._timeout_seconds = timeout_seconds
        self._retry_delay_seconds = retry_delay_seconds

    @property
    def name(self) -> str:
        """Probe name used in results and diagnostics."""
        return self._name

    async def run(self) -> SmokeProbeEvidence:
        """Execute the probe: GET, then the digest check on a 2xx response."""
        async with httpx2.AsyncClient(
            timeout=self._timeout_seconds, transport=self._transport
        ) as client:
            last_error: str | None = None
            for attempt in range(1, self._attempts + 1):
                try:
                    response = await client.get(self._url)
                except httpx2.HTTPError as exc:
                    last_error = f"attempt {attempt}: {type(exc).__name__}"
                else:
                    if not 200 <= response.status_code < 300:
                        last_error = f"attempt {attempt}: HTTP {response.status_code}"
                    elif self._digest_found(response):
                        return SmokeProbeEvidence(
                            name=self._name, passed=True, detail=self._found_detail()
                        )
                    else:
                        return SmokeProbeEvidence(
                            name=self._name, passed=False, detail=self._missing_detail()
                        )
                if attempt < self._attempts:
                    await asyncio.sleep(self._retry_delay_seconds)
        # attempts >= 1 is enforced in __init__, so a failure was always
        # recorded; the guard is explicit (an assert vanishes under python -O).
        if last_error is None:
            raise RuntimeError("smoke probe ran no attempt: attempts must be >= 1")
        return SmokeProbeEvidence(name=self._name, passed=False, detail=last_error)

    def _digest_found(self, response: httpx2.Response) -> bool:
        """Whether the expected digest appears in the target header or the body."""
        if self._header is None:
            return self._digest in response.text
        return self._digest in response.headers.get(self._header, "")

    def _found_detail(self) -> str:
        """Deterministic pass detail (header names are safe to echo)."""
        if self._header is None:
            return "digest found in the response body"
        return f"digest found in response header {self._header!r}"

    def _missing_detail(self) -> str:
        """Deterministic failure detail (the body itself is never echoed, ADR-009)."""
        if self._header is None:
            return "digest not found in the response body"
        return f"digest not found in response header {self._header!r}"
