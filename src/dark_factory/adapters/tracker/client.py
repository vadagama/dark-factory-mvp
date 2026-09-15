"""HTTP layer of the Plane tracker adapter: httpx2 client and error mapping (T-033).

Plane authenticates API calls with a static ``X-API-Key`` token of a dedicated
automation user, so — unlike the GitHub App flow — there is nothing to refresh
here. Responses are returned as-is for callers that read a status themselves
(404-as-absent lookups); ``get_json`` and ``expect`` map unexpected statuses
onto ``PlaneAPIError`` without echoing bodies, so a secret can never reach a
log through an error message (ADR-009). The transport is injectable: the
contract suite serves the API through ``httpx2.MockTransport``.
"""

from typing import Any, Final

import httpx2

from dark_factory.ports import PortError

DEFAULT_TIMEOUT_SECONDS: Final[float] = 10.0
"""Per-request timeout; a hung tracker must not stall a stage (FR-020)."""


class PlaneAPIError(PortError):
    """A Plane REST call ended in an unexpected status or failed in transport."""


class PlaneClient:
    """Thin Plane REST client shared by the tracker adapter."""

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str,
        transport: httpx2.AsyncBaseTransport | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._api_key = api_key
        self._client = httpx2.AsyncClient(
            base_url=base_url,
            transport=transport,
            timeout=timeout_seconds,
            headers={"Accept": "application/json", "User-Agent": "dark-factory"},
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
        """One authenticated request; the response is returned unchecked."""
        return await self._client.request(
            method,
            path,
            json=json,
            params=params,
            headers={"X-API-Key": self._api_key},
        )

    async def get_json(self, path: str, /, *, params: dict[str, str] | None = None) -> Any:
        """JSON body of a GET; raises ``PlaneAPIError`` on a non-200 status."""
        response = await self.request("GET", path, params=params)
        return self.expect(response, 200).json()

    def expect(self, response: httpx2.Response, *status_codes: int) -> httpx2.Response:
        """The response when its status is expected; ``PlaneAPIError`` otherwise.

        Error details carry only method, path and status — the body is dropped
        so nothing unexpected reaches logs (ADR-009).
        """
        if response.status_code not in status_codes:
            raise PlaneAPIError(
                f"Plane {response.request.method} {response.request.url.path}"
                f" failed with {response.status_code}"
            )
        return response

    async def aclose(self) -> None:
        """Release the underlying connection pool."""
        await self._client.aclose()
