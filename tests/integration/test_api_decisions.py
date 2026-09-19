"""Decision cards, «Запросить альтернативу» and the UI spec over the API (T093/T094).

Requires ``DARK_FACTORY_TEST_DATABASE_URL``; skipped without it. The change
branch of the ``FakeRepository`` carries the pack's design templates (ADR,
overview, SCN, SCR) so the cards and the UI spec are read from real commits.
"""

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from dark_factory.adapters.fakes import FakeRepository, FakeRepositoryProvisioning
from dark_factory.api.app import create_app
from dark_factory.api.auth import ApiToken, ApiTokenStore
from dark_factory.api.routes_artifacts import REPOSITORY_UNCONFIGURED_DETAIL
from dark_factory.changes.enums import Provider
from dark_factory.changes.refs import RepositoryRef
from dark_factory.orchestration.stages.agent import branch_name

PACK = Path(__file__).resolve().parents[2] / "packs" / "product-baseline" / "changeset" / "design"
API = "/api/v1"
OPERATOR = {"Authorization": "Bearer op-token"}
AGENT = {"Authorization": "Bearer agent-token"}
CHANGE_ID = "chg-calc-1"
REPO = RepositoryRef(provider=Provider.GITHUB, slug="small/calculator")
CHG = ".factory/changes/2026/CHG-0001-percent"
REQ = f"{CHG}/spec/requirements/REQ-001-percent.md"
ADR = f"{CHG}/design/decisions/ADR-001-example.md"
OVERVIEW = f"{CHG}/design/overview.md"
SCN = f"{CHG}/design/ui/scenarios/SCN-001-example.md"
SCR = f"{CHG}/design/ui/screens/SCR-001-example.md"

PRODUCT_BODY: dict[str, Any] = {
    "id": "prd-calc",
    "name": "Calculator",
    "repository": {"provider": "github", "slug": "small/calculator"},
}
CHANGE_BODY: dict[str, Any] = {
    "id": CHANGE_ID,
    "title": "Percent button",
    "source": "console",
    "product": {"provider": "github", "slug": "small/calculator"},
    "product_id": "prd-calc",
    "risk_class": "R1",
    "brief": {"problem": "no percent", "goal": "percent works"},
}


def _store() -> ApiTokenStore:
    return ApiTokenStore(
        [
            (
                "op-token",
                ApiToken(
                    actor="alice",
                    role="operator",
                    scopes=frozenset({"products:write", "changes:write", "approvals:write"}),
                ),
            ),
            (
                "agent-token",
                ApiToken(actor="spec-agent", role="service", scopes=frozenset({"changes:write"})),
            ),
        ]
    )


def _seeded_repository() -> tuple[FakeRepository, str]:
    repository = FakeRepository()
    branch = branch_name(CHANGE_ID)
    asyncio.run(repository.ensure_branch(REPO, "main", from_revision="base", idempotency_key="m"))
    asyncio.run(repository.ensure_branch(REPO, branch, from_revision="base", idempotency_key="b"))
    head = asyncio.run(
        repository.publish_commit(
            REPO,
            branch,
            {
                REQ: b"# REQ-001\n\n- AC-1: works\n",
                OVERVIEW: (PACK / "overview.md").read_bytes(),
                ADR: (PACK / "decisions" / "ADR-001-example.md").read_bytes(),
                SCN: (PACK / "ui" / "scenarios" / "SCN-001-example.md").read_bytes(),
                SCR: (PACK / "ui" / "screens" / "SCR-001-example.md").read_bytes(),
            },
            message="design v1",
            idempotency_key="c1",
        )
    )
    return repository, head


@pytest.fixture
def client(session_factory: sessionmaker[Session]) -> tuple[TestClient, FakeRepository, str]:
    repository, head = _seeded_repository()
    app = create_app(
        session_factory,
        tokens=_store(),
        provisioning=FakeRepositoryProvisioning(),
        repository=repository,
    )
    api = TestClient(app)
    assert api.post(f"{API}/products", json=PRODUCT_BODY, headers=OPERATOR).status_code == 201
    assert api.post(f"{API}/changes", json=CHANGE_BODY, headers=OPERATOR).status_code == 201
    return api, repository, head


