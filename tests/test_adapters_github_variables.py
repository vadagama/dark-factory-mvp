"""GitHub repository-variable adapter behind the CI stage toggles (T059, ADR-027).

The adapter is exercised over ``httpx2.MockTransport`` — no network, no real
credentials — with the bearer rule the contract emulator also enforces: every
API route must carry the installation token, so an unauthenticated call cannot
silently look like an empty repository.
"""

import asyncio
import json
from collections.abc import Mapping
from urllib.parse import unquote

import httpx2
import pytest

from dark_factory.adapters.scm.github import (
    GitHubCiStageToggles,
    GitHubClient,
    StaticTokenProvider,
)
from dark_factory.ports import PortError

SLUG = "small/pilot"
TOKEN = "gh-test-token"
BASE_URL = "https://api.github.test"
VARIABLES_PATH = f"/repos/{SLUG}/actions/variables"


class _VariablesAPI:
    """In-memory GitHub variables endpoint over ``MockTransport`` (bearer-enforcing)."""

    def __init__(self, values: Mapping[str, str] | None = None) -> None:
        """Start from ``values``; ``fail_status`` makes every API route fail."""
        self.values: dict[str, str] = dict(values or {})
        self.requests: list[httpx2.Request] = []
        self.fail_status: int | None = None

    @property
    def calls(self) -> list[tuple[str, str]]:
        """Recorded ``(method, path)`` pairs, in order."""
        return [(request.method, request.url.path) for request in self.requests]

    def toggles(self, token: str = TOKEN) -> GitHubCiStageToggles:
        """Adapter over this emulator; ``token`` proves the bearer is really sent."""
        client = GitHubClient(
            BASE_URL,
            StaticTokenProvider(token),
            transport=httpx2.MockTransport(self._handle),
        )
        return GitHubCiStageToggles(client, slug=SLUG)

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if request.headers.get("Authorization") != f"Bearer {TOKEN}":
            return httpx2.Response(status_code=401, json={"message": "Bad credentials"})
        if self.fail_status is not None:
            return httpx2.Response(status_code=self.fail_status, json={"message": "nope"})
        path = request.url.path
        if path == VARIABLES_PATH:
            if request.method == "GET":
                return self._list(request)
            if request.method == "POST":
                payload = json.loads(request.content)
                self.values[str(payload["name"])] = str(payload["value"])
                return httpx2.Response(status_code=201, json={})
        if path.startswith(f"{VARIABLES_PATH}/"):
            return self._variable_route(request, unquote(path.removeprefix(f"{VARIABLES_PATH}/")))
        return httpx2.Response(status_code=404, json={"message": "Not Found"})

    def _variable_route(self, request: httpx2.Request, name: str) -> httpx2.Response:
        if name not in self.values:
            return httpx2.Response(status_code=404, json={"message": "Not Found"})
        if request.method == "PATCH":
            payload = json.loads(request.content)
            self.values[name] = str(payload["value"])
            return httpx2.Response(status_code=204)
        if request.method == "DELETE":
            del self.values[name]
            return httpx2.Response(status_code=204)
        return httpx2.Response(status_code=404, json={"message": "Not Found"})

    def _list(self, request: httpx2.Request) -> httpx2.Response:
        page = int(request.url.params.get("page", "1"))
        per_page = int(request.url.params.get("per_page", "10"))
        names = sorted(self.values)
        window = names[(page - 1) * per_page : page * per_page]
        return httpx2.Response(
            status_code=200,
            json={
                "total_count": len(names),
                "variables": [{"name": name, "value": self.values[name]} for name in window],
            },
        )


# --- Reading -----------------------------------------------------------------


def test_values_returns_every_repository_variable() -> None:
    api = _VariablesAPI({"CI_SKIP_LINT": "true", "OTHER": "x"})
    assert asyncio.run(api.toggles().values()) == {"CI_SKIP_LINT": "true", "OTHER": "x"}
    assert api.calls == [("GET", VARIABLES_PATH)]


def test_values_pages_until_the_listing_stops_growing() -> None:
    """A silent first-page cap would report a switched-off stage as enabled."""
    api = _VariablesAPI({f"CI_SKIP_{index:03d}": "true" for index in range(150)})
    values = asyncio.run(api.toggles().values())
    assert len(values) == 150
    assert api.calls == [("GET", VARIABLES_PATH), ("GET", VARIABLES_PATH)]
    assert [request.url.params.get("page") for request in api.requests] == ["1", "2"]
    assert all(request.url.params.get("per_page") == "100" for request in api.requests)


def test_values_without_the_installation_token_fails() -> None:
    """An unauthenticated read must fail, never look like 'no variables set'."""
    api = _VariablesAPI({"CI_SKIP_LINT": "true"})
    with pytest.raises(PortError) as failure:
        asyncio.run(api.toggles(token="wrong-token").values())
    assert "401" in str(failure.value)
    assert TOKEN not in str(failure.value), "errors never echo credentials (ADR-009)"


# --- Writing -----------------------------------------------------------------


def test_set_value_updates_an_existing_variable() -> None:
    api = _VariablesAPI({"CI_SKIP_LINT": "false"})
    asyncio.run(api.toggles().set_value("CI_SKIP_LINT", "true"))
    assert api.calls == [("PATCH", f"{VARIABLES_PATH}/CI_SKIP_LINT")]
    assert api.values == {"CI_SKIP_LINT": "true"}
    assert json.loads(api.requests[0].content) == {"name": "CI_SKIP_LINT", "value": "true"}


def test_set_value_creates_a_variable_the_provider_does_not_know() -> None:
    """GitHub has no upsert: the 404 of the PATCH is answered by a POST."""
    api = _VariablesAPI()
    asyncio.run(api.toggles().set_value("CI_SKIP_LINT", "true"))
    assert api.calls == [
        ("PATCH", f"{VARIABLES_PATH}/CI_SKIP_LINT"),
        ("POST", VARIABLES_PATH),
    ]
    assert api.values == {"CI_SKIP_LINT": "true"}
    assert json.loads(api.requests[1].content) == {"name": "CI_SKIP_LINT", "value": "true"}


def test_set_value_none_deletes_the_variable() -> None:
    api = _VariablesAPI({"CI_SKIP_LINT": "true"})
    asyncio.run(api.toggles().set_value("CI_SKIP_LINT", None))
    assert api.calls == [("DELETE", f"{VARIABLES_PATH}/CI_SKIP_LINT")]
    assert api.values == {}


def test_set_value_none_tolerates_an_absent_variable() -> None:
    """Deleting what is already absent is the wanted state, not an error (idempotent)."""
    api = _VariablesAPI()
    asyncio.run(api.toggles().set_value("CI_SKIP_LINT", None))
    assert api.calls == [("DELETE", f"{VARIABLES_PATH}/CI_SKIP_LINT")]
    assert api.values == {}


@pytest.mark.parametrize("fail_status", [403, 404, 422, 500])
def test_provider_failure_becomes_a_port_error(fail_status: int) -> None:
    api = _VariablesAPI()
    api.fail_status = fail_status
    toggles = api.toggles()
    with pytest.raises(PortError) as read_failure:
        asyncio.run(toggles.values())
    assert str(fail_status) in str(read_failure.value)
    api.requests.clear()
    with pytest.raises(PortError) as write_failure:
        asyncio.run(toggles.set_value("CI_SKIP_LINT", "true"))
    assert str(fail_status) in str(write_failure.value), "a failed write is never silent"
