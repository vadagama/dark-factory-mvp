"""Domain invariants of the change model: statuses, immutability, keys, budgets (T-003)."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from dark_factory.changes import (
    RUN_STATUS_TRANSITIONS,
    RUN_TERMINAL_STATUSES,
    STAGE_STATUS_TRANSITIONS,
    STAGE_TERMINAL_STATUSES,
    BudgetSnapshot,
    InvalidStatusTransition,
    RunStatus,
    Stage,
    StageRun,
    StageStatus,
    Usage,
    attempt_id,
    completion_violations,
    effect_key,
    operation_key,
)
from dark_factory.changes.enums import FindingSeverity, FindingStatus
from tests.changes_factories import (
    make_evidence,
    make_finding,
    make_record,
    make_run,
    make_stage_result,
)


def test_change_run_defaults() -> None:
    run = make_run()
    assert run.status is RunStatus.PENDING
    assert run.state_revision == 1
    assert run.finished_at is None
    assert run.budget.max_rework_rounds == 3
    assert run.budget.used_rework_rounds == 0


def test_run_status_transitions_follow_the_table() -> None:
    run = make_run()
    run.apply_status(RunStatus.RUNNING)
    assert run.state_revision == 2
    run.apply_status(RunStatus.WAITING)
    run.apply_status(RunStatus.RUNNING)
    run.apply_status(RunStatus.BLOCKED)
    run.apply_status(RunStatus.RUNNING)
    run.apply_status(RunStatus.SUCCEEDED)
    assert run.status is RunStatus.SUCCEEDED
    assert run.finished_at is not None
    assert run.state_revision == 7


def test_run_status_rejects_invalid_and_terminal_transitions() -> None:
    run = make_run()
    with pytest.raises(InvalidStatusTransition):
        run.apply_status(RunStatus.SUCCEEDED)  # pending -> succeeded
    run.apply_status(RunStatus.RUNNING)
    run.apply_status(RunStatus.FAILED)
    with pytest.raises(InvalidStatusTransition):
        run.apply_status(RunStatus.RUNNING)  # failed is terminal


def test_stage_status_allows_retry_after_failure() -> None:
    stage = StageRun(id="stage-1", stage=Stage.CONSTRUCTION)
    stage.apply_status(StageStatus.IN_PROGRESS)
    stage.apply_status(StageStatus.FAILED)
    assert stage.finished_at is None  # a failed stage can be retried
    stage.apply_status(StageStatus.IN_PROGRESS)  # retry of the same operation
    stage.apply_status(StageStatus.SUCCEEDED)
    assert stage.finished_at is not None
    with pytest.raises(InvalidStatusTransition):
        stage.apply_status(StageStatus.IN_PROGRESS)  # succeeded is terminal


def test_stage_status_rejects_pending_to_succeeded() -> None:
    stage = StageRun(id="stage-1", stage=Stage.SPECIFICATION)
    with pytest.raises(InvalidStatusTransition):
        stage.apply_status(StageStatus.SUCCEEDED)


def test_transition_tables_are_total_with_final_terminals() -> None:
    assert set(RUN_STATUS_TRANSITIONS) == set(RunStatus)
    assert set(STAGE_STATUS_TRANSITIONS) == set(StageStatus)
    assert {
        RunStatus.SUCCEEDED,
        RunStatus.FAILED,
        RunStatus.CANCELED,
        RunStatus.SUPERSEDED,
    } == RUN_TERMINAL_STATUSES
    assert {
        StageStatus.SUCCEEDED,
        StageStatus.SKIPPED,
        StageStatus.SUPERSEDED,
        StageStatus.CANCELED,
    } == STAGE_TERMINAL_STATUSES
    for run_status in RUN_TERMINAL_STATUSES:
        assert RUN_STATUS_TRANSITIONS[run_status] == frozenset()
    for stage_status in STAGE_TERMINAL_STATUSES:
        assert STAGE_STATUS_TRANSITIONS[stage_status] == frozenset()


def test_stage_result_is_frozen() -> None:
    result = make_stage_result()
    with pytest.raises(ValidationError):
        result.status = StageStatus.FAILED  # type: ignore[misc]


def test_stage_result_rejects_non_result_statuses() -> None:
    for status in (StageStatus.PENDING, StageStatus.IN_PROGRESS, StageStatus.SKIPPED):
        with pytest.raises(ValidationError):
            make_stage_result(status=status)


def test_idempotency_keys_are_deterministic() -> None:
    key = operation_key("run-001", Stage.CONSTRUCTION, "731ac91")
    assert key == "run-001:construction:731ac91"
    assert attempt_id(key, 2) == "run-001:construction:731ac91:2"
    assert effect_key(key, "create_mr", "small/pilot#12") == (
        "run-001:construction:731ac91:create_mr:small/pilot#12"
    )


def test_budget_snapshot_flags() -> None:
    exhausted = BudgetSnapshot(
        max_rework_rounds=3,
        used_rework_rounds=3,
        token_budget=100,
        tokens_used=100,
        cost_budget=Decimal("10"),
        cost_used=Decimal("10"),
    )
    assert exhausted.rework_exhausted
    assert exhausted.rework_rounds_remaining == 0
    assert exhausted.token_budget_exhausted
    assert exhausted.cost_budget_exhausted

    open_budget = BudgetSnapshot(
        max_rework_rounds=3,
        used_rework_rounds=1,
        token_budget=100,
        tokens_used=10,
    )
    assert not open_budget.rework_exhausted
    assert open_budget.rework_rounds_remaining == 2
    assert not open_budget.token_budget_exhausted
    assert not open_budget.cost_budget_exhausted  # no cost budget set


def test_usage_rejects_negative_tokens() -> None:
    with pytest.raises(ValidationError):
        Usage(prompt_tokens=-1)


def test_completion_violations_accept_a_clean_success() -> None:
    record = make_record(
        stage_results=[
            make_stage_result(
                evidence=[make_evidence("ev-1", required=True)],
                findings=[
                    make_finding(severity=FindingSeverity.BLOCKER, status=FindingStatus.RESOLVED)
                ],
            )
        ]
    )
    assert completion_violations(record.run, record.stage_results) == []
    assert record.completion_violations() == []


def test_completion_violations_flag_unavailable_required_evidence() -> None:
    record = make_record(
        stage_results=[
            make_stage_result(
                evidence=[
                    make_evidence("ev-1", required=True, available=False),
                    make_evidence("ev-2", required=False, available=False),
                ]
            )
        ]
    )
    violations = completion_violations(record.run, record.stage_results)
    assert len(violations) == 1
    assert "ev-1" in violations[0]


def test_completion_violations_flag_open_blocker_findings() -> None:
    record = make_record(
        stage_results=[
            make_stage_result(
                findings=[
                    make_finding(severity=FindingSeverity.BLOCKER, status=FindingStatus.OPEN),
                    make_finding(
                        "f-2",
                        severity=FindingSeverity.MINOR,
                        status=FindingStatus.OPEN,
                    ),
                ]
            )
        ]
    )
    violations = completion_violations(record.run, record.stage_results)
    assert len(violations) == 1
    assert "f-1" in violations[0]


def test_completion_violations_only_gate_success() -> None:
    record = make_record(
        run_status=RunStatus.FAILED,
        stage_results=[
            make_stage_result(
                evidence=[make_evidence("ev-1", required=True, available=False)],
                findings=[make_finding(severity=FindingSeverity.BLOCKER)],
            )
        ],
    )
    assert completion_violations(record.run, record.stage_results) == []