def test_decision_cards_are_read_from_the_adr_files(
    client: tuple[TestClient, FakeRepository, str],
) -> None:
    api, _repository, head = client
    response = api.get(f"{API}/changes/{CHANGE_ID}/decisions")
    assert response.status_code == 200, response.text
    view = response.json()
    assert view["change_id"] == CHANGE_ID and view["revision"] == head
    assert view["approved"] is False and view["errors"] == []
    (card,) = view["decisions"]
    assert card["id"] == "adr:example-product:0001" and card["path"] == ADR
    assert card["status"] == "proposed" and card["document_status"] == "proposed"
    assert card["revision"] == head
    assert card["proposal"].startswith("Длительность таймаута")
    assert card["rationale"] and card["consequences"]
    assert [a["title"] for a in card["alternatives"]] == [
        "Константа в коде",
        "Таймаут на стороне клиента",
    ]
    assert card["alternatives"][0]["rejected_because"] == "Противоречит требованию AC-2"
    assert card["impact"] == ["public_api", "ui"]
    assert card["pending_alternative"] is None and card["affected_artifacts"] == []
    assert api.get(f"{API}/changes/chg-missing/decisions").status_code == 404

    # An architecture approval bound to the phase revision makes the card accepted.
    approved = api.post(
        f"{API}/changes/{CHANGE_ID}/approvals",
        json={
            "gate": "specification",
            "phase": "architecture",
            "outcome": "approved",
            "subject_revision": head,
        },
        headers=OPERATOR,
    )
    assert approved.status_code == 201, approved.text
    after = api.get(f"{API}/changes/{CHANGE_ID}/decisions").json()
    assert after["approved"] is True and after["decisions"][0]["status"] == "accepted"


def test_request_alternative_creates_an_architecture_rework_order(
    client: tuple[TestClient, FakeRepository, str],
) -> None:
    api, _repository, head = client
    comment = api.post(
        f"{API}/changes/{CHANGE_ID}/comments",
        json={
            "artifact": ADR,
            "anchor_id": "решение",
            "body": "too rigid",
            "phase": "architecture",
        },
        headers=OPERATOR,
    )
    assert comment.status_code == 201, comment.text
    body = {"instruction": "consider a client-side timeout", "comment_ids": [comment.json()["id"]]}
    url = f"{API}/changes/{CHANGE_ID}/decisions/adr:example-product:0001/alternative"

    assert api.post(url, json=body, headers=AGENT).status_code == 403, "operator only"
    blank = api.post(url, json={"instruction": "   "}, headers=OPERATOR)
    assert blank.status_code == 422
    assert api.post(url, json={}, headers=OPERATOR).status_code == 422, "instruction is required"
    unknown = api.post(
        f"{API}/changes/{CHANGE_ID}/decisions/adr:nope/alternative", json=body, headers=OPERATOR
    )
    assert unknown.status_code == 404

    created = api.post(url, json=body, headers={**OPERATOR, "Idempotency-Key": "alt-1"})
    assert created.status_code == 201, created.text
    order = created.json()
    assert order["phase"] == "architecture" and order["status"] == "pending"
    assert order["decision_ids"] == ["adr:example-product:0001"]
    assert order["comment_ids"] == [comment.json()["id"]]
    assert order["instruction"] == "consider a client-side timeout"
    assert order["revisions"][ADR] == head
    replay = api.post(url, json=body, headers={**OPERATOR, "Idempotency-Key": "alt-1"})
    assert replay.status_code == 200 and replay.json()["id"] == order["id"]

    # The rejected decision of the architecture phase is recorded next to the order.
    decisions = api.get(f"{API}/changes/{CHANGE_ID}/approvals").json()
    assert [(d["outcome"], d["phase"], d["commit_sha"]) for d in decisions] == [
        ("rejected", "architecture", head)
    ]
    # The card now needs revision and shows the pending order.
    card = api.get(f"{API}/changes/{CHANGE_ID}/decisions").json()["decisions"][0]
    assert card["status"] == "needs_revision"
    assert card["pending_alternative"]["id"] == order["id"]
    # One round at a time: a second request while the first is pending is a conflict.
    second = api.post(url, json={"instruction": "again"}, headers=OPERATOR)
    assert second.status_code == 409
    listed = api.get(f"{API}/changes/{CHANGE_ID}/rework-orders", params={"phase": "architecture"})
    assert [o["id"] for o in listed.json()] == [order["id"]]


