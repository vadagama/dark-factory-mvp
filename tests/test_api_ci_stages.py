"""CI stage toggle endpoints of the factory API (T059, ADR-026/ADR-027).

The API is the only writer of the stage toggles: the console calls it, it
resolves every request through the catalog and then touches one repository
variable. These tests pin that contract without a database and without GitHub —
the session factory never connects (no route here touches the store) and the
toggle port is an in-memory fake.
"""

from collections.abc import Mapping
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from dark_factory.api.app import create_app
from dark_factory.api.auth import SCOPE_CI_WRITE, ApiToken, ApiTokenStore
from dark_factory.ci import CI_STAGES, SKIP_VALUE
from dark_factory.ports import CiStageTogglePort, PortError

API = "/api/v1"
REPOSITORY = "small/pilot"
OP_TOKEN = "op-token"
OTHER_SCOPE_TOKEN = "other-scope-token"
SERVICE_TOKEN = "service-token"

_STAGE_FIELDS = {
    "job",
    "title",
    "group",
    "summary",
    "local_command",
    "weight",
    "variable",
    "enabled",
}


class FakeToggles(CiStageTogglePort):
    """In-memory toggle store: records writes and can fail on demand."""

    def __init__(self, values: Mapping[str, str] | None = None) -> None:
        """Start from ``values`` (variable name → stored value)."""
        self.stored: dict[str, str] = dict(values or {})
        self.writes: list[tuple[str, str | None]] = []
        self.failure: PortError | None = None

    async def values(self) -> Mapping[str, str]:
        """Every stored value, or the configured failure."""
        self._raise_if_failing()
        return dict(self.stored)

    async def set_value(self, variable: str, value: str | None) -> None:
        """Record and apply one write (``None`` deletes)."""
        self._raise_if_failing()
        self.writes.append((variable, value))
        if value is None:
            self.stored.pop(variable, None)
        else:
            self.stored[variable] = value

    def _raise_if_failing(self) -> None:
        if self.failure is not None:
            raise self.failure


def _store() -> ApiTokenStore:
    """Token store with the three identities the authorization tests need."""
    return ApiTokenStore(
        [
            (
                OP_TOKEN,
                ApiToken(actor="alice", role="operator", scopes=frozenset({SCOPE_CI_WRITE})),
            ),
            (
                OTHER_SCOPE_TOKEN,
                ApiToken(actor="bob", role="operator", scopes=frozenset({"changes:write"})),
            ),
            (
                SERVICE_TOKEN,
                ApiToken(actor="bot", role="service", scopes=frozenset({SCOPE_CI_WRITE})),
            ),
        ]
    )


def _client(toggles: CiStageTogglePort | None = None) -> TestClient:
    """API client over a never-connecting session factory and the given toggles."""
    factory = sessionmaker(expire_on_commit=False)
    return TestClient(
        create_app(factory, tokens=_store(), ci_toggles=toggles, ci_repository=REPOSITORY)
    )


