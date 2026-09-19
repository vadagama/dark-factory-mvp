"""AuthN/AuthZ and contract-shape tests of the factory API without a database (T035).

The app is built over a session factory that is never used: authentication
failures, validation errors and OpenAPI generation all happen before any route
body runs, so no route touches the store. Any accidental database access would
raise and fail the test loudly.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from dark_factory.api.app import create_app
from dark_factory.api.auth import ApiToken, ApiTokenStore

API = "/api/v1"

CONTRACT_PATHS = [
    "/runs",
    "/runs/{run_id}",
    "/runs/{run_id}/stage-results",
    "/runs/{run_id}/trace",
    "/runs/{run_id}/evidence",
    "/runs/{run_id}/evidence/{evidence_id}",
    "/runs/{run_id}/gates",
    "/runs/{run_id}/findings",
    "/runs/{run_id}/withdraw",
    "/changes",
    "/changes/{change_id}",
    "/changes/{change_id}/trace",
    "/changes/{change_id}/approvals",
    "/changes/{change_id}/brief",
    "/changes/{change_id}/guidance",
    "/briefs/formulate",
]

CHANGE_BODY: dict[str, Any] = {
    "id": "chg-1",
    "title": "Add a health endpoint",
    "source": "tracker",
    "product": {"provider": "github", "slug": "org/repo"},
    "risk_class": "R1",
}


def _store() -> ApiTokenStore:
    return ApiTokenStore(
        [
            (
                "op-token",
                ApiToken(
                    actor="alice",
                    role="operator",
                    scopes=frozenset({"changes:write", "approvals:write", "runs:write"}),
                ),
            ),
            (
                "svc-token",
                ApiToken(actor="tracker", role="service", scopes=frozenset({"changes:write"})),
            ),
            (
                "svc-runs-token",
                ApiToken(actor="runner", role="service", scopes=frozenset({"runs:write"})),
            ),
            ("null-token", ApiToken(actor="bot", role="service", scopes=frozenset())),
        ]
    )


def _client(tokens: ApiTokenStore | None = None) -> TestClient:
    # A session factory that never connects: Session creation is lazy, and the
    # requests in these tests stop before any query would need a database.
    factory = sessionmaker(expire_on_commit=False)
    return TestClient(create_app(factory, tokens=tokens if tokens is not None else _store()))


def _assert_error_body(payload: dict[str, Any], status: int, title: str) -> None:
    assert payload["type"] == "about:blank"
    assert payload["title"] == title
    assert payload["status"] == status
    assert isinstance(payload["detail"], str) and payload["detail"]


def test_unauthenticated_intake_is_rejected_with_401() -> None:
    response = _client().post(f"{API}/changes", json=CHANGE_BODY)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    _assert_error_body(response.json(), 401, "Unauthorized")


def test_unknown_token_is_rejected_with_401() -> None:
    response = _client().post(
        f"{API}/changes",
        json=CHANGE_BODY,
        headers={"Authorization": "Bearer not-a-known-token"},
    )
    assert response.status_code == 401
    _assert_error_body(response.json(), 401, "Unauthorized")


def test_empty_token_store_fails_closed() -> None:
    response = _client(tokens=ApiTokenStore()).post(
        f"{API}/changes",
        json=CHANGE_BODY,
        headers={"Authorization": "Bearer op-token"},
    )
    assert response.status_code == 401
    _assert_error_body(response.json(), 401, "Unauthorized")


def test_token_without_scope_is_rejected_with_403() -> None:
    response = _client().post(
        f"{API}/changes",
        json=CHANGE_BODY,
        headers={"Authorization": "Bearer null-token"},
    )
    assert response.status_code == 403
    _assert_error_body(response.json(), 403, "Forbidden")


def test_service_role_cannot_approve() -> None:
    body = {"gate": "code", "outcome": "approved", "subject_revision": "abc123"}
    response = _client().post(
        f"{API}/changes/chg-1/approvals",
        json=body,
        headers={"Authorization": "Bearer svc-token"},
    )
    assert response.status_code == 403
    _assert_error_body(response.json(), 403, "Forbidden")


def test_unauthenticated_withdraw_is_rejected_with_401() -> None:
    response = _client().post(f"{API}/runs/run-1/withdraw")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    _assert_error_body(response.json(), 401, "Unauthorized")


def test_withdraw_requires_the_runs_write_scope() -> None:
    # A valid token with another scope must not reach the transition.
    response = _client().post(
        f"{API}/runs/run-1/withdraw", headers={"Authorization": "Bearer svc-token"}
    )
    assert response.status_code == 403
    _assert_error_body(response.json(), 403, "Forbidden")


def test_service_role_cannot_withdraw() -> None:
    # Agents execute the pipeline; they never retract the work that gates them.
    response = _client().post(
        f"{API}/runs/run-1/withdraw", headers={"Authorization": "Bearer svc-runs-token"}
    )
    assert response.status_code == 403
    _assert_error_body(response.json(), 403, "Forbidden")


def test_changes_scope_does_not_authorize_approvals() -> None:
    store = ApiTokenStore(
        [
            (
                "writer-token",
                ApiToken(actor="ci", role="service", scopes=frozenset({"changes:write"})),
            )
        ]
    )
    body = {"gate": "code", "outcome": "approved", "subject_revision": "abc123"}
    response = _client(tokens=store).post(
        f"{API}/changes/chg-1/approvals",
        json=body,
        headers={"Authorization": "Bearer writer-token"},
    )
    assert response.status_code == 403
    _assert_error_body(response.json(), 403, "Forbidden")


def test_openapi_schema_contains_all_contract_paths() -> None:
    response = _client().get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    for path in CONTRACT_PATHS:
        assert f"{API}{path}" in paths, path
    assert set(paths[f"{API}/changes"]) == {"get", "post"}
    assert set(paths[f"{API}/changes/{{change_id}}/approvals"]) == {"get", "post"}
    # The withdrawal is the one mutating run endpoint (T064, TD-030).
    assert set(paths[f"{API}/runs/{{run_id}}/withdraw"]) == {"post"}
    assert set(paths[f"{API}/runs/{{run_id}}"]) == {"get"}


def test_unknown_path_is_a_rfc7807_404() -> None:
    response = _client().get(f"{API}/nope")
    assert response.status_code == 404
    _assert_error_body(response.json(), 404, "Not Found")


def test_missing_subject_revision_is_a_rfc7807_422() -> None:
    response = _client().post(
        f"{API}/changes/chg-1/approvals",
        json={"gate": "code", "outcome": "approved"},
        headers={"Authorization": "Bearer op-token"},
    )
    assert response.status_code == 422
    _assert_error_body(response.json(), 422, "Unprocessable Entity")


@pytest.mark.parametrize("raw", ["", "   ", "Basic dXNlcjpwYXNz", "Bearer"])
def test_malformed_authorization_header_is_rejected_with_401(raw: str) -> None:
    response = _client().post(f"{API}/changes", json=CHANGE_BODY, headers={"Authorization": raw})
    assert response.status_code == 401


# --- intake brief (T071/T072) -------------------------------------------------------


def test_brief_formulation_requires_the_changes_scope() -> None:
    response = _client().post(f"{API}/briefs/formulate", json={"source_text": "make login fast"})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_brief_formulation_without_a_harness_answers_an_honest_draft() -> None:
    # No formulator is bound (the core path): the endpoint must not invent a
    # brief — it returns a draft whose error says the harness is absent and
    # keeps the operator's text. The route touches no store.
    response = _client().post(
        f"{API}/briefs/formulate",
        json={"source_text": "  make login fast  "},
        headers={"Authorization": "Bearer op-token"},
    )
    assert response.status_code == 200, response.text
    brief = response.json()
    assert brief["status"] == "draft"
    assert brief["source_text"] == "make login fast"
    assert brief["problem"] is None
    assert "DARK_FACTORY_LLM_" in brief["error"]


def test_brief_formulation_rejects_an_empty_text() -> None:
    response = _client().post(
        f"{API}/briefs/formulate",
        json={"source_text": ""},
        headers={"Authorization": "Bearer op-token"},
    )
    assert response.status_code == 422


def test_brief_update_requires_the_changes_scope() -> None:
    response = _client().put(f"{API}/changes/chg-1/brief", json={"problem": "p", "goal": "g"})
    assert response.status_code == 401
