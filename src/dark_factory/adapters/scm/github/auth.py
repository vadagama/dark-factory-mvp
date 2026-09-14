"""GitHub App authentication: short-lived installation tokens (T-024, ADR-019 p.3).

The flow per official recommendation: sign an RS256 JWT with the App private
key (``iss=app_id``, ``iat=now-60s``, ``exp=now+600s``), exchange it at
``POST /app/installations/{installation_id}/access_tokens`` for an
installation token that expires within an hour, cache it and refresh before
expiry. The client additionally invalidates the cache on a 401 so a revoked
token triggers exactly one refresh-and-retry.

The token source is injectable: tests stub ``fetcher`` (or use
``StaticTokenProvider``) and never need real keys; the contract-suite
emulator serves the exchange endpoint. The private key is a secret and is
never logged (ADR-009).
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Final, Protocol, runtime_checkable

import httpx2
import jwt

from dark_factory.adapters.scm.github.config import GitHubConfig

_JWT_TTL: Final[timedelta] = timedelta(seconds=600)
"""JWT lifetime; GitHub accepts at most 600 seconds."""

_JWT_BACKDATED_IAT: Final[timedelta] = timedelta(seconds=60)
"""Clock-skew guard GitHub recommends for the ``iat`` claim."""

_REFRESH_MARGIN: Final[timedelta] = timedelta(seconds=60)
"""Cached tokens are refreshed this long before their documented expiry."""


def _now() -> datetime:
    return datetime.now(tz=UTC)


@runtime_checkable
class TokenProvider(Protocol):
    """Source of the bearer token the GitHub client sends (ADR-019 p.3)."""

    async def token(self) -> str: ...

    async def invalidate(self) -> None: ...


class StaticTokenProvider(TokenProvider):
    """Fixed token source for tests and offline wiring; never refreshes."""

    def __init__(self, token: str) -> None:
        self._token = token

    async def token(self) -> str:
        return self._token

    async def invalidate(self) -> None:
        """No-op: a static token cannot be refreshed."""


class InstallationTokenFetcher:
    """Exchanges a signed App JWT for an installation token over HTTP.

    Standalone callable class so tests can stub the exchange with a plain
    async function of the same shape (``(jwt) -> (token, expires_at)``).
    """

    def __init__(
        self,
        config: GitHubConfig,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config
        self._transport = transport

    async def __call__(self, encoded_jwt: str) -> tuple[str, datetime]:
        async with httpx2.AsyncClient(
            base_url=self._config.api_base_url, transport=self._transport
        ) as client:
            response = await client.post(
                f"/app/installations/{self._config.installation_id}/access_tokens",
                headers={"Authorization": f"Bearer {encoded_jwt}"},
            )
        if response.status_code != 201:
            # Status only: response bodies never reach logs (ADR-009).
            raise GitHubAuthError(f"installation token exchange failed with {response.status_code}")
        data = response.json()
        return str(data["token"]), _parse_expires_at(str(data["expires_at"]))


class GitHubAuthError(RuntimeError):
    """Installation-token exchange or JWT signing failed."""


def _parse_expires_at(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _sign_jwt(config: GitHubConfig, now: datetime) -> str:
    """RS256 App JWT (``iss=app_id``, backdated ``iat``, 600s ``exp``)."""
    if not config.app_id or not config.private_key:
        raise GitHubAuthError(
            "GitHub App is not configured; set the DARK_FACTORY_GITHUB_APP_* variables"
        )
    payload = {
        "iss": config.app_id,
        "iat": int((now - _JWT_BACKDATED_IAT).timestamp()),
        "exp": int((now + _JWT_TTL).timestamp()),
    }
    return str(jwt.encode(payload, config.private_key, algorithm="RS256"))


class GitHubAppAuth(TokenProvider):
    """Installation-token provider: JWT exchange, cached to near expiry.

    ``fetcher`` replaces the HTTP exchange (tests); ``clock`` replaces the
    wall clock (deterministic expiry tests).
    """

    def __init__(
        self,
        config: GitHubConfig,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
        fetcher: Callable[[str], Awaitable[tuple[str, datetime]]] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._config = config
        self._fetcher = (
            fetcher
            if fetcher is not None
            else InstallationTokenFetcher(config, transport=transport)
        )
        self._clock: Callable[[], datetime] = clock if clock is not None else _now
        self._token: str | None = None
        self._expires_at = datetime.min.replace(tzinfo=UTC)

    async def token(self) -> str:
        """The cached installation token, refreshed at or before the margin."""
        now = self._clock()
        if self._token is not None and now < self._expires_at - _REFRESH_MARGIN:
            return self._token
        token, expires_at = await self._fetcher(_sign_jwt(self._config, now))
        self._token = token
        self._expires_at = expires_at
        return token

    async def invalidate(self) -> None:
        """Drop the cached token; the next ``token()`` fetches a fresh one."""
        self._token = None
        self._expires_at = datetime.min.replace(tzinfo=UTC)
