"""Contract tests for ExecutionPort."""

import asyncio
from hashlib import sha256

import pytest

from dark_factory.ports import ExecutionPort, ExecutionResult, WorkspaceHandle
from tests.contract.conftest import _ExecutionBinding


def _unknown_workspace(binding: _ExecutionBinding) -> WorkspaceHandle:
    """A handle no binding ever minted."""
    return WorkspaceHandle(
        workspace_id="ws-9999",
        repository=binding.make_request().repository,
        revision=binding.revision,
    )


def test_adapter_satisfies_protocol(execution_binding: _ExecutionBinding) -> None:
    assert isinstance(execution_binding.port, ExecutionPort)


def test_prepare_workspace_returns_pinned_handle(execution_binding: _ExecutionBinding) -> None:
    handle = asyncio.run(
        execution_binding.port.prepare_workspace(
            execution_binding.make_request(), idempotency_key="ws-1"
        )
    )
    assert isinstance(handle, WorkspaceHandle)
    assert handle.workspace_id != ""
    assert handle.repository == execution_binding.make_request().repository
    assert handle.revision == execution_binding.revision


def test_prepare_workspace_replays_same_key_to_same_handle(
    execution_binding: _ExecutionBinding,
) -> None:
    first = asyncio.run(
        execution_binding.port.prepare_workspace(
            execution_binding.make_request(), idempotency_key="ws-1"
        )
    )
    second = asyncio.run(
        execution_binding.port.prepare_workspace(
            execution_binding.make_request(), idempotency_key="ws-1"
        )
    )
    assert second == first


def test_run_command_is_deterministic(execution_binding: _ExecutionBinding) -> None:
    handle = asyncio.run(
        execution_binding.port.prepare_workspace(
            execution_binding.make_request(), idempotency_key="ws-1"
        )
    )
    first = asyncio.run(
        execution_binding.port.run_command(
            handle, execution_binding.ok_command, idempotency_key="cmd-1"
        )
    )
    second = asyncio.run(
        execution_binding.port.run_command(
            handle, execution_binding.ok_command, idempotency_key="cmd-2"
        )
    )
    assert second == first  # the result depends on argv only, not on the key
    assert isinstance(first, ExecutionResult)
    assert first.ok is True
    assert first.exit_code == 0
    assert first.stdout != ""


def test_run_command_failure_branch(execution_binding: _ExecutionBinding) -> None:
    handle = asyncio.run(
        execution_binding.port.prepare_workspace(
            execution_binding.make_request(), idempotency_key="ws-1"
        )
    )
    result = asyncio.run(
        execution_binding.port.run_command(
            handle, execution_binding.fail_command, idempotency_key="cmd-1"
        )
    )
    assert result.ok is False
    assert result.exit_code != 0
    assert result.stderr != ""


def test_run_command_same_key_reflects_current_state(
    execution_binding: _ExecutionBinding,
) -> None:
    # The key addresses the call in the effect ledger; it is NOT a result cache.
    # An agent edits a file and re-runs the check under the same key, so a port
    # that replayed the result stored under the key would pin the first
    # (failing) outcome forever.
    handle = asyncio.run(
        execution_binding.port.prepare_workspace(
            execution_binding.make_request(), idempotency_key="ws-1"
        )
    )
    first = asyncio.run(
        execution_binding.port.run_command(
            handle, execution_binding.state_command, idempotency_key="cmd-1"
        )
    )
    assert first.ok is True

    execution_binding.make_state_fail(handle)  # the workspace state changed

    second = asyncio.run(
        execution_binding.port.run_command(
            handle, execution_binding.state_command, idempotency_key="cmd-1"
        )
    )
    assert second.ok is False
    assert second.exit_code == 1


def test_collect_evidence_returns_hashed_content(
    execution_binding: _ExecutionBinding, evidence_workspace: WorkspaceHandle
) -> None:
    evidence = asyncio.run(
        execution_binding.port.collect_evidence(
            evidence_workspace, "reports/pytest-report.xml", idempotency_key="ev-1"
        )
    )
    assert evidence.path == "reports/pytest-report.xml"
    assert evidence.content == b"<testsuite tests='3'/>"
    assert evidence.content_hash == sha256(evidence.content).hexdigest()


def test_write_file_is_readable_as_evidence(
    execution_binding: _ExecutionBinding, evidence_workspace: WorkspaceHandle
) -> None:
    asyncio.run(
        execution_binding.port.write_file(
            evidence_workspace, "src/app.py", b"print('hi')\n", idempotency_key="wf-1"
        )
    )
    evidence = asyncio.run(
        execution_binding.port.collect_evidence(
            evidence_workspace, "src/app.py", idempotency_key="ev-2"
        )
    )
    assert evidence.content == b"print('hi')\n"
    assert evidence.content_hash == sha256(evidence.content).hexdigest()