def _auth(token: str = OP_TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _assert_error_body(payload: dict[str, Any], status: int, title: str) -> None:
    assert payload["type"] == "about:blank"
    assert payload["title"] == title
    assert payload["status"] == status
    assert isinstance(payload["detail"], str) and payload["detail"]


# --- Reading the catalog -----------------------------------------------------


def test_get_serves_the_catalog_with_state() -> None:
    response = _client(FakeToggles()).get(f"{API}/ci/stages")
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == 1
    assert body["available"] is True
    assert body["reason"] is None
    assert body["repository"] == REPOSITORY
    assert [stage["job"] for stage in body["stages"]] == [stage.job for stage in CI_STAGES]
    for stage in body["stages"]:
        assert set(stage) == _STAGE_FIELDS, stage["job"]
        assert stage["enabled"] is True, "no variables set means every stage runs"


def test_state_maps_skip_values_to_disabled() -> None:
    toggles = FakeToggles(
        {
            "CI_SKIP_LINT": "true",
            "CI_SKIP_TYPECHECK": "TRUE",
            "CI_SKIP_TEST": "false",
            "CI_SKIP_BUILD": "ofl",
            "UNRELATED_VARIABLE": "true",
        }
    )
    body = _client(toggles).get(f"{API}/ci/stages").json()
    state = {stage["job"]: stage["enabled"] for stage in body["stages"]}
    assert state["lint"] is False, "the skip value switches the stage off"
    assert state["typecheck"] is False, "GitHub compares expression strings case-insensitively"
    assert state["test"] is True, "an explicit false is not the skip value"
    assert state["build"] is True, "a typo keeps the gate enabled (fail-safe)"
    assert state["security"] is True, "an absent variable keeps the stage enabled"
    assert "UNRELATED_VARIABLE" not in state, "only catalog variables are reported"


def test_unconfigured_contour_serves_the_catalog_without_state() -> None:
    body = _client(None).get(f"{API}/ci/stages").json()
    assert body["available"] is False
    assert body["repository"] is None
    assert "DARK_FACTORY_GITHUB_REPOSITORY_SLUG" in body["reason"]
    assert len(body["stages"]) == len(CI_STAGES)
    assert all(stage["enabled"] is None for stage in body["stages"]), "unknown state, not guessed"


def test_provider_read_failure_is_a_502_problem() -> None:
    toggles = FakeToggles()
    toggles.failure = PortError("GitHub GET /repos/small/pilot/actions/variables failed with 403")
    response = _client(toggles).get(f"{API}/ci/stages")
    assert response.status_code == 502
    _assert_error_body(response.json(), 502, "Bad Gateway")
    assert "403" in response.json()["detail"], "the operator sees the provider status"


# --- Authorization of the writes --------------------------------------------


def test_put_without_a_token_is_401() -> None:
    toggles = FakeToggles()
    response = _client(toggles).put(f"{API}/ci/stages/lint", json={"enabled": False})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    _assert_error_body(response.json(), 401, "Unauthorized")
    assert toggles.writes == [], "nothing is written before authentication"


def test_put_with_an_unknown_token_is_401() -> None:
    response = _client(FakeToggles()).put(
        f"{API}/ci/stages/lint", json={"enabled": False}, headers=_auth("not-a-token")
    )
    assert response.status_code == 401
    _assert_error_body(response.json(), 401, "Unauthorized")


def test_put_without_the_ci_scope_is_403() -> None:
    toggles = FakeToggles()
    response = _client(toggles).put(
        f"{API}/ci/stages/lint", json={"enabled": False}, headers=_auth(OTHER_SCOPE_TOKEN)
    )
    assert response.status_code == 403
    _assert_error_body(response.json(), 403, "Forbidden")
    assert toggles.writes == []


def test_put_from_the_service_role_is_403() -> None:
    """Agents never reconfigure the pipeline that gates them (ADR-011)."""
    toggles = FakeToggles()
    response = _client(toggles).put(
        f"{API}/ci/stages/lint", json={"enabled": False}, headers=_auth(SERVICE_TOKEN)
    )
    assert response.status_code == 403
    _assert_error_body(response.json(), 403, "Forbidden")
    assert toggles.writes == []


def test_empty_token_store_fails_closed() -> None:
    factory = sessionmaker(expire_on_commit=False)
    client = TestClient(create_app(factory, tokens=ApiTokenStore(), ci_toggles=FakeToggles()))
    response = client.put(f"{API}/ci/stages/lint", json={"enabled": False}, headers=_auth())
    assert response.status_code == 401


# --- Writing one toggle ------------------------------------------------------


def test_put_disables_a_stage_with_the_skip_value() -> None:
    toggles = FakeToggles()
    response = _client(toggles).put(
        f"{API}/ci/stages/lint", json={"enabled": False}, headers=_auth()
    )
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is False
    assert body["variable"] == "CI_SKIP_LINT"
    assert body["job"] == "lint"
    assert toggles.writes == [("CI_SKIP_LINT", SKIP_VALUE)]
    assert toggles.stored == {"CI_SKIP_LINT": "true"}


def test_put_enables_a_stage_by_removing_the_variable() -> None:
    toggles = FakeToggles({"CI_SKIP_LINT": "true"})
    response = _client(toggles).put(
        f"{API}/ci/stages/lint", json={"enabled": True}, headers=_auth()
    )
    assert response.status_code == 200
    assert response.json()["enabled"] is True
    assert toggles.writes == [("CI_SKIP_LINT", None)], "enabled means absent (ADR-026)"
    assert toggles.stored == {}


def test_repeating_a_write_keeps_the_same_state() -> None:
    """The call is a target state, not an event: a retry changes nothing."""
    toggles = FakeToggles()
    client = _client(toggles)
    for _ in range(2):
        response = client.put(f"{API}/ci/stages/lint", json={"enabled": False}, headers=_auth())
        assert response.status_code == 200
        assert response.json()["enabled"] is False
    assert toggles.stored == {"CI_SKIP_LINT": "true"}


@pytest.mark.parametrize("job", ["nope", "CI_SKIP_LINT", "lint-disabled", "uikit-storybooks"])
def test_put_of_an_unknown_stage_is_404_and_writes_nothing(job: str) -> None:
    toggles = FakeToggles()
    response = _client(toggles).put(
        f"{API}/ci/stages/{job}", json={"enabled": False}, headers=_auth()
    )
    assert response.status_code == 404
    assert toggles.writes == [], "a request can never name an arbitrary variable"


def test_put_on_an_unconfigured_contour_is_503() -> None:
    response = _client(None).put(f"{API}/ci/stages/lint", json={"enabled": False}, headers=_auth())
    assert response.status_code == 503
    _assert_error_body(response.json(), 503, "Service Unavailable")
    assert "DARK_FACTORY_GITHUB_REPOSITORY_SLUG" in response.json()["detail"]


def test_provider_write_failure_is_a_502_problem() -> None:
    toggles = FakeToggles()
    toggles.failure = PortError("GitHub PATCH /repos/small/pilot/actions/variables failed with 404")
    response = _client(toggles).put(
        f"{API}/ci/stages/lint", json={"enabled": False}, headers=_auth()
    )
    assert response.status_code == 502
    _assert_error_body(response.json(), 502, "Bad Gateway")


@pytest.mark.parametrize("payload", [{"enabled": "yes"}, {"enabled": 1}, {"enabled": "true"}, {}])
def test_put_requires_a_real_boolean_body(payload: dict[str, Any]) -> None:
    toggles = FakeToggles()
    response = _client(toggles).put(f"{API}/ci/stages/lint", json=payload, headers=_auth())
    assert response.status_code == 422
    _assert_error_body(response.json(), 422, "Unprocessable Entity")
    assert toggles.writes == []


def test_openapi_exposes_the_ci_paths() -> None:
    paths = _client(None).get("/openapi.json").json()["paths"]
    assert set(paths[f"{API}/ci/stages"]) == {"get"}
    assert set(paths[f"{API}/ci/stages/{{job}}"]) == {"put"}
