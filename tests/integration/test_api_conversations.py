"""Discussion, artifacts, phase gate and approvals over the API on PostgreSQL (T078-T087).

Requires ``DARK_FACTORY_TEST_DATABASE_URL``; skipped without it. The product
repository is the in-memory ``FakeRepository`` seeded with a change branch and
a requirement file, so the write-through, the anchors and the version-bound
approvals are exercised against real commits of the fake.
"""

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from dark_factory.adapters.fakes import FakeRepository, FakeRepositoryProvisioning
from dark_factory.api.app import create_app
from dark_factory.api.auth import ApiToken, ApiTokenStore
from dark_factory.changes.enums import Provider
from dark_factory.changes.refs import RepositoryRef
from dark_factory.orchestration.stages.agent import branch_name
from dark_factory.ports import RepositoryState

API = "/api/v1"
OPERATOR = {"Authorization": "Bearer op-token"}
AGENT = {"Authorization": "Bearer agent-token"}
CHANGE_ID = "chg-calc-1"
REPO = RepositoryRef(provider=Provider.GITHUB, slug="small/calculator")
REQ = ".factory/changes/2026/CHG-0001-percent/spec/requirements/REQ-001-percent.md"
INTENT = ".factory/changes/2026/CHG-0001-percent/intent.md"
V1 = (
    "---\nschema: dark-factory.dev/requirement/v1\nid: req:calc:percent\ntype: requirement\n"
    "title: Percent\nproduct: calc\nstatus: proposed\nchange: chg-calc-1\n---\n\n"
    "# Percent\n\n- AC-1: pressing % divides by 100\n- AC-2: rounding is half up\n"
)

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
            {REQ: V1.encode(), INTENT: b"intent\n"},
            message="spec v1",
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
    test_client = TestClient(app)
    assert (
        test_client.post(f"{API}/products", json=PRODUCT_BODY, headers=OPERATOR).status_code == 201
    )
    assert test_client.post(f"{API}/changes", json=CHANGE_BODY, headers=OPERATOR).status_code == 201
    return test_client, repository, head


def test_questions_are_asked_by_the_agent_and_answered_by_the_operator(
    client: tuple[TestClient, FakeRepository, str],
) -> None:
    api, _repository, head = client
    body = {
        "text": "Round half up or half even?",
        "kind": "choice",
        "options": ["half up", "half even"],
        "anchor": {"artifact": REQ, "anchor_id": "AC-2", "revision": head},
        "phase": "requirements",
    }
    created = api.post(
        f"{API}/changes/{CHANGE_ID}/questions",
        json=body,
        headers={**AGENT, "Idempotency-Key": "k1"},
    )
    assert created.status_code == 201, created.text
    question = created.json()
    assert question["status"] == "open" and question["blocking"] is True
    replay = api.post(
        f"{API}/changes/{CHANGE_ID}/questions",
        json=body,
        headers={**AGENT, "Idempotency-Key": "k1"},
    )
    assert replay.status_code == 200 and replay.json()["id"] == question["id"]

    # The gate is closed while the blocking question is open, with the way out.
    gate = api.get(f"{API}/changes/{CHANGE_ID}/phase-gate", params={"phase": "requirements"}).json()
    assert gate["available"] is False and gate["blocking_questions"] == 1
    assert "factory change answer" in gate["reasons"][0]["how"]
    assert gate["current_revision"] == head

    # The agent may not answer; the operator may, and only with one of the options.
    forbidden = api.post(
        f"{API}/changes/{CHANGE_ID}/questions/{question['id']}/answer",
        json={"value": "half up"},
        headers=AGENT,
    )
    assert forbidden.status_code == 403
    invalid = api.post(
        f"{API}/changes/{CHANGE_ID}/questions/{question['id']}/answer",
        json={"value": "banker's"},
        headers=OPERATOR,
    )
    assert invalid.status_code == 422
    answered = api.post(
        f"{API}/changes/{CHANGE_ID}/questions/{question['id']}/answer",
        json={"value": "half up", "comment": "as in the shop"},
        headers=OPERATOR,
    )
    assert answered.status_code == 200, answered.text
    assert answered.json()["status"] == "answered"
    assert answered.json()["answer"]["answered_by"] == "alice"
    assert api.get(
        f"{API}/changes/{CHANGE_ID}/phase-gate", params={"phase": "requirements"}
    ).json()["available"]
    listed = api.get(f"{API}/changes/{CHANGE_ID}/questions", params={"status": "answered"}).json()
    assert [q["id"] for q in listed] == [question["id"]]


