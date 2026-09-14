"""Unit tests of GitHub App authentication and the client transport (T-024).

Offline by construction: the token exchange runs against the contract-suite
emulator (or a stub fetcher), the JWT is signed with a throwaway RSA key, and
the clock is injected — no network, no real credentials.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from dark_factory.adapters.scm.github import (
    GitHubAPIError,
    GitHubAppAuth,
    GitHubAuthError,
    GitHubClient,
    GitHubConfig,
    StaticTokenProvider,
)
from tests.contract.github_api import (
    GITHUB_API_BASE_URL,
    GITHUB_INSTALLATION_TOKEN,
    GitHubApiEmulator,
)


class _Clock:
    """Injectable wall clock; tests move ``now`` forward explicitly."""

    def __init__(self) -> None:
        self.now = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


class _StubFetcher:
    """Token exchange stub: one fresh token per call, fixed lifetime."""

    def __init__(self, clock: _Clock, expires_in: timedelta) -> None:
        self.calls = 0
        self._clock = clock
        self._expires_in = expires_in

    async def __call__(self, encoded_jwt: str) -> tuple[str, datetime]:
        self.calls += 1
        return f"token-{self.calls}", self._clock.now + self._expires_in


def _app_config() -> GitHubConfig:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    return GitHubConfig(
        api_base_url=GITHUB_API_BASE_URL,
        app_id="123",
        installation_id="42",
        private_key=pem,
    )


def test_token_is_cached_until_the_refresh_margin() -> None:
    clock = _Clock()
    fetcher = _StubFetcher(clock, expires_in=timedelta(hours=1))
    auth = GitHubAppAuth(_app_config(), fetcher=fetcher, clock=clock)

    assert asyncio.run(auth.token()) == "token-1"
    clock.now += timedelta(minutes=30)
    assert asyncio.run(auth.token()) == "token-1"
    assert fetcher.calls == 1

    # Inside the 60s refresh margin: the next read fetches a fresh token.
    clock.now += timedelta(minutes=59, seconds=30)
    assert asyncio.run(auth.token()) == "token-2"
    assert fetcher.calls == 2


def test_invalidate_forces_a_refetch() -> None:
    clock = _Clock()
    fetcher = _StubFetcher(clock, expires_in=timedelta(hours=1))
    auth = GitHubAppAuth(_app_config(), fetcher=fetcher, clock=clock)

    asyncio.run(auth.token())
    asyncio.run(auth.invalidate())
    assert asyncio.run(auth.token()) == "token-2"
    assert fetcher.calls == 2


def test_unconfigured_app_fails_without_network() -> None:
    auth = GitHubAppAuth(GitHubConfig(app_id=None, private_key=None), clock=_Clock())
    with pytest.raises(GitHubAuthError):
        asyncio.run(auth.token())


def test_jwt_exchange_against_the_emulator() -> None:
    emulator = GitHubApiEmulator()
    clock = _Clock()
    auth = GitHubAppAuth(_app_config(), transport=emulator.transport(), clock=clock)

    assert asyncio.run(auth.token()) == GITHUB_INSTALLATION_TOKEN
    assert asyncio.run(auth.token()) == GITHUB_INSTALLATION_TOKEN
    assert emulator.installation_token_requests == 1
    assert emulator.last_installation_jwt is not None

    payload = jwt.decode(emulator.last_installation_jwt, options={"verify_signature": False})
    issued_at = datetime.fromtimestamp(payload["iat"], tz=UTC)
    expires_at = datetime.fromtimestamp(payload["exp"], tz=UTC)
    assert payload["iss"] == "123"
    assert issued_at <= clock.now < expires_at
    assert expires_at - issued_at == timedelta(seconds=660)


class _RotatingStub:
    """Token provider whose first token is stale; invalidate() issues the good one."""

    def __init__(self) -> None:
        self._token = "stale-token"
        self.invalidations = 0

    async def token(self) -> str:
        return self._token

    async def invalidate(self) -> None:
        self.invalidations += 1
        self._token = GITHUB_INSTALLATION_TOKEN


def test_client_retries_once_after_a_401() -> None:
    emulator = GitHubApiEmulator()
    provider = _RotatingStub()
    client = GitHubClient(GITHUB_API_BASE_URL, provider, transport=emulator.transport())

    data = asyncio.run(client.get_json("/repos/small/pilot/git/ref/heads/main"))

    assert data["object"]["sha"] == emulator.branches["main"]
    assert provider.invalidations == 1


def test_client_maps_unexpected_status_to_github_api_error() -> None:
    emulator = GitHubApiEmulator()
    client = GitHubClient(
        GITHUB_API_BASE_URL,
        StaticTokenProvider(GITHUB_INSTALLATION_TOKEN),
        transport=emulator.transport(),
    )

    with pytest.raises(GitHubAPIError):
        asyncio.run(client.get_json("/repos/unknown/repo/pulls"))


def test_static_token_provider_never_refreshes() -> None:
    provider = StaticTokenProvider("fixed")
    assert asyncio.run(provider.token()) == "fixed"
    asyncio.run(provider.invalidate())
    assert asyncio.run(provider.token()) == "fixed"
