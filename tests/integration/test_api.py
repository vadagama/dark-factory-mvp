"""Integration tests of the factory API against PostgreSQL (T035, contract api.md).

Requires ``DARK_FACTORY_TEST_DATABASE_URL`` (the shared fixtures build the
schema through the real Alembic migration); skipped without it. Every test
runs against a truncated schema; changes, runs, stage results and usage rows
are seeded through the repositories and the ORM to keep the API observations
deterministic.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from dark_factory.api.app import create_app
from dark_factory.api.auth import ApiToken, ApiTokenStore
from dark_factory.changes.enums import (
    Provider,
    RiskClass,
    Route,
    RunStatus,
    Stage,
    StageStatus,
)
from dark_factory.changes.refs import RepositoryRef
from dark_factory.changes.run import Change, StageResult
from dark_factory.orchestration.state.change_store import (
    APPROVAL_RECORD_ACTION,
    CHANGE_INTAKE_ACTION,
    ChangeRepository,
)
from dark_factory.orchestration.state.engine import session_scope
from dark_factory.orchestration.state.models import (
    AuditLogEntry,
    Execution,
    UsageRecord,
)
from dark_factory.orchestration.state.models import (
    Change as ChangeRow,
)
from dark_factory.orchestration.state.models import (
    Decision as DecisionRow,
)
from dark_factory.orchestration.state.models import (
    Stage as StageRow,
)
from dark_factory.orchestration.state.repositories import ExecutionRepository
from dark_factory.orchestration.state.run_store import WITHDRAW_ACTION
from dark_factory.orchestration.state.stage_results import StageResultRepository

OPERATOR = {"Authorization": "Bearer op-token"}
SERVICE = {"Authorization": "Bearer svc-token"}
API = "/api/v1"

_BASE = datetime(2026, 9, 14, 12, 0, 0, tzinfo=UTC)

_CHANGE_BODY: dict[str, Any] = {
    "id": "chg-1",
    "title": "Add a health endpoint",
    "source": "tracker",
    "product": {"provider": "github", "slug": "org/repo"},
    "risk_class": "R1",
    "external_ref": "PLANE-7",
}


def _client(session_factory: sessionmaker[Session]) -> TestClient:
    store = ApiTokenStore(
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
        ]
    )
    return TestClient(create_app(session_factory, tokens=store))


def _change(change_id: str, external_ref: str | None = None) -> Change:
    return Change(
        id=change_id,
        title="Add a health endpoint",
        source="tracker",
        external_ref=external_ref,
        product=RepositoryRef(provider="github", slug="org/repo"),
        risk_class=RiskClass.R1,
    )


def _result(
    run_id: str,
    change_id: str,
    stage: Stage,
    produced_at: datetime,
    *,
    attempt_number: int = 1,
    input_revision: str | None = "rev-1",
    status: StageStatus = StageStatus.SUCCEEDED,
    gate_results: list[dict[str, Any]] | None = None,
    findings: list[dict[str, Any]] | None = None,
    evidence: list[dict[str, Any]] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
) -> StageResult:
    return StageResult(
        stage=stage,
        run_id=run_id,
        change_id=change_id,
        attempt_number=attempt_number,
        input_revision=input_revision,
        status=status,
        next_action={"type": "execute_stage", "next_stage": Stage.PLANNING.value},
        gate_results=gate_results or [],
        findings=findings or [],
        evidence=evidence or [],
        artifacts=artifacts or [],
        produced_at=produced_at,
    )


def _seed_change(session_factory: sessionmaker[Session], change_id: str) -> None:
    with session_scope(session_factory) as session:
        ChangeRepository(session).create(_change(change_id))


def _seed_run(
    session_factory: sessionmaker[Session],
    run_id: str,
    change_id: str,
    status: RunStatus = RunStatus.RUNNING,
) -> None:
    with session_scope(session_factory) as session:
        ExecutionRepository(session).create(
            execution_id=run_id,
            change_id=change_id,
            route=Route.QUICK,
            provider=Provider.GITHUB,
        )
        if status is not RunStatus.PENDING:
            row = session.get(Execution, run_id)
            assert row is not None
            row.status = status.value
            row.state_revision = 2


def _seed_stage_result(session_factory: sessionmaker[Session], result: StageResult) -> bool:
    with session_scope(session_factory) as session:
        return StageResultRepository(session).record(result)


def _seed_stage(
    session_factory: sessionmaker[Session],
    run_id: str,
    stage: Stage,
    status: StageStatus,
) -> None:
    """Seed one logical stage operation of a run, without driving the flow."""
    with session_scope(session_factory) as session:
        row = ExecutionRepository(session).get_or_create_stage(
            execution_id=run_id, stage=stage, input_revision="rev-1"
        )
        row.status = status.value


def _run_state(session_factory: sessionmaker[Session], run_id: str) -> tuple[str, int]:
    with session_scope(session_factory) as session:
        row = session.get(Execution, run_id)
        assert row is not None
        return row.status, row.state_revision


def _stage_status(session_factory: sessionmaker[Session], run_id: str, stage: Stage) -> str:
    with session_scope(session_factory) as session:
        row = session.execute(
            select(StageRow).where(StageRow.execution_id == run_id, StageRow.stage == stage.value)
        ).scalar_one()
        return str(row.status)


def _audit_outcomes(session_factory: sessionmaker[Session], action: str) -> list[str]:
    with session_scope(session_factory) as session:
        rows = session.execute(
            select(AuditLogEntry.outcome)
            .where(AuditLogEntry.action == action)
            .order_by(AuditLogEntry.occurred_at, AuditLogEntry.id)
        ).scalars()
        return list(rows)


def _change_count(session_factory: sessionmaker[Session]) -> int:
    with session_scope(session_factory) as session:
        rows = session.execute(select(ChangeRow)).scalars()
        return len(list(rows))


def _decision_count(session_factory: sessionmaker[Session]) -> int:
    with session_scope(session_factory) as session:
        rows = session.execute(select(DecisionRow)).scalars()
        return len(list(rows))


def _state_revision(session_factory: sessionmaker[Session], change_id: str) -> int:
    with session_scope(session_factory) as session:
        row = ChangeRepository(session).get_raw(change_id)
        assert row is not None
        return row.state_revision


def test_change_intake_create_replay_and_external_ref_dedup(
    session_factory: sessionmaker[Session],
) -> None:
    client = _client(session_factory)
    headers = {**OPERATOR, "Idempotency-Key": "idem-1"}

    created = client.post(f"{API}/changes", json=_CHANGE_BODY, headers=headers)
    assert created.status_code == 201
    assert created.json()["id"] == "chg-1"

    replayed = client.post(f"{API}/changes", json=_CHANGE_BODY, headers=headers)
    assert replayed.status_code == 200
    assert replayed.json() == created.json()

    deduped = client.post(f"{API}/changes", json={**_CHANGE_BODY, "id": "chg-2"}, headers=OPERATOR)
    assert deduped.status_code == 200
    assert deduped.json()["id"] == "chg-1"

    assert _change_count(session_factory) == 1
    assert _audit_outcomes(session_factory, CHANGE_INTAKE_ACTION) == [
        "created",
        "replayed",
        "replayed",
    ]


def test_change_intake_without_token_is_401_and_service_role_is_allowed(
    session_factory: sessionmaker[Session],
) -> None:
    client = _client(session_factory)
    body = {key: value for key, value in _CHANGE_BODY.items() if key != "external_ref"}

    assert client.post(f"{API}/changes", json=body).status_code == 401

    response = client.post(f"{API}/changes", json=body, headers=SERVICE)
    assert response.status_code == 201
    assert response.json()["source"] == "tracker"


def test_change_card_and_404(session_factory: sessionmaker[Session]) -> None:
    _seed_change(session_factory, "chg-1")
    _seed_run(session_factory, "run-1", "chg-1")
    client = _client(session_factory)

    card = client.get(f"{API}/changes/chg-1")
    assert card.status_code == 200
    payload = card.json()
    assert payload["id"] == "chg-1"
    assert payload["runs"] == [{"run_id": "run-1", "status": "running"}]
    assert payload["decisions_count"] == 0

    assert client.get(f"{API}/changes/chg-1/trace").status_code == 200
    missing = client.get(f"{API}/changes/unknown")
    assert missing.status_code == 404
    assert missing.json()["type"] == "about:blank"
    assert client.get(f"{API}/changes/unknown/trace").status_code == 404


def test_approval_flow_idempotency_and_conflict(session_factory: sessionmaker[Session]) -> None:
    _seed_change(session_factory, "chg-1")
    client = _client(session_factory)
    body = {"gate": "code", "outcome": "approved", "subject_revision": "abc123"}

    created = client.post(
        f"{API}/changes/chg-1/approvals",
        json=body,
        headers={**OPERATOR, "Idempotency-Key": "appr-1"},
    )
    assert created.status_code == 201
    decision = created.json()
    assert decision["commit_sha"] == "abc123"
    assert decision["decided_by"] == "human"
    assert decision["id"].startswith("dec_")
    assert decision["gate"] == "code"
    assert decision["outcome"] == "approved"
    assert _state_revision(session_factory, "chg-1") == 2
    assert _audit_outcomes(session_factory, APPROVAL_RECORD_ACTION) == ["created"]

    replayed = client.post(
        f"{API}/changes/chg-1/approvals",
        json=body,
        headers={**OPERATOR, "Idempotency-Key": "appr-1"},
    )
    assert replayed.status_code == 200
    assert replayed.json()["id"] == decision["id"]
    assert _decision_count(session_factory) == 1
    assert _audit_outcomes(session_factory, APPROVAL_RECORD_ACTION) == ["created", "replayed"]
    assert _state_revision(session_factory, "chg-1") == 2  # a replay is not a second effect

    conflict = client.post(
        f"{API}/changes/chg-1/approvals",
        json={**body, "expected_state_revision": 99},
        headers=OPERATOR,
    )
    assert conflict.status_code == 409
    assert conflict.json() == {
        "type": "about:blank",
        "title": "Conflict",
        "status": 409,
        "detail": "state_revision mismatch",
    }
    assert _decision_count(session_factory) == 1  # nothing recorded on conflict
    assert _state_revision(session_factory, "chg-1") == 2

    second = client.post(
        f"{API}/changes/chg-1/approvals",
        json={**body, "gate": "release"},
        headers={**OPERATOR, "Idempotency-Key": "appr-2"},
    )
    assert second.status_code == 201
    assert _state_revision(session_factory, "chg-1") == 3

    approvals = client.get(f"{API}/changes/chg-1/approvals")
    assert approvals.status_code == 200
    assert [item["gate"] for item in approvals.json()] == ["code", "release"]


def test_approval_on_unknown_change_is_404(session_factory: sessionmaker[Session]) -> None:
    client = _client(session_factory)
    response = client.post(
        f"{API}/changes/unknown/approvals",
        json={"gate": "code", "outcome": "approved", "subject_revision": "abc123"},
        headers=OPERATOR,
    )
    assert response.status_code == 404


def test_run_card_aggregates_gates_findings_evidence_usage(
    session_factory: sessionmaker[Session],
) -> None:
    _seed_change(session_factory, "chg-1")
    _seed_run(session_factory, "run-1", "chg-1")
    with session_scope(session_factory) as session:
        session.add(
            UsageRecord(
                id="u-1",
                execution_id="run-1",
                stage="construction",
                prompt_tokens=100,
                completion_tokens=40,
                total_tokens=140,
                manual_interventions=1,
            )
        )
        session.add(
            UsageRecord(
                id="u-2",
                execution_id="run-1",
                stage="review_verification",
                prompt_tokens=50,
                completion_tokens=10,
                total_tokens=60,
            )
        )
    first = _result(
        "run-1",
        "chg-1",
        Stage.CONSTRUCTION,
        _BASE,
        gate_results=[{"gate": "code", "status": "passed", "sha": "sha-A"}],
        findings=[{"id": "F-1", "origin": "agent", "severity": "blocker", "status": "open"}],
        evidence=[
            {
                "id": "ev-1",
                "type": "log",
                "uri": "s3://evidence/1",
                "required": True,
                "available": True,
            }
        ],
    )
    second = _result(
        "run-1",
        "chg-1",
        Stage.CONSTRUCTION,
        _BASE + timedelta(minutes=5),
        attempt_number=2,
        input_revision="rev-2",
        gate_results=[{"gate": "code", "status": "failed", "sha": "sha-B"}],
        findings=[{"id": "F-2", "origin": "ci", "severity": "minor", "status": "resolved"}],
        evidence=[
            {
                "id": "ev-1",
                "type": "log",
                "uri": "s3://evidence/2",
                "required": True,
                "available": True,
            },
            {
                "id": "ev-2",
                "type": "test_results",
                "uri": "s3://evidence/3",
                "required": True,
                "available": False,
            },
        ],
    )
    assert _seed_stage_result(session_factory, first)
    assert not _seed_stage_result(
        session_factory, first
    )  # FR-014: the attempt is never overwritten
    assert _seed_stage_result(session_factory, second)

    client = _client(session_factory)
    card = client.get(f"{API}/runs/run-1")
    assert card.status_code == 200
    payload = card.json()
    assert payload["run_id"] == "run-1"
    assert payload["change_id"] == "chg-1"
    assert payload["status"] == "running"
    assert payload["usage"] == {
        "prompt_tokens": 150,
        "completion_tokens": 50,
        "cost": None,
        "manual_interventions": 1,
    }
    assert payload["open_blockers"] == 1
    assert payload["gates"] == [
        {"gate": "code", "status": "failed", "sha": "sha-B", "summary": None, "evidence_ids": []}
    ]

    gates = client.get(f"{API}/runs/run-1/gates")
    assert gates.status_code == 200
    assert gates.json()[0]["status"] == "failed"
    assert gates.json()[0]["sha"] == "sha-B"  # the last result per gate wins

    findings = client.get(f"{API}/runs/run-1/findings")
    assert [item["id"] for item in findings.json()] == ["F-1", "F-2"]
    blocker = client.get(f"{API}/runs/run-1/findings?severity=blocker")
    assert [item["id"] for item in blocker.json()] == ["F-1"]
    resolved = client.get(f"{API}/runs/run-1/findings?status=resolved")
    assert [item["id"] for item in resolved.json()] == ["F-2"]
    assert client.get(f"{API}/runs/run-1/findings?severity=info&status=open").json() == []

    evidence = client.get(f"{API}/runs/run-1/evidence")
    assert [item["id"] for item in evidence.json()] == ["ev-1", "ev-2"]
    by_id = client.get(f"{API}/runs/run-1/evidence/ev-1")
    assert by_id.status_code == 200
    assert by_id.json()["uri"] == "s3://evidence/2"  # later attempt wins
    assert client.get(f"{API}/runs/run-1/evidence/unknown").status_code == 404

    results = client.get(f"{API}/runs/run-1/stage-results")
    assert results.status_code == 200
    assert len(results.json()) == 2

    trace = client.get(f"{API}/runs/run-1/trace")
    assert trace.status_code == 200
    assert [item["stage"] for item in trace.json()["chain"]] == ["construction"]

    assert client.get(f"{API}/runs/unknown").status_code == 404
    assert client.get(f"{API}/runs/unknown/trace").status_code == 404
    assert client.get(f"{API}/runs/unknown/stage-results").status_code == 404
    assert client.get(f"{API}/runs/unknown/gates").status_code == 404
    assert client.get(f"{API}/runs/unknown/findings").status_code == 404
    assert client.get(f"{API}/runs/unknown/evidence").status_code == 404


def test_trace_chain_follows_canonical_stage_order(session_factory: sessionmaker[Session]) -> None:
    _seed_change(session_factory, "chg-1")
    _seed_run(session_factory, "run-1", "chg-1")
    assert _seed_stage_result(
        session_factory,
        _result(
            "run-1",
            "chg-1",
            Stage.RELEASE,
            _BASE,
            artifacts=[{"artifact_type": "image", "uri": "registry/img:1", "revision": "r1"}],
        ),
    )
    assert _seed_stage_result(
        session_factory,
        _result("run-1", "chg-1", Stage.SPECIFICATION, _BASE + timedelta(minutes=1)),
    )
    client = _client(session_factory)
    chain = client.get(f"{API}/runs/run-1/trace").json()["chain"]
    assert [item["stage"] for item in chain] == ["specification", "release"]
    assert chain[1]["artifacts"][0]["uri"] == "registry/img:1"


def test_runs_list_filters(session_factory: sessionmaker[Session]) -> None:
    _seed_change(session_factory, "chg-1")
    _seed_change(session_factory, "chg-2")
    _seed_run(session_factory, "run-1", "chg-1", RunStatus.RUNNING)
    _seed_run(session_factory, "run-2", "chg-1", RunStatus.SUCCEEDED)
    _seed_run(session_factory, "run-3", "chg-2", RunStatus.RUNNING)
    with session_scope(session_factory) as session:
        repo = ExecutionRepository(session)
        repo.get_or_create_stage(
            execution_id="run-1", stage=Stage.CONSTRUCTION, input_revision="rev-1"
        )
        repo.get_or_create_stage(execution_id="run-2", stage=Stage.RELEASE, input_revision="rev-1")
    client = _client(session_factory)

    runs = client.get(f"{API}/runs")
    assert runs.status_code == 200
    assert [item["run_id"] for item in runs.json()] == ["run-1", "run-2", "run-3"]

    by_change = client.get(f"{API}/runs?change_id=chg-1")
    assert [item["run_id"] for item in by_change.json()] == ["run-1", "run-2"]
    by_status = client.get(f"{API}/runs?status=running")
    assert [item["run_id"] for item in by_status.json()] == ["run-1", "run-3"]
    by_stage = client.get(f"{API}/runs?stage=construction")
    assert [item["run_id"] for item in by_stage.json()] == ["run-1"]
    paged = client.get(f"{API}/runs?limit=1&offset=1")
    assert [item["run_id"] for item in paged.json()] == ["run-2"]

    assert client.get(f"{API}/runs?status=bogus").status_code == 422
    assert client.get(f"{API}/runs?limit=0").status_code == 422
    assert client.get(f"{API}/runs?limit=201").status_code == 422


def test_change_trace_includes_all_runs(session_factory: sessionmaker[Session]) -> None:
    _seed_change(session_factory, "chg-1")
    _seed_run(session_factory, "run-1", "chg-1")
    _seed_run(session_factory, "run-2", "chg-1", RunStatus.SUCCEEDED)
    assert _seed_stage_result(
        session_factory, _result("run-1", "chg-1", Stage.SPECIFICATION, _BASE)
    )
    assert _seed_stage_result(
        session_factory, _result("run-2", "chg-1", Stage.RELEASE, _BASE + timedelta(minutes=2))
    )
    client = _client(session_factory)
    trace = client.get(f"{API}/changes/chg-1/trace")
    assert trace.status_code == 200
    payload = trace.json()
    assert [run["run_id"] for run in payload["runs"]] == ["run-1", "run-2"]
    assert payload["runs"][0]["chain"][0]["stage"] == "specification"
    assert payload["runs"][1]["chain"][0]["stage"] == "release"


def test_stage_result_is_idempotent_by_attempt_id(session_factory: sessionmaker[Session]) -> None:
    _seed_change(session_factory, "chg-1")
    _seed_run(session_factory, "run-1", "chg-1")
    result = _result("run-1", "chg-1", Stage.CONSTRUCTION, _BASE)
    assert _seed_stage_result(session_factory, result)
    overwrite = _result("run-1", "chg-1", Stage.CONSTRUCTION, _BASE, status=StageStatus.FAILED)
    assert not _seed_stage_result(session_factory, overwrite)
    with session_scope(session_factory) as session:
        stored = StageResultRepository(session).list_for_run("run-1")
    assert len(stored) == 1
    assert stored[0].status is StageStatus.SUCCEEDED


def test_run_withdraw_cancels_the_run_and_replays_idempotently(
    session_factory: sessionmaker[Session],
) -> None:
    """``POST /runs/{id}/withdraw`` (T064, TD-030): a terminal operator decision."""
    _seed_change(session_factory, "chg-1")
    _seed_run(session_factory, "run-1", "chg-1")
    _seed_stage(session_factory, "run-1", Stage.SPECIFICATION, StageStatus.WAITING)
    client = _client(session_factory)
    headers = {**OPERATOR, "Idempotency-Key": "wd-1"}

    created = client.post(f"{API}/runs/run-1/withdraw", headers=headers)
    assert created.status_code == 200
    body = created.json()
    assert body["status"] == "canceled"
    assert body["finished_at"] is not None
    assert _run_state(session_factory, "run-1") == ("canceled", 3)  # seeded revision 2 + 1
    assert _stage_status(session_factory, "run-1", Stage.SPECIFICATION) == "canceled"
    assert _audit_outcomes(session_factory, WITHDRAW_ACTION) == ["created"]

    replayed = client.post(f"{API}/runs/run-1/withdraw", headers=headers)
    assert replayed.status_code == 200
    assert replayed.json() == body
    # The repeat moved neither the run nor the stage; only the replay was recorded.
    assert _run_state(session_factory, "run-1") == ("canceled", 3)
    assert _audit_outcomes(session_factory, WITHDRAW_ACTION) == ["created", "replayed"]


def test_run_withdraw_of_an_unknown_run_is_404(
    session_factory: sessionmaker[Session],
) -> None:
    client = _client(session_factory)

    response = client.post(f"{API}/runs/unknown/withdraw", headers=OPERATOR)

    assert response.status_code == 404
    assert response.json() == {
        "type": "about:blank",
        "title": "Not Found",
        "status": 404,
        "detail": "Run 'unknown' does not exist",
    }
    assert _audit_outcomes(session_factory, WITHDRAW_ACTION) == []


def test_run_withdraw_of_a_finished_run_is_409(session_factory: sessionmaker[Session]) -> None:
    """A finished run is never resurrected or reclassified by a withdrawal."""
    _seed_change(session_factory, "chg-1")
    _seed_run(session_factory, "run-1", "chg-1", RunStatus.SUCCEEDED)
    client = _client(session_factory)

    response = client.post(f"{API}/runs/run-1/withdraw", headers=OPERATOR)

    assert response.status_code == 409
    payload = response.json()
    assert payload["status"] == 409
    assert "is terminal" in payload["detail"]
    assert _run_state(session_factory, "run-1") == ("succeeded", 2)
    assert _audit_outcomes(session_factory, WITHDRAW_ACTION) == []