def test_artifact_tree_document_versions_and_write_through(
    client: tuple[TestClient, FakeRepository, str],
) -> None:
    api, repository, head = client
    tree = api.get(f"{API}/changes/{CHANGE_ID}/artifacts").json()
    assert tree["revision"] == head
    assert [(n["path"], n["kind"]) for n in tree["nodes"]] == [(INTENT, "spec"), (REQ, "spec")]

    document = api.get(f"{API}/changes/{CHANGE_ID}/artifacts/{REQ}").json()
    assert document["revision"] == head
    assert document["properties"]["values"]["title"] == "Percent"
    assert "AC-2" in document["anchors"]
    assert document["viewed"] is False and document["draft"] is None

    # A draft is autosaved outside git; the tree lists it; it never touches the branch.
    draft = api.put(
        f"{API}/changes/{CHANGE_ID}/artifact-drafts/{REQ}",
        json={"content": V1 + "- AC-3: draft\n", "base_revision": head},
        headers=OPERATOR,
    )
    assert draft.status_code == 200 and draft.json()["stale"] is False
    assert api.get(f"{API}/changes/{CHANGE_ID}/artifacts").json()["drafts"] == [REQ]
    assert asyncio.run(repository.get_revision(REPO, branch_name(CHANGE_ID))) == head

    # Viewing is recorded per revision and is not an approval.
    viewed = api.post(
        f"{API}/changes/{CHANGE_ID}/artifact-views/{REQ}", json={"revision": head}, headers=OPERATOR
    )
    assert viewed.status_code == 200 and viewed.json()["viewed"] is True
    assert (
        api.get(f"{API}/changes/{CHANGE_ID}/phase-gate", params={"phase": "requirements"}).json()[
            "approved"
        ]
        is False
    )

    # The explicit save becomes one commit; the draft is gone; properties are protected.
    protected = api.put(
        f"{API}/changes/{CHANGE_ID}/artifacts/{REQ}",
        json={"content": V1, "base_revision": head, "properties": {"id": "req:calc:other"}},
        headers=OPERATOR,
    )
    assert protected.status_code == 422
    written = api.put(
        f"{API}/changes/{CHANGE_ID}/artifacts/{REQ}",
        json={
            "content": V1.replace("- AC-2: rounding is half up\n", ""),
            "base_revision": head,
            "properties": {"status": "in_review"},
        },
        headers={**OPERATOR, "Idempotency-Key": "save-1"},
    )
    assert written.status_code == 200, written.text
    new_revision = written.json()["revision"]
    assert written.json()["created_commit"] is True and new_revision != head
    assert api.get(f"{API}/changes/{CHANGE_ID}/artifact-drafts/{REQ}").status_code == 404
    versions = api.get(f"{API}/changes/{CHANGE_ID}/artifact-versions/{REQ}").json()
    assert [v["revision"] for v in versions] == [new_revision, head]
    diff = api.get(
        f"{API}/changes/{CHANGE_ID}/artifact-diff/{REQ}",
        params={"from_revision": head, "to_revision": new_revision},
    ).json()
    assert diff["removed"] == 2, diff["unified"]
    assert "-- AC-2: rounding is half up" in diff["unified"]
    assert "-status: proposed" in diff["unified"] and "+status: in_review" in diff["unified"]
    updated = api.get(f"{API}/changes/{CHANGE_ID}/artifacts/{REQ}").json()
    assert updated["properties"]["values"]["status"] == "in_review"
    assert "AC-2" not in updated["anchors"]

    # A concurrent edit from the old revision of a changed file is a conflict.
    conflict = api.put(
        f"{API}/changes/{CHANGE_ID}/artifacts/{REQ}",
        json={"content": "something else", "base_revision": head},
        headers=OPERATOR,
    )
    assert conflict.status_code == 409
    assert api.get(f"{API}/changes/{CHANGE_ID}/artifacts/missing.md").status_code == 404


