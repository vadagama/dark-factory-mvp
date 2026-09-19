"""Intake with a brief, the brief agent and Guidance over the API on PostgreSQL (T071-T074).

Requires ``DARK_FACTORY_TEST_DATABASE_URL``; skipped without it. The brief
formulator runs on a scripted harness (no LLM); products come from the real
registry, so ``Guidance`` is computed from stored facts exactly as in the
served API.
"""

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from dark_factory.adapters.fakes import FakeRepositoryProvisioning
from dark_factory.api.app import create_app
from dark_factory.api.auth import ApiToken, ApiTokenStore
from dark_factory.orchestration.intake import BriefFormulator
from dark_factory.ports import AgentResult, HealthStatus, TaskEnvelope

API = "/api/v1"
OPERATOR = {"Authorization": "Bearer op-token"}

PRODUCT_BODY: dict[str, Any] = {
    "id": "prd-calc",
    "name": "Calculator",
    "repository": {"provider": "github", "slug": "small/calculator"},
}
CHANGE_BODY: dict[str, Any] = {
    "id": "chg-calc-1",
    "title": "Percent button",
    "source": "console",
    "product": {"provider": "github", "slug": "small/calculator"},
    "product_id": "prd-calc",
    "risk_class": "R1",
    "brief": {"problem": "no percent operation", "goal": "a % button that works"},
    "scenario": "specs_only",
    "spend_limit": {"cost_budget_usd": "15.00"},
}


class JsonHarness:
    async def run_stage(self, envelope: TaskEnvelope, /) -> AgentResult:
        return AgentResult(
            ok=True,
            output='{"problem": "no percent", "goal": "percent works",'
            ' "constraints": ["keep keyboard"], "out_of_scope": ["scientific mode"]}',
        )

    async def health(self, /) -> HealthStatus:
        return HealthStatus(healthy=True, detail="json")


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
            )
        ]
    )


def _client(
    session_factory: sessionmaker[Session], *, formulator: BriefFormulator | None = None
) -> TestClient:
    provisioning = FakeRepositoryProvisioning()
    return TestClient(
        create_app(
            session_factory, tokens=_store(), provisioning=provisioning, brief_formulator=formulator
        )
    )


def test_intake_stores_brief_scenario_and_limit(session_factory: sessionmaker[Session]) -> None:
    client = _client(session_factory)
    client.post(f"{API}/products", json=PRODUCT_BODY, headers=OPERATOR)

    response = client.post(f"{API}/changes", json=CHANGE_BODY, headers=OPERATOR)

    assert response.status_code == 201, response.text
    change = response.json()
    assert change["brief"]["status"] == "complete"
    assert change["brief"]["problem"] == "no percent operation"
    assert change["scenario"] == "specs_only"
    assert change["spend_limit"] == {"cost_budget_usd": "15.00", "token_budget": None}
    card = client.get(f"{API}/changes/chg-calc-1").json()
    assert card["spend_limit"]["cost_budget_usd"] == "15.00"
    assert card["brief"]["goal"] == "a % button that works"


def test_changes_can_be_listed_per_product(session_factory: sessionmaker[Session]) -> None:
    client = _client(session_factory)
    client.post(f"{API}/products", json=PRODUCT_BODY, headers=OPERATOR)
    client.post(f"{API}/changes", json=CHANGE_BODY, headers=OPERATOR)
    other = {**CHANGE_BODY, "id": "chg-other", "product_id": "prd-other"}
    client.post(f"{API}/changes", json=other, headers=OPERATOR)

    assert [
        c["id"] for c in client.get(f"{API}/changes", params={"product_id": "prd-calc"}).json()
    ] == ["chg-calc-1"]
    assert len(client.get(f"{API}/changes").json()) == 2


def test_product_guidance_follows_readiness(session_factory: sessionmaker[Session]) -> None:
    client = _client(session_factory)
    client.post(f"{API}/products", json=PRODUCT_BODY, headers=OPERATOR)

    created = client.get(f"{API}/products/prd-calc/guidance")
    assert created.status_code == 200, created.text
    guidance = created.json()
    assert guidance["subject"] == {"kind": "product", "id": "prd-calc"}
    assert guidance["primary"]["api"] == "POST /products/prd-calc/validate"
    assert guidance["primary"]["cli"] == "factory product validate --id prd-calc"

    client.post(f"{API}/products/prd-calc/validate", headers=OPERATOR)  # unseeded → error
    errored = client.get(f"{API}/products/prd-calc/guidance").json()
    assert errored["headline"] == "Репозиторий недоступен фабрике"
    assert errored["blockers"][0]["who"] == "operator"
    assert client.get(f"{API}/products/prd-missing/guidance").status_code == 404


def test_change_guidance_names_the_next_step(session_factory: sessionmaker[Session]) -> None:
    client = _client(session_factory)
    client.post(f"{API}/products", json=PRODUCT_BODY, headers=OPERATOR)
    client.post(f"{API}/changes", json=CHANGE_BODY, headers=OPERATOR)

    response = client.get(f"{API}/changes/chg-calc-1/guidance")

    assert response.status_code == 200, response.text
    guidance = response.json()
    assert guidance["subject"] == {"kind": "change", "id": "chg-calc-1"}
    assert guidance["phase"] == "initiative"
    # The product is only registered, not ready: the start is blocked, with a way out.
    assert guidance["primary"]["enabled"] is False
    assert "не готов" in guidance["primary"]["reason"]
    assert guidance["blockers"][0]["how"].endswith("factory product validate --id prd-calc.")
    assert "specs-only" in guidance["why"]
    assert "15.00 USD" in guidance["why"]
    assert client.get(f"{API}/changes/chg-missing/guidance").status_code == 404


def test_brief_update_replaces_the_brief_and_moves_the_guidance(
    session_factory: sessionmaker[Session],
) -> None:
    client = _client(session_factory)
    client.post(f"{API}/products", json=PRODUCT_BODY, headers=OPERATOR)
    draft = {**CHANGE_BODY, "brief": {"source_text": "make percent work"}}
    client.post(f"{API}/changes", json=draft, headers=OPERATOR)
    before = client.get(f"{API}/changes/chg-calc-1/guidance").json()
    assert before["headline"] == "Бриф в черновике"
    assert before["primary"]["api"] == "PUT /changes/chg-calc-1/brief"

    response = client.put(
        f"{API}/changes/chg-calc-1/brief",
        json={"problem": "no percent", "goal": "percent works", "source_text": "make percent work"},
        headers=OPERATOR,
    )

    assert response.status_code == 200, response.text
    assert response.json()["brief"]["status"] == "complete"
    assert response.json()["brief"]["source_text"] == "make percent work"
    after = client.get(f"{API}/changes/chg-calc-1/guidance").json()
    assert after["headline"] != "Бриф в черновике"
    assert (
        client.put(f"{API}/changes/chg-missing/brief", json={}, headers=OPERATOR).status_code == 404
    )


def test_brief_formulation_runs_the_agent(session_factory: sessionmaker[Session]) -> None:
    client = _client(session_factory, formulator=BriefFormulator(JsonHarness()))

    response = client.post(
        f"{API}/briefs/formulate", json={"source_text": "I want percent"}, headers=OPERATOR
    )

    assert response.status_code == 200, response.text
    brief = response.json()
    assert brief["status"] == "complete"
    assert brief["problem"] == "no percent"
    assert brief["constraints"] == ["keep keyboard"]
    assert brief["out_of_scope"] == ["scientific mode"]
    assert brief["source_text"] == "I want percent"
    assert brief["formulated_by"] == "agent"