def test_ui_spec_is_read_from_the_scenario_and_screen_files(
    client: tuple[TestClient, FakeRepository, str],
) -> None:
    api, _repository, head = client
    response = api.get(f"{API}/changes/{CHANGE_ID}/ui")
    assert response.status_code == 200, response.text
    view = response.json()
    assert set(view) == {
        "change_id",
        "revision",
        "dev_url",
        "scenarios",
        "screens",
        "links",
        "components",
        "errors",
    }
    assert view["change_id"] == CHANGE_ID and view["revision"] == head
    assert view["dev_url"] is None, "the pack manifest declares no dev_url"
    (scenario,) = view["scenarios"]
    assert scenario["id"] == "SCN-001" and scenario["path"] == SCN
    assert [s["id"] for s in scenario["steps"]] == ["S1", "S2", "S3"]
    assert scenario["steps"][0]["screen"] == "SCR-001" and scenario["screens"] == ["SCR-001"]
    (screen,) = view["screens"]
    assert screen["id"] == "SCR-001" and screen["route"] == "/checkout/confirm"
    assert screen["preview_url"] == "/checkout/confirm", "relative stays relative without dev_url"
    assert [s["kind"] for s in screen["states"]] == [
        "loading",
        "empty",
        "error",
        "success",
        "access",
    ]
    assert [e["id"] for e in screen["elements"]][:2] == ["EL-summary", "EL-confirm"]
    assert screen["components"] == ["Card", "Button", "Alert"]
    assert view["links"] == [
        {
            "id": "SCR-001->SCR-001",
            "from_screen": "SCR-001",
            "to_screen": "SCR-001",
            "trigger": "Нажатие «Повторить»",
            "condition": "Состояние error",
        }
    ]
    assert view["components"][0] == {"name": "Card", "screens": ["SCR-001"]}
    assert view["errors"] == []
    # An element comment anchors to EL-*; the anchor resolves against the screen file.
    comment = api.post(
        f"{API}/changes/{CHANGE_ID}/comments",
        json={"artifact": SCR, "anchor_id": "EL-retry", "body": "label", "phase": "interface"},
        headers=OPERATOR,
    )
    assert comment.status_code == 201 and comment.json()["anchor_state"] == "attached"
    tree = api.get(f"{API}/changes/{CHANGE_ID}/artifacts").json()
    kinds = {node["path"]: node["kind"] for node in tree["nodes"]}
    assert kinds[SCN] == "ui" and kinds[SCR] == "ui" and kinds[ADR] == "adr"
    assert kinds[OVERVIEW] == "design"


def test_decisions_and_ui_answer_503_without_a_repository(
    session_factory: sessionmaker[Session],
) -> None:
    api = TestClient(create_app(session_factory, tokens=_store()))
    api.post(f"{API}/products", json=PRODUCT_BODY, headers=OPERATOR)
    api.post(f"{API}/changes", json=CHANGE_BODY, headers=OPERATOR)
    decisions = api.get(f"{API}/changes/{CHANGE_ID}/decisions")
    assert (
        decisions.status_code == 503
        and decisions.json()["detail"] == REPOSITORY_UNCONFIGURED_DETAIL
    )
    assert api.get(f"{API}/changes/{CHANGE_ID}/ui").status_code == 503
    alternative = api.post(
        f"{API}/changes/{CHANGE_ID}/decisions/adr:x/alternative",
        json={"instruction": "x"},
        headers=OPERATOR,
    )
    assert alternative.status_code == 503
