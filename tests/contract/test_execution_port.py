"""Contract tests for ExecutionPort."""

import asyncio
from hashlib import sha256

from dark_factory.ports import (
    ExecutionPort,
    ExecutionResult,
    Provider,
    RepositoryRef,
    WorkspaceHandle,
    WorkspaceRequest,
)

PRODUCT = RepositoryRef(provider=Provider.GITHUB, slug="small/pilot")


def _request() -> WorkspaceRequest:
    return WorkspaceRequest(repository=PRODUCT, revision="abc123", change_id="chg-001")


def test_adapter_satisfies_protocol(execution_port: ExecutionPort) -> None:
    assert isinstance(execution_port, ExecutionPort)


def test_prepare_workspace_returns_pinned_handle(execution_port: ExecutionPort) -> None:
    handle = asyncio.run(execution_port.prepare_workspace(_request(), idempotency_key="ws-1"))
    assert isinstance(handle, WorkspaceHandle)
    assert handle.workspace_id != ""
    assert handle.repository == PRODUCT
    assert handle.revision == "abc123"


def test_prepare_workspace_replays_same_key_to_same_handle(
    execution_port: ExecutionPort,
) -> None:
    first = asyncio.run(execution_port.prepare_workspace(_request(), idempotency_key="ws-1"))
    second = asyncio.run(execution_port.prepare_workspace(_request(), idempotency_key="ws-1"))
    assert second == first


def test_run_command_is_deterministic(execution_port: ExecutionPort) -> None:
    handle = asyncio.run(execution_port.prepare_workspace(_request(), idempotency_key="ws-1"))
    first = asyncio.run(
        execution_port.run_command(handle, ("pytest", "-q"), idempotency_key="cmd-1")
    )
    second = asyncio.run(
        execution_port.run_command(handle, ("pytest", "-q"), idempotency_key="cmd-2")
    )
    assert second == first  # the result depends on argv only, not on the key
    assert isinstance(first, ExecutionResult)
    assert first.ok is True
    assert first.exit_code == 0
    assert first.stdout != ""


def test_run_command_failure_branch(failing_execution: ExecutionPort) -> None:
    handle = asyncio.run(failing_execution.prepare_workspace(_request(), idempotency_key="ws-1"))
    result = asyncio.run(
        failing_execution.run_command(handle, ("pytest", "tests/"), idempotency_key="cmd-1")
    )
    assert result.ok is False
    assert result.exit_code != 0
    assert result.stderr != ""


def test_collect_evidence_returns_hashed_content(
    execution_port: ExecutionPort, evidence_workspace: WorkspaceHandle
) -> None:
    evidence = asyncio.run(
        execution_port.collect_evidence(
            evidence_workspace, "reports/pytest-report.xml", idempotency_key="ev-1"
        )
    )
    assert evidence.path == "reports/pytest-report.xml"
    assert evidence.content == b"<testsuite tests='3'/>"
    assert evidence.content_hash == sha256(evidence.content).hexdigest()