def test_a_new_revision_makes_lost_questions_stale_and_comments_detached(
    client: tuple[TestClient, FakeRepository, str],
) -> None:
    api, _repository, head = client
    question = api.post(
        f"{API}/changes/{CHANGE_ID}/questions",
        json={
            "text": "?",
            "anchor": {"artifact": REQ, "anchor_id": "AC-2"},
            "phase": "requirements",
        },
        headers=AGENT,
    ).json()
    kept = api.post(
        f"{API}/changes/{CHANGE_ID}/questions",
        json={
            "text": "?",
            "anchor": {"artifact": REQ, "anchor_id": "AC-1"},
            "phase": "requirements",
        },
        headers=AGENT,
    ).json()
    comment = api.post(
        f"{API}/changes/{CHANGE_ID}/comments",
        json={"artifact": REQ, "anchor_id": "AC-2", "body": "too vague", "phase": "requirements"},
        headers=OPERATOR,
    )
    assert comment.status_code == 201, comment.text
    assert comment.json()["anchor"]["revision"] == head, "the anchor is bound to the head"
    assert comment.json()["anchor_state"] == "attached"

    written = api.put(
        f"{API}/changes/{CHANGE_ID}/artifacts/{REQ}",
        json={"content": V1.replace("- AC-2: rounding is half up\n", ""), "base_revision": head},
        headers=OPERATOR,
    ).json()
    assert written["stale_questions"] == [question["id"]]
    assert written["detached_comments"] == [comment.json()["id"]]
    assert api.get(f"{API}/changes/{CHANGE_ID}/questions/").status_code in (200, 404, 307)
    questions = {
        q["id"]: q["status"] for q in api.get(f"{API}/changes/{CHANGE_ID}/questions").json()
    }
    assert questions == {question["id"]: "stale", kept["id"]: "open"}
    comments = api.get(f"{API}/changes/{CHANGE_ID}/comments").json()
    assert comments[0]["anchor_state"] == "detached"
    assert comments[0]["status"] == "open", "a detached comment is shown as such, never moved"


def test_comment_lifecycle_rework_order_and_version_bound_approval(
    client: tuple[TestClient, FakeRepository, str],
) -> None:
    api, _repository, head = client
    comment = api.post(
        f"{API}/changes/{CHANGE_ID}/comments",
        json={
            "artifact": REQ,
            "anchor_id": "AC-1",
            "body": "make it testable",
            "phase": "requirements",
        },
        headers=OPERATOR,
    ).json()
    # The agent's «исправлено» is addressed, not closed; only the operator closes.
    addressed = api.post(
        f"{API}/changes/{CHANGE_ID}/comments/{comment['id']}/addressed",
        json={"note": "rephrased"},
        headers=AGENT,
    )
    assert addressed.status_code == 200 and addressed.json()["status"] == "addressed"
    assert (
        api.post(
            f"{API}/changes/{CHANGE_ID}/comments/{comment['id']}/close", headers=AGENT
        ).status_code
        == 403
    )
    reopened = api.post(
        f"{API}/changes/{CHANGE_ID}/comments/{comment['id']}/reopen", headers=OPERATOR
    )
    assert reopened.json()["status"] == "open"

    # A comment alone does not start rework: the gate is still available.
    assert api.get(
        f"{API}/changes/{CHANGE_ID}/phase-gate", params={"phase": "requirements"}
    ).json()["available"]
    # The explicit send-back creates the order, records the rejected decision and closes the gate.
    order = api.post(
        f"{API}/changes/{CHANGE_ID}/rework-orders",
        json={
            "phase": "requirements",
            "comment_ids": [comment["id"]],
            "instruction": "tighten AC-1",
        },
        headers={**OPERATOR, "Idempotency-Key": "rw-1"},
    )
    assert order.status_code == 201, order.text
    assert order.json()["status"] == "pending" and order.json()["revisions"][REQ] == head
    assert (
        api.get(f"{API}/changes/{CHANGE_ID}/comments").json()[0]["rework_order_id"]
        == order.json()["id"]
    )
    decisions = api.get(f"{API}/changes/{CHANGE_ID}/approvals").json()
    assert [(d["outcome"], d["commit_sha"]) for d in decisions] == [("rejected", head)]
    second = api.post(
        f"{API}/changes/{CHANGE_ID}/rework-orders",
        json={"phase": "requirements", "instruction": "again"},
        headers=OPERATOR,
    )
    assert second.status_code == 409, "one round at a time"
    gate = api.get(f"{API}/changes/{CHANGE_ID}/phase-gate", params={"phase": "requirements"}).json()
    assert gate["available"] is False and gate["rework_pending"] is True
    # An approval while the gate is closed is refused with the reasons.
    refused = api.post(
        f"{API}/changes/{CHANGE_ID}/approvals",
        json={"gate": "specification", "outcome": "approved", "subject_revision": head},
        headers=OPERATOR,
    )
    assert refused.status_code == 409 and "раунд ещё не запущен" in refused.json()["detail"]
    unknown = api.post(
        f"{API}/changes/{CHANGE_ID}/rework-orders",
        json={"phase": "interface", "comment_ids": ["cmt_nope"]},
        headers=OPERATOR,
    )
    assert unknown.status_code == 404, "an unknown comment does not exist: 404, not a bad body"


