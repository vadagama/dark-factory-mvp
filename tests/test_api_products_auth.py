"""AuthN/AuthZ and contract-shape tests of the product endpoints (T066, ADR-030).

The app is built over a session factory that is never used: authentication
failures and OpenAPI generation happen before any route body runs, so no route
touches the store. Any accidental database access would raise and fail loudly.
"""

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from dark_factory.api.app import create_app
from dark_factory.api.auth import ApiToken, ApiTokenStore

API = "/api/v1"

PRODUCT_BODY: dict[str, Any] = {
    "id": "prd-1",
    "name": "Pilot product",
    "repository": {"provider": "github", "slug": "org/repo"},
}


def _store() -> ApiTokenStore:
    return ApiTokenStore(
        [
            (
                "op-token",
                ApiToken(
                    actor="alice",
                    role="operator",
                    scopes=frozenset({"products:write", "changes:write"}),
                ),
            ),
            (
                "svc-products-token",
                ApiToken(actor="runner", role="service", scopes=frozenset({"products:write"})),
            ),
            ("null-token", ApiToken(actor="bot", role="service", scopes=frozenset())),
        ]
    )


def _client(tokens: ApiTokenStore | None = None) -> TestClient:
    factory = sessionmaker(expire_on_commit=False)
    return TestClient(create_app(factory, tokens=tokens if tokens is not None else _store()))


def _assert_error_body(payload: dict[str, Any], status: int, title: str) -> None:
    assert payload["type"] == "about:blank"
    assert payload["title"] == title
    assert payload["status"] == status
    assert isinstance(payload["detail"], str) and payload["detail"]


def test_unauthenticated_registration_is_rejected_with_401() -> None:
    response = _client().post(f"{API}/products", json=PRODUCT_BODY)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    _assert_error_body(response.json(), 401, "Unauthorized")


def test_token_without_the_products_scope_is_rejected_with_403() -> None:
    response = _client().post(
        f"{API}/products", json=PRODUCT_BODY, headers={"Authorization": "Bearer null-token"}
    )
    assert response.status_code == 403
    _assert_error_body(response.json(), 403, "Forbidden")


def test_service_role_cannot_register_a_product() -> None:
    # Registration is an operator action (ADR-030 p.6): a product is registered
    # for a human, and an agent never invents one.
    response = _client().post(
        f"{API}/products",
        json=PRODUCT_BODY,
        headers={"Authorization": "Bearer svc-products-token"},
    )
    assert response.status_code == 403
    _assert_error_body(response.json(), 403, "Forbidden")


def test_unauthenticated_validation_is_rejected_with_401() -> None:
    response = _client().post(f"{API}/products/prd-1/validate")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_service_role_cannot_validate_a_product() -> None:
    response = _client().post(
        f"{API}/products/prd-1/validate",
        headers={"Authorization": "Bearer svc-products-token"},
    )
    assert response.status_code == 403
    _assert_error_body(response.json(), 403, "Forbidden")


def test_changes_scope_does_not_authorize_registration() -> None:
    store = ApiTokenStore(
        [
            (
                "changes-only",
                ApiToken(actor="ci", role="operator", scopes=frozenset({"changes:write"})),
            )
        ]
    )
    response = _client(tokens=store).post(
        f"{API}/products", json=PRODUCT_BODY, headers={"Authorization": "Bearer changes-only"}
    )
    assert response.status_code == 403
    _assert_error_body(response.json(), 403, "Forbidden")


def test_openapi_schema_contains_the_product_paths() -> None:
    paths = _client().get("/openapi.json").json()["paths"]
    assert set(paths[f"{API}/products"]) == {"get", "post"}
    assert set(paths[f"{API}/products/{{product_id}}"]) == {"get"}
    assert set(paths[f"{API}/products/{{product_id}}/validate"]) == {"post"}
