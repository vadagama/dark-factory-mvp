"""Contract tests for WorkflowEnginePort."""

import asyncio

import pytest

from dark_factory.ports import RunNotFoundError, RunStatus, WorkflowEnginePort


def test_adapter_satisfies_protocol(workflow_engine_port: WorkflowEnginePort) -> None:
    assert isinstance(workflow_engine_port, WorkflowEnginePort)


def test_start_mints_a_running_run(workflow_engine_port: WorkflowEnginePort) -> None:
    run_id = asyncio.run(workflow_engine_port.start(idempotency_key="k1"))
    assert run_id != ""
    assert asyncio.run(workflow_engine_port.get_status(run_id)) is RunStatus.RUNNING


def test_start_replays_same_key_to_same_run(workflow_engine_port: WorkflowEnginePort) -> None:
    first = asyncio.run(workflow_engine_port.start(idempotency_key="k1"))
    second = asyncio.run(workflow_engine_port.start(idempotency_key="k1"))
    assert second == first


def test_start_different_keys_mint_different_runs(
    workflow_engine_port: WorkflowEnginePort,
) -> None:
    first = asyncio.run(workflow_engine_port.start(idempotency_key="k1"))
    second = asyncio.run(workflow_engine_port.start(idempotency_key="k2"))
    assert second != first


def test_resume_moves_waiting_run_to_running(
    workflow_engine_port: WorkflowEnginePort, waiting_run: str
) -> None:
    assert asyncio.run(workflow_engine_port.get_status(waiting_run)) is RunStatus.WAITING
    assert (
        asyncio.run(workflow_engine_port.resume(waiting_run, idempotency_key="r1")) == waiting_run
    )
    assert asyncio.run(workflow_engine_port.get_status(waiting_run)) is RunStatus.RUNNING


def test_resume_replay_is_idempotent(
    workflow_engine_port: WorkflowEnginePort, waiting_run: str
) -> None:
    asyncio.run(workflow_engine_port.resume(waiting_run, idempotency_key="r1"))
    assert (
        asyncio.run(workflow_engine_port.resume(waiting_run, idempotency_key="r1")) == waiting_run
    )
    assert asyncio.run(workflow_engine_port.get_status(waiting_run)) is RunStatus.RUNNING


def test_resume_unknown_run_raises(workflow_engine_port: WorkflowEnginePort) -> None:
    with pytest.raises(RunNotFoundError):
        asyncio.run(workflow_engine_port.resume("no-such-run", idempotency_key="r1"))


def test_cancel_moves_run_to_canceled(workflow_engine_port: WorkflowEnginePort) -> None:
    run_id = asyncio.run(workflow_engine_port.start(idempotency_key="k1"))
    asyncio.run(workflow_engine_port.cancel(run_id, idempotency_key="c1", reason="test"))
    assert asyncio.run(workflow_engine_port.get_status(run_id)) is RunStatus.CANCELED


def test_cancel_replay_is_a_no_op(workflow_engine_port: WorkflowEnginePort) -> None:
    run_id = asyncio.run(workflow_engine_port.start(idempotency_key="k1"))
    asyncio.run(workflow_engine_port.cancel(run_id, idempotency_key="c1", reason="test"))
    asyncio.run(workflow_engine_port.cancel(run_id, idempotency_key="c1", reason="test"))
    assert asyncio.run(workflow_engine_port.get_status(run_id)) is RunStatus.CANCELED


def test_get_status_unknown_run_raises(workflow_engine_port: WorkflowEnginePort) -> None:
    with pytest.raises(RunNotFoundError):
        asyncio.run(workflow_engine_port.get_status("no-such-run"))