def test_write_file_is_idempotent_by_state(
    execution_binding: _ExecutionBinding, evidence_workspace: WorkspaceHandle
) -> None:
    asyncio.run(
        execution_binding.port.write_file(
            evidence_workspace, "src/app.py", b"v1", idempotency_key="wf-1"
        )
    )
    asyncio.run(
        execution_binding.port.write_file(
            evidence_workspace, "src/app.py", b"v1", idempotency_key="wf-2"
        )
    )
    evidence = asyncio.run(
        execution_binding.port.collect_evidence(
            evidence_workspace, "src/app.py", idempotency_key="ev-1"
        )
    )
    assert evidence.content == b"v1"


def test_write_file_overwrite_of_equal_length_is_visible(
    execution_binding: _ExecutionBinding, evidence_workspace: WorkspaceHandle
) -> None:
    # Same path, equal-length payloads and the same key: ``write_file`` is
    # idempotent by state (last write wins), so the second write must land. A
    # port that deduplicated by key — the reading this contract rules out — would
    # silently keep the first payload.
    asyncio.run(
        execution_binding.port.write_file(
            evidence_workspace, "src/app.py", b"v1", idempotency_key="wf-1"
        )
    )
    asyncio.run(
        execution_binding.port.write_file(
            evidence_workspace, "src/app.py", b"v2", idempotency_key="wf-1"
        )
    )
    evidence = asyncio.run(
        execution_binding.port.collect_evidence(
            evidence_workspace, "src/app.py", idempotency_key="ev-1"
        )
    )
    assert evidence.content == b"v2"
    assert evidence.content_hash == sha256(b"v2").hexdigest()


def test_write_file_rejects_unknown_workspace(execution_binding: _ExecutionBinding) -> None:
    unknown = _unknown_workspace(execution_binding)
    with pytest.raises(KeyError):
        asyncio.run(
            execution_binding.port.write_file(unknown, "src/app.py", b"v1", idempotency_key="wf-1")
        )


def test_collect_changes_of_a_fresh_workspace_is_empty(
    execution_binding: _ExecutionBinding,
) -> None:
    handle = asyncio.run(
        execution_binding.port.prepare_workspace(
            execution_binding.make_request(), idempotency_key="ws-1"
        )
    )
    changes = asyncio.run(execution_binding.port.collect_changes(handle, idempotency_key="cc-1"))
    assert changes == {}


def test_collect_changes_returns_the_current_file_set(
    execution_binding: _ExecutionBinding,
) -> None:
    handle = asyncio.run(
        execution_binding.port.prepare_workspace(
            execution_binding.make_request(), idempotency_key="ws-1"
        )
    )
    asyncio.run(
        execution_binding.port.write_file(handle, "src/app.py", b"v1", idempotency_key="wf-1")
    )
    asyncio.run(
        execution_binding.port.write_file(handle, "docs/note.md", b"note", idempotency_key="wf-2")
    )

    changes = asyncio.run(execution_binding.port.collect_changes(handle, idempotency_key="cc-1"))

    assert changes == {"src/app.py": b"v1", "docs/note.md": b"note"}


def test_collect_changes_reflects_current_state_not_the_first_call(
    execution_binding: _ExecutionBinding,
) -> None:
    # Like run_command/collect_evidence, the key is a ledger address, not a
    # result cache: the publication must carry what the workspace holds now.
    handle = asyncio.run(
        execution_binding.port.prepare_workspace(
            execution_binding.make_request(), idempotency_key="ws-1"
        )
    )
    asyncio.run(
        execution_binding.port.write_file(handle, "src/app.py", b"v1", idempotency_key="wf-1")
    )
    first = asyncio.run(execution_binding.port.collect_changes(handle, idempotency_key="cc-1"))
    assert first == {"src/app.py": b"v1"}

    asyncio.run(
        execution_binding.port.write_file(handle, "src/app.py", b"v2", idempotency_key="wf-2")
    )

    second = asyncio.run(execution_binding.port.collect_changes(handle, idempotency_key="cc-1"))
    assert second == {"src/app.py": b"v2"}


def test_collect_changes_returns_a_copy(execution_binding: _ExecutionBinding) -> None:
    handle = asyncio.run(
        execution_binding.port.prepare_workspace(
            execution_binding.make_request(), idempotency_key="ws-1"
        )
    )
    asyncio.run(
        execution_binding.port.write_file(handle, "src/app.py", b"v1", idempotency_key="wf-1")
    )
    first = asyncio.run(execution_binding.port.collect_changes(handle, idempotency_key="cc-1"))

    asyncio.run(
        execution_binding.port.write_file(handle, "src/app.py", b"v2", idempotency_key="wf-2")
    )

    # The earlier mapping is a snapshot: a later write never mutates it.
    assert first == {"src/app.py": b"v1"}
    again = asyncio.run(execution_binding.port.collect_changes(handle, idempotency_key="cc-3"))
    assert again == {"src/app.py": b"v2"}


def test_collect_changes_rejects_unknown_workspace(
    execution_binding: _ExecutionBinding,
) -> None:
    unknown = _unknown_workspace(execution_binding)
    with pytest.raises(KeyError):
        asyncio.run(execution_binding.port.collect_changes(unknown, idempotency_key="cc-1"))
