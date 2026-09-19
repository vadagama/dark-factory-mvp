"""Product registry endpoints against PostgreSQL (T066, ADR-030/ADR-031).

Requires ``DARK_FACTORY_TEST_DATABASE_URL``; skipped without it. Validation runs
against ``FakeRepositoryProvisioning`` (the contract fake of the provisioning
port), so the shipped states of ADR-031 p.4 are exercised without a repository.
"""

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from dark_factory.adapters.fakes import FakeRepositoryProvisioning
from dark_factory.api.app import create_app
from dark_factory.api.auth import ApiToken, ApiTokenStore
from dark_factory.changes.enums import ProductStatus, Provider
from dark_factory.changes.refs import RepositoryRef
from dark_factory.orchestration.state.change_store import (
    PRODUCT_ADD_ACTION,
    PRODUCT_VALIDATE_ACTION,
)
from dark_factory.orchestration.state.engine import session_scope
from dark_factory.orchestration.state.models import AuditLogEntry
from dark_factory.ports import RepositoryProvisioningPort, RepositoryState

API = "/api/v1"
OPERATOR = {"Authorization": "Bearer op-token"}
REPOSITORY = RepositoryRef(provider=Provider.GITHUB, slug="org/repo")

_BODY: dict[str, Any] = {
    "id": "prd-1",
    "name": "Pilot product",
    "description": "the product the pilot runs against",
    "repository": {"provider": "github", "slug": "org/repo"},
    "repository_url": "https://github.com/org/repo",
    "baseline_ref": ".factory/product",
    "dev_env_ref": "factory-dev/org-repo",
}


def _store() -> ApiTokenStore:
    return ApiTokenStore(
        [
            (
                "op-token",
                ApiToken(actor="alice", role="operator", scopes=frozenset({"products:write"})),
            )
        ]
    )


def _client(
    session_factory: sessionmaker[Session],
    provisioning: RepositoryProvisioningPort | None = None,
) -> TestClient:
    return TestClient(create_app(session_factory, tokens=_store(), provisioning=provisioning))


def _seeded_provisioning(state: str) -> FakeRepositoryProvisioning:
    provisioning = FakeRepositoryProvisioning()
    provisioning.seed(REPOSITORY, state)
    return provisioning


def _product_audit(session_factory: sessionmaker[Session]) -> list[tuple[str, str, str]]:
    """Audit rows of the ``product`` resource, in occurrence order.

    The row id is a random uuid4, so it is not insertion order; ``occurred_at``
    is. Order-sensitive assertions still compare sorted projections, because two
    rows of one request can share a timestamp.
    """
    with session_scope(session_factory) as session:
        rows = session.execute(
            select(AuditLogEntry.action, AuditLogEntry.outcome, AuditLogEntry.resource_id)
            .where(AuditLogEntry.resource_type == "product")
            .order_by(AuditLogEntry.occurred_at)
        ).all()
    return [(row[0], row[1], row[2]) for row in rows]


def _register(client: TestClient) -> dict[str, Any]:
    response = client.post(f"{API}/products", json=_BODY, headers=OPERATOR)
    assert response.status_code == 201, response.text
    payload: dict[str, Any] = response.json()
    return payload


def test_registration_creates_the_product(session_factory: sessionmaker[Session]) -> None:
    product = _register(_client(session_factory))

    assert product["id"] == "prd-1"
    assert product["name"] == "Pilot product"
    assert product["description"] == "the product the pilot runs against"
    assert product["repository"] == {"provider": "github", "slug": "org/repo"}
    assert product["repository_url"] == "https://github.com/org/repo"
    assert product["baseline_ref"] == ".factory/product"
    assert product["dev_env_ref"] == "factory-dev/org-repo"
    assert product["status"] == ProductStatus.CREATED.value
    assert product["state_revision"] == 1
    assert _product_audit(session_factory) == [(PRODUCT_ADD_ACTION, "created", "prd-1")]


def test_registration_replays_by_product_id(session_factory: sessionmaker[Session]) -> None:
    client = _client(session_factory)
    _register(client)

    replayed = client.post(f"{API}/products", json=_BODY, headers=OPERATOR)

    assert replayed.status_code == 200
    assert replayed.json()["id"] == "prd-1"
    assert sorted(row[1] for row in _product_audit(session_factory)) == ["created", "replayed"]
    assert len(client.get(f"{API}/products", headers=OPERATOR).json()) == 1


