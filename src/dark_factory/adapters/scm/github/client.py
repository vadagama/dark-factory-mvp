"""HTTP layer of the GitHub adapter: httpx2 client, auth header, error mapping (T-024).

Every request carries the installation token from the ``TokenProvider``; a 401
invalidates the cache and retries exactly once, so a refreshed token heals the
request without surfacing the round trip. Responses are returned as-is for
callers that distinguish statuses (404-as-absent lookups); ``expect`` and
``get_json`` map unexpected statuses onto ``GitHubAPIError``. The transport is
injectable — the contract suite serves the API through ``httpx2.MockTransport``
(no network, deterministic).
"""

from typing import Any, Final

import httpx2

from dark_factory.adapters.scm.github.auth import TokenProvider
from dark_factory.ports import PortError

_API_VERSION: Final[str] = "2022-11-28"


class GitHubAPIError(PortError):
    """A GitHub REST call ended in an unexpected status."""


class GitHubClient:
    """Thin GitHub REST client shared by the adapter's ports."""

    def __init__(
        self,
        base_url: str,
        auth: TokenProvider,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self._auth = auth
        self._client = httpx2.AsyncClient(
            base_url=base_url,
            transport=transport,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": _API_VERSION,
                "User-Agent": "dark-factory",
            },
        )

    async def request(
        self,
        method: str,
        path: str,
        /,
        *,
        json: Any = None,
        params: dict[str, str] | None = None,
    ) -> httpx2.Response:
        """One authenticated request; a 401 refreshes the token and retries once."""
        response = await self._send(method, path, json=json, params=params)
        if response.status_code == 401:
            await self._auth.invalidate()
            response = await self._send(method, path, json=json, params=params)
        return response

    async def get_json(self, path: str, /, *, params: dict[str, str] | None = None) -> Any:
        """JSON body of a GET; raises ``GitHubAPIError`` on a non-200 status."""
        response = await self.request("GET", path, params=params)
        return self.expect(response, 200).json()

    async def check_runs(self, slug: str, ref: str, /) -> list[dict[str, Any]]:
        """Check runs reported on ``ref`` (Checks API); empty when the ref has none."""
        response = await self.request("GET", f"/repos/{slug}/commits/{ref}/check-runs")
        if response.status_code == 404:
            return []
        return list(self.expect(response, 200).json().get("check_runs", []))

    def expect(self, response: httpx2.Response, *status_codes: int) -> httpx2.Response:
        """The response when its status is expected; ``GitHubAPIError`` otherwise.

        Error details carry only method, path and status — bodies are dropped
        so nothing unexpected reaches logs (ADR-009).
        """
        if response.status_code not in status_codes:
            raise GitHubAPIError(
                f"GitHub {response.request.method} {response.request.url.path}"
                f" failed with {response.status_code}"
            )
        return response

    async def aclose(self) -> None:
        """Release the underlying connection pool."""
        await self._client.aclose()

    async def _send(
        self,
        method: str,
        path: str,
        /,
        *,
        json: Any = None,
        params: dict[str, str] | None = None,
    ) -> httpx2.Response:
        token = await self._auth.token()
        return await self._client.request(
            method,
            path,
            json=json,
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        )