def test_approval_is_version_bound_and_a_skip_needs_a_reason(
    client: tuple[TestClient, FakeRepository, str],
) -> None:
    api, _repository, head = client
    stale = api.post(
        f"{API}/changes/{CHANGE_ID}/approvals",
        json={"gate": "specification", "outcome": "approved", "subject_revision": "older"},
        headers=OPERATOR,
    )
    assert stale.status_code == 409 and "not the current revision" in stale.json()["detail"]
    approved = api.post(
        f"{API}/changes/{CHANGE_ID}/approvals",
        json={"gate": "specification", "outcome": "approved", "subject_revision": head},
        headers=OPERATOR,
    )
    assert approved.status_code == 201, approved.text
    gate = api.get(f"{API}/changes/{CHANGE_ID}/phase-gate", params={"phase": "requirements"}).json()
    assert gate["approved"] is True and gate["approvals"][0]["state"] == "current"
    # A new revision makes the approval stale — nothing is deleted, nothing is green.
    api.put(
        f"{API}/changes/{CHANGE_ID}/artifacts/{INTENT}",
        json={"content": "intent v2\n", "base_revision": head},
        headers=OPERATOR,
    )
    after = api.get(
        f"{API}/changes/{CHANGE_ID}/phase-gate", params={"phase": "requirements"}
    ).json()
    assert after["approved"] is False and after["approvals"][0]["state"] == "stale"
    waived = api.post(
        f"{API}/changes/{CHANGE_ID}/approvals",
        json={"gate": "ui", "outcome": "waived", "subject_revision": after["current_revision"]},
        headers=OPERATOR,
    )
    assert waived.status_code == 422, "a skipped phase needs its reason"
    with_reason = api.post(
        f"{API}/changes/{CHANGE_ID}/approvals",
        json={
            "gate": "ui",
            "outcome": "waived",
            "subject_revision": after["current_revision"],
            "comment": "backend-only change: no UI",
        },
        headers=OPERATOR,
    )
    assert with_reason.status_code == 201


def test_artifacts_answer_503_without_a_repository(session_factory: sessionmaker[Session]) -> None:
    api = TestClient(create_app(session_factory, tokens=_store()))
    api.post(f"{API}/products", json=PRODUCT_BODY, headers=OPERATOR)
    api.post(f"{API}/changes", json=CHANGE_BODY, headers=OPERATOR)
    assert api.get(f"{API}/changes/{CHANGE_ID}/artifacts").status_code == 503
    gate = api.get(f"{API}/changes/{CHANGE_ID}/phase-gate", params={"phase": "requirements"}).json()
    # Without a repository the revision is unknown, not absent: the gate does not
    # claim the artifacts are missing, and a decision binds to what the operator names.
    assert gate["current_revision"] is None and gate["available"] is True


def test_product_bootstrap_applies_the_baseline_packs(
    session_factory: sessionmaker[Session],
) -> None:
    provisioning = FakeRepositoryProvisioning()
    provisioning.seed(REPO, RepositoryState.EMPTY)
    api = TestClient(create_app(session_factory, tokens=_store(), provisioning=provisioning))
    api.post(f"{API}/products", json=PRODUCT_BODY, headers=OPERATOR)
    response = api.post(
        f"{API}/products/prd-calc/bootstrap", headers={**OPERATOR, "Idempotency-Key": "boot-1"}
    )
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["applied_packs"][0]["name"] == "product-baseline"
    replay = api.post(
        f"{API}/products/prd-calc/bootstrap", headers={**OPERATOR, "Idempotency-Key": "boot-1"}
    )
    assert replay.json()["result"]["revision"] == result["revision"]
    assert api.post(f"{API}/products/prd-missing/bootstrap", headers=OPERATOR).status_code == 404
    unconfigured = TestClient(create_app(session_factory, tokens=_store()))
    assert (
        unconfigured.post(f"{API}/products/prd-calc/bootstrap", headers=OPERATOR).status_code == 503
    )


