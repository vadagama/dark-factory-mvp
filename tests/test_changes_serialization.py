"""JSON/YAML round-trips of run records and stage results (T-003 DoD)."""

from decimal import Decimal

import pytest
from pydantic import TypeAdapter, ValidationError

from dark_factory.changes import (
    EvidenceType,
    ExecuteStageAction,
    Gate,
    GateStatus,
    MergeAction,
    NextAction,
    Provider,
    ReleaseAction,
    RequestApprovalAction,
    ReworkAction,
    Role,
    RunRecord,
    Stage,
    StageResult,
    StopAction,
    StopOutcome,
    WaitForCIAction,
    WaitForInputAction,
    from_json,
    from_yaml,
    to_json,
    to_yaml,
)
from tests.changes_factories import (
    NOW,
    make_change_request,
    make_decision,
    make_evidence,
    make_finding,
    make_gate_result,
    make_record,
    make_stage_result,
)

ACTIONS: list[NextAction] = [
    ExecuteStageAction(next_stage=Stage.REVIEW_VERIFICATION),
    WaitForInputAction(reason="need a product decision"),
    WaitForCIAction(reason="pipeline is running", change_request=make_change_request()),
    ReworkAction(round=2, max_rounds=3, reason="blocker findings remain"),
    RequestApprovalAction(gate=Gate.CODE, requested_from=Role.ARCHITECT),
    MergeAction(change_request=make_change_request()),
    ReleaseAction(target_environment="dev"),
    StopAction(outcome=StopOutcome.BLOCKED, reason="rework limit exhausted"),
]
ACTION_IDS = [
    "execute_stage",
    "wait_for_input",
    "wait_for_ci",
    "rework",
    "request_approval",
    "merge",
    "release",
    "stop",
]


def _rich_record() -> RunRecord:
    record = make_record(
        stage_results=[
            make_stage_result(
                evidence=[
                    make_evidence("ev-1", required=True),
                    make_evidence("ev-2", required=False, available=False),
                ],
                findings=[make_finding()],
                gate_results=[make_gate_result()],
            )
        ]
    )
    record.decisions.append(make_decision())
    return record


@pytest.mark.parametrize("action", ACTIONS, ids=ACTION_IDS)
def test_next_action_round_trips_in_a_stage_result(action: NextAction) -> None:
    result = make_stage_result(next_action=action)
    json_restored = from_json(StageResult, to_json(result))
    yaml_restored = from_yaml(StageResult, to_yaml(result))
    assert json_restored == result
    assert yaml_restored == result
    assert type(json_restored.next_action) is type(action)
    assert type(yaml_restored.next_action) is type(action)


def test_run_record_round_trips_through_json() -> None:
    record = _rich_record()
    restored = from_json(RunRecord, to_json(record))
    assert restored == record
    assert isinstance(restored.run.budget.cost_used, Decimal)
    assert restored.run.budget.cost_used == Decimal("0.75")
    assert restored.run.created_at == NOW
    assert restored.change.product.provider is Provider.GITHUB
    assert restored.stage_results[0].evidence[1].type is EvidenceType.REPORT


def test_run_record_round_trips_through_yaml() -> None:
    record = _rich_record()
    restored = from_yaml(RunRecord, to_yaml(record))
    assert restored == record
    assert isinstance(restored.run.budget.cost_used, Decimal)
    assert restored.stage_results[0].next_action == record.stage_results[0].next_action
    assert restored.completion_violations() == []


def test_yaml_uses_wire_values_and_iso_dates() -> None:
    text = to_yaml(make_record())
    assert "provider: github" in text
    assert "status: succeeded" in text
    assert "2026-09-13T12:00:00Z" in text


def test_serialization_rejects_unknown_next_action_type() -> None:
    adapter: TypeAdapter[NextAction] = TypeAdapter(NextAction)
    with pytest.raises(ValidationError):
        adapter.validate_python({"type": "teleport", "reason": "unknown action"})


def test_stage_result_rejects_unknown_next_action_type() -> None:
    payload = {
        "stage": "construction",
        "run_id": "run-001",
        "change_id": "chg-001",
        "status": "succeeded",
        "next_action": {"type": "teleport"},
        "produced_at": "2026-09-13T12:00:00+00:00",
    }
    with pytest.raises(ValidationError):
        StageResult.model_validate(payload)


def test_gate_result_status_survives_round_trip() -> None:
    result = make_stage_result(gate_results=[make_gate_result()])
    restored = from_json(StageResult, to_json(result))
    assert restored.gate_results[0].status is GateStatus.PASSED
