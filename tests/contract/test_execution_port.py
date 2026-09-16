"""Contract tests for ExecutionPort."""

import asyncio
from hashlib import sha256

import pytest

from dark_factory.adapters.fakes import FakeExecution
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


def test_run_command_same_key_reflects_current_state(
    execution: FakeExecution, execution_port: ExecutionPort
) -> None:
    # The key addresses the call in the effect ledger; it is NOT a result cache.
    # An agent edits a file and re-runs the check under the same key, so a port
    # that replayed the result stored under the key would pin the first
    # (failing) outcome forever. The fake keeps no key ledger by construction.
    handle = asyncio.run(execution_port.prepare_workspace(_request(), idempotency_key="ws-1"))
    first = asyncio.run(
        execution_port.run_command(handle, ("pytest", "-q"), idempotency_key="cmd-1")
    )
    assert first.ok is True

    execution.seed_failure(("pytest", "-q"), exit_code=1)  # the workspace state changed

    second = asyncio.run(
        execution_port.run_command(handle, ("pytest", "-q"), idempotency_key="cmd-1")
    )
    assert second.ok is False
    assert second.exit_code == 1


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


def test_write_file_is_readable_as_evidence(
    execution_port: ExecutionPort, evidence_workspace: WorkspaceHandle
) -> None:
    asyncio.run(
        execution_port.write_file(
            evidence_workspace, "src/app.py", b"print('hi')\n", idempotency_key="wf-1"
        )
    )
    evidence = asyncio.run(
        execution_port.collect_evidence(evidence_workspace, "src/app.py", idempotency_key="ev-2")
    )
    assert evidence.content == b"print('hi')\n"
    assert evidence.content_hash == sha256(evidence.content).hexdigest()


def test_write_file_is_idempotent_by_state(
    execution_port: ExecutionPort, evidence_workspace: WorkspaceHandle
) -> None:
    asyncio.run(
        execution_port.write_file(evidence_workspace, "src/app.py", b"v1", idempotency_key="wf-1")
    )
    asyncio.run(
        execution_port.write_file(evidence_workspace, "src/app.py", b"v1", idempotency_key="wf-2")
    )
    evidence = asyncio.run(
        execution_port.collect_evidence(evidence_workspace, "src/app.py", idempotency_key="ev-1")
    )
    assert evidence.content == b"v1"


def test_write_file_overwrite_of_equal_length_is_visible(
    execution_port: ExecutionPort, evidence_workspace: WorkspaceHandle
) -> None:
    # Same path, equal-length payloads and the same key: ``write_file`` is
    # idempotent by state (last write wins), so the second write must land. A
    # port that deduplicated by key — the reading this contract rules out — would
    # silently keep the first payload.
    asyncio.run(
        execution_port.write_file(evidence_workspace, "src/app.py", b"v1", idempotency_key="wf-1")
    )
    asyncio.run(
        execution_port.write_file(evidence_workspace, "src/app.py", b"v2", idempotency_key="wf-1")
    )
    evidence = asyncio.run(
        execution_port.collect_evidence(evidence_workspace, "src/app.py", idempotency_key="ev-1")
    )
    assert evidence.content == b"v2"
    assert evidence.content_hash == sha256(b"v2").hexdigest()


def test_write_file_rejects_unknown_workspace(execution_port: ExecutionPort) -> None:
    unknown = WorkspaceHandle(
        workspace_id="ws-9999",
        repository=PRODUCT,
        revision="abc123",
    )
    with pytest.raises(KeyError):
        asyncio.run(execution_port.write_file(unknown, "src/app.py", b"v1", idempotency_key="wf-1"))