# --- M3 (T098, ADR-039): the phase projection and phase-bound approvals -----------------

DESIGN = ".factory/changes/2026/CHG-0001-percent/design/overview.md"
OVERVIEW = (
    "---\nschema: dark-factory.dev/design/v1\nid: design:calc:percent\ntype: design\n"
    "title: Percent design\nproduct: calc\nstatus: proposed\nchange: chg-calc-1\n"
    "ui: not_required\nui_reason: backend-only change\n---\n\n## Overview\n\nNo UI.\n"
)


def test_phases_projection_and_phase_bound_approvals(
    client: tuple[TestClient, FakeRepository, str],
) -> None:
    api, _repository, head = client
    before = api.get(f"{API}/changes/{CHANGE_ID}/phases")
    assert before.status_code == 200, before.text
    projection = before.json()
    assert projection["current"] == "initiative", "no run yet: the intake"
    assert [p["phase"] for p in projection["phases"]] == [
        "initiative",
        "requirements",
        "architecture",
        "interface",
        "plan",
        "execution",
        "demonstration",
        "delivery",
    ]
    assert projection["phases"][1]["revision"] == head, "the requirements' own revision"
    assert projection["phases"][2]["revision"] is None, "no design artifacts yet"

    # An architecture approval must say so: the gate is shared with requirements.
    mismatch = api.post(
        f"{API}/changes/{CHANGE_ID}/approvals",
        json={
            "gate": "ui",
            "phase": "architecture",
            "outcome": "approved",
            "subject_revision": head,
        },
        headers=OPERATOR,
    )
    assert mismatch.status_code == 422
    approved = api.post(
        f"{API}/changes/{CHANGE_ID}/approvals",
        json={
            "gate": "specification",
            "phase": "requirements",
            "outcome": "approved",
            "subject_revision": head,
        },
        headers=OPERATOR,
    )
    assert approved.status_code == 201, approved.text
    assert approved.json()["phase"] == "requirements"

    # The architect's round lands design files: the requirements approval stays current,
    # because it binds to the revision of the requirements' own artifacts (ADR-039).
    written = api.put(
        f"{API}/changes/{CHANGE_ID}/artifacts/{DESIGN}",
        json={"content": OVERVIEW, "base_revision": head},
        headers=OPERATOR,
    )
    assert written.status_code == 200, written.text
    design_revision = written.json()["revision"]
    requirements = api.get(
        f"{API}/changes/{CHANGE_ID}/phase-gate", params={"phase": "requirements"}
    ).json()
    assert requirements["current_revision"] == head
    assert requirements["approved"] is True and requirements["approvals"][0]["state"] == "current"
    assert requirements["approvals"][0]["phase"] == "requirements"
    architecture = api.get(
        f"{API}/changes/{CHANGE_ID}/phase-gate", params={"phase": "architecture"}
    ).json()
    assert architecture["current_revision"] == design_revision
    assert architecture["approvals"] == [], "the requirements decision is not the architecture's"
    assert architecture["available"] is True

    # The interface gate reads the architect's proposal and offers the waiver (T097).
    interface = api.get(
        f"{API}/changes/{CHANGE_ID}/phase-gate", params={"phase": "interface"}
    ).json()
    assert interface["ui_requirement"] == {
        "required": False,
        "source": "agent",
        "reason": "backend-only change",
    }
    assert [c["status"] for c in interface["checks"]] == ["not_required", "not_required"]
    assert "backend-only change" in interface["reasons"][0]["what"]
    waived = api.post(
        f"{API}/changes/{CHANGE_ID}/approvals",
        json={
            "gate": "ui",
            "phase": "interface",
            "outcome": "waived",
            "comment": "backend-only change",
        },
        headers=OPERATOR,
    )
    assert waived.status_code == 201, waived.text
    assert waived.json()["commit_sha"] is None, "a waiver binds to no document"
    after = api.get(f"{API}/changes/{CHANGE_ID}/phase-gate", params={"phase": "interface"}).json()
    assert after["waived"] is True and after["ui_requirement"]["source"] == "operator"
    assert after["reasons"] == []
    phases = api.get(f"{API}/changes/{CHANGE_ID}/phases").json()
    by_phase = {p["phase"]: p for p in phases["phases"]}
    assert by_phase["requirements"]["approved_revision"] == head
    assert by_phase["interface"]["state"] == "pending", "no run: nothing is active yet"
    assert phases["current"] == "initiative"
