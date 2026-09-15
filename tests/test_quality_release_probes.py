"""Smoke-probe tests of the release verification (T034, FR-013, ADR-009).

Every probe runs against an injected ``httpx2`` transport (MockTransport) —
no network. The runner semantics (a broken probe is data, never an abort)
and the ADR-009 hygiene (URLs and response bodies never leak into
diagnostics) are covered here.
"""

import asyncio
import json

import httpx2

from dark_factory.changes.run_records import SmokeProbeEvidence
from dark_factory.quality.release import (
    HttpDigestProbe,
    HttpHealthProbe,
    SmokeProbe,
    run_smoke_probes,
)

URL = "http://target.internal:8080/healthz"


def _transport(
    responses: list[httpx2.Response],
    calls: list[httpx2.Request] | None = None,
) -> httpx2.MockTransport:
    """Transport replaying the given responses once per request, in order."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        if calls is not None:
            calls.append(request)
        return responses.pop(0) if len(responses) > 1 else responses[0]

    return httpx2.MockTransport(handler)


def _run(probe: SmokeProbe) -> SmokeProbeEvidence:
    return asyncio.run(probe.run())


class TestRunSmokeProbes:
    def test_results_are_collected_in_order(self) -> None:
        class _Ok(SmokeProbe):
            @property
            def name(self) -> str:
                return "first"

            async def run(self) -> SmokeProbeEvidence:
                return SmokeProbeEvidence(name="first", passed=True, detail="ok")

        outcome = asyncio.run(run_smoke_probes([_Ok()]))

        assert [probe.name for probe in outcome.probes] == ["first"]
        assert outcome.probes[0].passed is True

    def test_a_broken_probe_becomes_a_failed_result_without_aborting_the_rest(self) -> None:
        class _Broken(SmokeProbe):
            @property
            def name(self) -> str:
                return "broken"

            async def run(self) -> SmokeProbeEvidence:
                raise RuntimeError("secret internals")

        class _Ok(SmokeProbe):
            @property
            def name(self) -> str:
                return "after"

            async def run(self) -> SmokeProbeEvidence:
                return SmokeProbeEvidence(name="after", passed=True, detail="ok")

        outcome = asyncio.run(run_smoke_probes([_Broken(), _Ok()]))

        broken, after = outcome.probes
        assert broken.passed is False
        assert broken.detail == "probe error: RuntimeError"
        assert "secret internals" not in (broken.detail or "")
        assert after.passed is True

    def test_no_probes_yield_an_empty_outcome(self) -> None:
        outcome = asyncio.run(run_smoke_probes([]))

        assert outcome.probes == ()


class TestHttpHealthProbe:
    def test_any_2xx_response_passes(self) -> None:
        for status in (200, 201, 204, 299):
            probe = HttpHealthProbe(URL, transport=_transport([httpx2.Response(status)]))
            result = _run(probe)
            assert result.passed is True, status
            assert result.detail == f"HTTP {status}"

    def test_server_error_is_retried_up_to_the_fixed_attempts(self) -> None:
        calls: list[httpx2.Request] = []
        transport = _transport([httpx2.Response(500)], calls)
        probe = HttpHealthProbe(URL, transport=transport, retry_delay_seconds=0.0, attempts=3)

        result = _run(probe)

        assert result.passed is False
        assert len(calls) == 3
        assert result.detail == "attempt 3: HTTP 500"

    def test_last_attempt_wins_over_earlier_failures(self) -> None:
        transport = _transport([httpx2.Response(500), httpx2.Response(503), httpx2.Response(200)])
        probe = HttpHealthProbe(URL, transport=transport, retry_delay_seconds=0.0)

        result = _run(probe)

        assert result.passed is True
        assert result.detail == "HTTP 200"

    def test_transport_error_is_retried_and_never_leaks_its_text(self) -> None:
        def handler(request: httpx2.Request) -> httpx2.Response:
            raise httpx2.ConnectError(f"refused to connect to {request.url}")

        probe = HttpHealthProbe(
            URL, transport=httpx2.MockTransport(handler), retry_delay_seconds=0.0
        )

        result = _run(probe)

        assert result.passed is False
        assert result.detail == "attempt 3: ConnectError"
        assert URL not in (result.detail or "")
        assert "refused" not in (result.detail or "")

    def test_the_url_is_never_part_of_the_detail(self) -> None:
        secret_url = "http://user:token@target.internal/healthz?api_key=very-secret"
        probe = HttpHealthProbe(
            secret_url, transport=_transport([httpx2.Response(502)]), retry_delay_seconds=0.0
        )

        result = _run(probe)

        assert "very-secret" not in (result.detail or "")
        assert "user:token" not in (result.detail or "")

    def test_the_probe_satisfies_the_protocol(self) -> None:
        probe = HttpHealthProbe(URL, transport=_transport([httpx2.Response(200)]))

        assert isinstance(probe, SmokeProbe)
        assert probe.name == "http-health"


class TestHttpDigestProbe:
    def test_digest_in_the_body_passes(self) -> None:
        digest = "sha256:" + "a" * 64
        body = json.dumps({"image": {"digest": digest}})
        probe = HttpDigestProbe(
            URL,
            digest,
            transport=_transport([httpx2.Response(200, text=body)]),
            retry_delay_seconds=0.0,
        )

        result = _run(probe)

        assert result.passed is True
        assert result.detail == "digest found in the response body"

    def test_digest_in_a_response_header_passes(self) -> None:
        digest = "sha256:" + "b" * 64
        probe = HttpDigestProbe(
            URL,
            digest,
            header="X-Image-Digest",
            transport=_transport([httpx2.Response(200, headers={"X-Image-Digest": digest})]),
            retry_delay_seconds=0.0,
        )

        result = _run(probe)

        assert result.passed is True
        assert result.detail == "digest found in response header 'X-Image-Digest'"

    def test_missing_digest_in_the_body_fails_without_echoing_the_body(self) -> None:
        probe = HttpDigestProbe(
            URL,
            "sha256:expected",
            transport=_transport([httpx2.Response(200, text="version=1.2.3 secret=abc")]),
            retry_delay_seconds=0.0,
        )

        result = _run(probe)

        assert result.passed is False
        assert result.detail == "digest not found in the response body"
        assert "secret=abc" not in (result.detail or "")

    def test_missing_digest_header_fails(self) -> None:
        probe = HttpDigestProbe(
            URL,
            "sha256:expected",
            header="X-Image-Digest",
            transport=_transport([httpx2.Response(200)]),
            retry_delay_seconds=0.0,
        )

        result = _run(probe)

        assert result.passed is False
        assert result.detail == "digest not found in response header 'X-Image-Digest'"

    def test_non_2xx_is_retried_before_the_digest_is_judged(self) -> None:
        calls: list[httpx2.Request] = []
        digest = "sha256:" + "c" * 64
        transport = _transport([httpx2.Response(503), httpx2.Response(503)], calls)
        probe = HttpDigestProbe(
            URL,
            digest,
            transport=transport,
            retry_delay_seconds=0.0,
        )

        result = _run(probe)

        assert result.passed is False
        assert len(calls) == 3  # 503 replayed on every attempt
        assert result.detail == "attempt 3: HTTP 503"

    def test_the_probe_satisfies_the_protocol(self) -> None:
        probe = HttpDigestProbe(
            URL,
            "sha256:expected",
            transport=_transport([httpx2.Response(200, text="sha256:expected")]),
        )

        assert isinstance(probe, SmokeProbe)
        assert probe.name == "http-digest"


class TestProbeConstantsAreCodeFixed:
    def test_mvp_policy_is_fixed_in_code(self) -> None:
        from dark_factory.quality.release import (
            SMOKE_PROBE_ATTEMPTS,
            SMOKE_PROBE_RETRY_DELAY_SECONDS,
            SMOKE_PROBE_TIMEOUT_SECONDS,
        )

        assert SMOKE_PROBE_ATTEMPTS == 3
        assert SMOKE_PROBE_TIMEOUT_SECONDS == 5.0
        assert SMOKE_PROBE_RETRY_DELAY_SECONDS == 1.0

    def test_injected_values_override_the_defaults(self) -> None:
        calls: list[httpx2.Request] = []
        probe = HttpHealthProbe(
            URL,
            transport=_transport([httpx2.Response(500)], calls),
            attempts=2,
            timeout_seconds=0.1,
            retry_delay_seconds=0.0,
        )

        result = _run(probe)

        assert len(calls) == 2
        assert result.detail == "attempt 2: HTTP 500"