def test_get_and_list_products(session_factory: sessionmaker[Session]) -> None:
    client = _client(session_factory)
    _register(client)

    listed = client.get(f"{API}/products").json()
    assert [product["id"] for product in listed] == ["prd-1"]

    fetched = client.get(f"{API}/products/prd-1")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == "prd-1"

    missing = client.get(f"{API}/products/prd-missing")
    assert missing.status_code == 404
    assert missing.json()["title"] == "Not Found"


def test_validation_records_readiness(session_factory: sessionmaker[Session]) -> None:
    provisioning = _seeded_provisioning("baseline_absent")
    client = _client(session_factory, provisioning)
    _register(client)

    response = client.post(f"{API}/products/prd-1/validate", headers=OPERATOR)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == ProductStatus.READY.value
    assert payload["status_reason"] is None
    assert payload["validation"]["state"] == RepositoryState.BASELINE_ABSENT.value
    assert payload["validation"]["head_revision"] is not None
    assert payload["validation"]["default_branch"] == "main"
    # created -> validating -> ready: the state machine passes through validating.
    assert payload["state_revision"] == 3
    assert sorted(row[0] for row in _product_audit(session_factory)) == sorted(
        [PRODUCT_ADD_ACTION, PRODUCT_VALIDATE_ACTION]
    )


def test_validation_records_an_unreachable_repository_as_an_error(
    session_factory: sessionmaker[Session],
) -> None:
    client = _client(session_factory, _seeded_provisioning("unavailable"))
    _register(client)

    payload = client.post(f"{API}/products/prd-1/validate", headers=OPERATOR).json()

    assert payload["status"] == ProductStatus.ERROR.value
    assert payload["status_reason"]
    assert payload["validation"]["state"] == RepositoryState.UNAVAILABLE.value


def test_validation_can_be_repeated(session_factory: sessionmaker[Session]) -> None:
    client = _client(session_factory, _seeded_provisioning("baseline_current"))
    _register(client)

    first = client.post(f"{API}/products/prd-1/validate", headers=OPERATOR).json()
    second = client.post(f"{API}/products/prd-1/validate", headers=OPERATOR).json()

    assert first["status"] == ProductStatus.READY.value
    assert second["status"] == ProductStatus.READY.value
    assert second["state_revision"] > first["state_revision"]


def test_validation_without_a_body_is_accepted(
    session_factory: sessionmaker[Session],
) -> None:
    client = _client(session_factory, _seeded_provisioning("empty"))
    _register(client)

    response = client.post(f"{API}/products/prd-1/validate", headers=OPERATOR)

    assert response.status_code == 200, response.text
    assert response.json()["validation"]["state"] == RepositoryState.EMPTY.value
    assert response.json()["status"] == ProductStatus.READY.value


def test_validation_without_provisioning_is_refused_and_changes_nothing(
    session_factory: sessionmaker[Session],
) -> None:
    client = _client(session_factory)
    _register(client)

    response = client.post(f"{API}/products/prd-1/validate", headers=OPERATOR)

    assert response.status_code == 503
    assert response.json()["title"] == "Service Unavailable"
    product = client.get(f"{API}/products/prd-1").json()
    assert product["status"] == ProductStatus.CREATED.value
    assert product["state_revision"] == 1
    assert [row[0] for row in _product_audit(session_factory)] == [PRODUCT_ADD_ACTION]


def test_validation_of_an_unknown_product_is_404(
    session_factory: sessionmaker[Session],
) -> None:
    response = _client(session_factory, _seeded_provisioning("empty")).post(
        f"{API}/products/prd-missing/validate", headers=OPERATOR
    )
    assert response.status_code == 404


def test_validation_rejects_a_stale_expected_revision(
    session_factory: sessionmaker[Session],
) -> None:
    client = _client(session_factory, _seeded_provisioning("empty"))
    _register(client)

    response = client.post(
        f"{API}/products/prd-1/validate",
        json={"expected_state_revision": 99},
        headers=OPERATOR,
    )

    assert response.status_code == 409
    assert client.get(f"{API}/products/prd-1").json()["state_revision"] == 1
