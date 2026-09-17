"""Unit tests of the real worktree ``ExecutionPort`` adapter (T-092, TD-022).

Hermetic: every test seeds a throwaway source repository with the real ``git``
binary under ``tmp_path`` (the operator-prepared local mirror) and mints real
worktrees from it, so replay, path safety, timeouts and output caps are
exercised against actual git behaviour — no network, no shared state.
"""

import asyncio
import shutil
import subprocess
from hashlib import sha256
from pathlib import Path
from typing import Final, cast

import pytest

from dark_factory.execution import (
    WORKSPACE_COMMAND_TIMEOUT_ENV_VAR,
    WORKSPACE_MIRROR_ROOT_ENV_VAR,
    WORKSPACE_ROOT_ENV_VAR,
    UnsafeWorkspacePath,
    WorkspaceError,
    WorktreeExecution,
    WorktreeExecutionConfig,
)
from dark_factory.execution.workspace import (
    DEFAULT_COMMAND_TIMEOUT_SECONDS,
    MAX_COMMAND_OUTPUT_BYTES,
    WORKSPACES_DIR_NAME,
)
from dark_factory.ports import (
    Provider,
    RepositoryRef,
    WorkspaceHandle,
    WorkspaceRequest,
)

REPOSITORY: Final[RepositoryRef] = RepositoryRef(provider=Provider.GITHUB, slug="small/pilot")


def _git(cwd: Path, *argv: str) -> str:
    """Run one git command in ``cwd`` and return its stdout (local repository, no network)."""
    process = subprocess.run(
        ("git", "-C", str(cwd), "-c", "commit.gpgsign=false", *argv),
        check=True,
        capture_output=True,
        text=True,
    )
    return process.stdout


def _request(revision: str) -> WorkspaceRequest:
    return WorkspaceRequest(repository=REPOSITORY, revision=revision, change_id="chg-001")


def _workspace_dir(config: WorktreeExecutionConfig, handle: WorkspaceHandle) -> Path:
    return config.root / WORKSPACES_DIR_NAME / handle.workspace_id


@pytest.fixture
def mirror_root(tmp_path: Path) -> Path:
    """The operator-prepared mirror root with one seeded source repo (``github/small/pilot``)."""
    source = tmp_path / "mirror" / "github" / "small" / "pilot"
    source.mkdir(parents=True)
    _git(source, "init", "-b", "main")
    (source / "docs").mkdir()
    (source / "docs" / "note.md").write_bytes(b"note\n")
    _git(source, "add", ".")
    _git(source, "commit", "-m", "seed")
    return tmp_path / "mirror"


@pytest.fixture
def revision(mirror_root: Path) -> str:
    """HEAD of the seeded source repository — the revision tests pin."""
    return _git(mirror_root / "github" / "small" / "pilot", "rev-parse", "HEAD").strip()


@pytest.fixture
def workspace_config(tmp_path: Path, mirror_root: Path) -> WorktreeExecutionConfig:
    return WorktreeExecutionConfig(root=tmp_path / "workspaces-root", mirror_root=mirror_root)


@pytest.fixture
def execution(workspace_config: WorktreeExecutionConfig) -> WorktreeExecution:
    return WorktreeExecution(workspace_config)


@pytest.fixture
def handle(execution: WorktreeExecution, revision: str) -> WorkspaceHandle:
    """One prepared workspace in the seeded repository at the pinned revision."""
    return asyncio.run(execution.prepare_workspace(_request(revision), idempotency_key="ws-1"))


# --- prepare_workspace ------------------------------------------------------


def test_prepare_workspace_mints_a_worktree_at_the_pinned_revision(
    execution: WorktreeExecution, workspace_config: WorktreeExecutionConfig, revision: str
) -> None:
    handle = asyncio.run(execution.prepare_workspace(_request(revision), idempotency_key="ws-1"))

    assert handle.workspace_id != ""
    assert handle.repository == REPOSITORY
    assert handle.revision == revision
    head = _git(_workspace_dir(workspace_config, handle), "rev-parse", "HEAD").strip()
    assert head == revision


def test_replay_with_the_same_key_reuses_the_workspace(
    execution: WorktreeExecution, workspace_config: WorktreeExecutionConfig, revision: str
) -> None:
    handle = asyncio.run(execution.prepare_workspace(_request(revision), idempotency_key="ws-1"))
    asyncio.run(execution.write_file(handle, "agent/scratch.md", b"edit", idempotency_key="wf-1"))

    replay = asyncio.run(execution.prepare_workspace(_request(revision), idempotency_key="ws-1"))

    # The id is deterministic in the key: a replay addresses the same directory
    # and the agent's edits survive it.
    assert replay == handle
    assert (
        _workspace_dir(workspace_config, handle) / "agent" / "scratch.md"
    ).read_bytes() == b"edit"
    other = asyncio.run(execution.prepare_workspace(_request(revision), idempotency_key="ws-2"))
    assert other.workspace_id != handle.workspace_id


def test_replay_after_deleting_the_worktree_converges_to_the_pinned_revision(
    execution: WorktreeExecution,
    workspace_config: WorktreeExecutionConfig,
    handle: WorkspaceHandle,
    revision: str,
) -> None:
    workspace_dir = _workspace_dir(workspace_config, handle)
    asyncio.run(execution.write_file(handle, "agent/scratch.md", b"edit", idempotency_key="wf-1"))
    shutil.rmtree(workspace_dir)

    replay = asyncio.run(execution.prepare_workspace(_request(revision), idempotency_key="ws-1"))

    # A broken or missing workspace is re-created from the pinned revision: the
    # replay converges to a usable workspace, never mints a second one.
    assert replay.workspace_id == handle.workspace_id
    assert _git(workspace_dir, "rev-parse", "HEAD").strip() == revision
    assert not (workspace_dir / "agent" / "scratch.md").exists()


def test_unknown_revision_is_a_workspace_error(execution: WorktreeExecution, revision: str) -> None:
    unknown = "0" * 40  # a well-formed sha the mirror does not have
    with pytest.raises(WorkspaceError):
        asyncio.run(execution.prepare_workspace(_request(unknown), idempotency_key="ws-1"))


def test_missing_mirror_is_a_workspace_error(tmp_path: Path) -> None:
    execution = WorktreeExecution(
        WorktreeExecutionConfig(root=tmp_path / "workspaces-root", mirror_root=tmp_path / "mirror")
    )
    with pytest.raises(WorkspaceError):
        asyncio.run(execution.prepare_workspace(_request("abc123"), idempotency_key="ws-1"))


# --- write_file -------------------------------------------------------------


def test_write_file_creates_parent_directories_and_wins_last(
    execution: WorktreeExecution, handle: WorkspaceHandle
) -> None:
    asyncio.run(
        execution.write_file(handle, "deep/nested/report.xml", b"v1", idempotency_key="wf-1")
    )
    asyncio.run(
        execution.write_file(handle, "deep/nested/report.xml", b"v2", idempotency_key="wf-2")
    )

    evidence = asyncio.run(
        execution.collect_evidence(handle, "deep/nested/report.xml", idempotency_key="ev-1")
    )

    assert evidence.content == b"v2"  # last write wins
    assert evidence.content_hash == sha256(b"v2").hexdigest()


def test_write_file_overwrites_equal_length_content(
    execution: WorktreeExecution, handle: WorkspaceHandle
) -> None:
    # Idempotent by state, not by key: an equal-length payload under the same
    # key must still land.
    asyncio.run(execution.write_file(handle, "note.md", b"aaaa", idempotency_key="wf-1"))
    asyncio.run(execution.write_file(handle, "note.md", b"bbbb", idempotency_key="wf-1"))

    evidence = asyncio.run(execution.collect_evidence(handle, "note.md", idempotency_key="ev-1"))

    assert evidence.content == b"bbbb"


def test_write_file_rejects_unsafe_paths(
    execution: WorktreeExecution, handle: WorkspaceHandle
) -> None:
    for path in ("/etc/passwd", "../escape.md", "..", ""):
        with pytest.raises(UnsafeWorkspacePath):
            asyncio.run(execution.write_file(handle, path, b"x", idempotency_key="wf-1"))


def test_write_file_refuses_to_escape_through_a_symlink(
    execution: WorktreeExecution,
    workspace_config: WorktreeExecutionConfig,
    handle: WorkspaceHandle,
    tmp_path: Path,
) -> None:
    workspace_dir = _workspace_dir(workspace_config, handle)
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace_dir / "outside-link").symlink_to(outside, target_is_directory=True)

    # Writing *through* a symlinked directory must not land outside the workspace.
    with pytest.raises(UnsafeWorkspacePath):
        asyncio.run(
            execution.write_file(handle, "outside-link/escape.md", b"x", idempotency_key="wf-1")
        )
    assert not (outside / "escape.md").exists()

    # ... and neither may a symlinked file be overwritten in place.
    (workspace_dir / "escape.md").symlink_to(outside / "escape.md")
    with pytest.raises(UnsafeWorkspacePath):
        asyncio.run(execution.write_file(handle, "escape.md", b"x", idempotency_key="wf-2"))
    assert not (outside / "escape.md").exists()


# --- run_command ------------------------------------------------------------


def test_run_command_executes_in_the_workspace(
    execution: WorktreeExecution, handle: WorkspaceHandle, revision: str
) -> None:
    result = asyncio.run(
        execution.run_command(handle, ("git", "rev-parse", "HEAD"), idempotency_key="cmd-1")
    )

    assert result.ok is True
    assert result.exit_code == 0
    assert result.stdout.strip() == revision


def test_run_command_reports_a_failing_command(
    execution: WorktreeExecution, handle: WorkspaceHandle
) -> None:
    result = asyncio.run(execution.run_command(handle, ("false",), idempotency_key="cmd-1"))

    assert result.ok is False
    assert result.exit_code == 1


def test_run_command_rejects_an_empty_argv(
    execution: WorktreeExecution, handle: WorkspaceHandle
) -> None:
    with pytest.raises(ValueError):
        asyncio.run(execution.run_command(handle, (), idempotency_key="cmd-1"))


def test_run_command_caps_captured_output(
    execution: WorktreeExecution, handle: WorkspaceHandle
) -> None:
    # 2 MiB on stdout; the captured remainder stops at the 1 MiB cap.
    result = asyncio.run(
        execution.run_command(
            handle,
            ("sh", "-c", "yes 0123456789 | head -c 2097152"),
            idempotency_key="cmd-1",
        )
    )

    assert result.ok is True
    assert len(result.stdout.encode()) == MAX_COMMAND_OUTPUT_BYTES


def test_run_command_times_out(tmp_path: Path, mirror_root: Path, revision: str) -> None:
    config = WorktreeExecutionConfig(
        root=tmp_path / "workspaces-root", mirror_root=mirror_root, command_timeout=0.2
    )
    execution = WorktreeExecution(config)
    handle = asyncio.run(execution.prepare_workspace(_request(revision), idempotency_key="ws-1"))

    result = asyncio.run(execution.run_command(handle, ("sleep", "5"), idempotency_key="cmd-1"))

    assert result.ok is False
    assert result.stderr == "command timed out"


# --- collect_evidence -------------------------------------------------------


def test_collect_evidence_hashes_the_written_file(
    execution: WorktreeExecution, handle: WorkspaceHandle
) -> None:
    asyncio.run(
        execution.write_file(handle, "reports/report.xml", b"<testsuite/>", idempotency_key="wf-1")
    )

    evidence = asyncio.run(
        execution.collect_evidence(handle, "reports/report.xml", idempotency_key="ev-1")
    )

    assert evidence.path == "reports/report.xml"
    assert evidence.content == b"<testsuite/>"
    assert evidence.content_hash == sha256(b"<testsuite/>").hexdigest()


def test_collect_evidence_of_a_missing_file_is_a_key_error(
    execution: WorktreeExecution, handle: WorkspaceHandle
) -> None:
    with pytest.raises(KeyError):
        asyncio.run(execution.collect_evidence(handle, "missing.xml", idempotency_key="ev-1"))


# --- collect_changes --------------------------------------------------------


def test_collect_changes_of_a_fresh_workspace_is_empty(
    execution: WorktreeExecution, handle: WorkspaceHandle
) -> None:
    assert asyncio.run(execution.collect_changes(handle, idempotency_key="cc-1")) == {}


def test_collect_changes_lists_the_written_files(
    execution: WorktreeExecution, handle: WorkspaceHandle
) -> None:
    asyncio.run(
        execution.write_file(handle, "src/app.py", b"print('hi')\n", idempotency_key="wf-1")
    )
    asyncio.run(execution.write_file(handle, "docs/note.md", b"changed\n", idempotency_key="wf-2"))

    changes = asyncio.run(execution.collect_changes(handle, idempotency_key="cc-1"))

    assert changes == {"src/app.py": b"print('hi')\n", "docs/note.md": b"changed\n"}


def test_collect_changes_skips_deleted_tracked_files(
    execution: WorktreeExecution,
    workspace_config: WorktreeExecutionConfig,
    handle: WorkspaceHandle,
) -> None:
    # Deletions are not expressible in the file-set unit of transfer (TD-024),
    # so a removed tracked file must not surface as a change entry.
    (_workspace_dir(workspace_config, handle) / "docs" / "note.md").unlink()

    changes = asyncio.run(execution.collect_changes(handle, idempotency_key="cc-1"))

    assert changes == {}


def test_collect_changes_returns_an_independent_copy(
    execution: WorktreeExecution, handle: WorkspaceHandle
) -> None:
    asyncio.run(execution.write_file(handle, "src/app.py", b"v1", idempotency_key="wf-1"))
    first = asyncio.run(execution.collect_changes(handle, idempotency_key="cc-1"))

    # Mutating the returned mapping must not touch the workspace: the port
    # returns a fresh copy on every call.
    cast("dict[str, bytes]", first)["injected"] = b"x"

    again = asyncio.run(execution.collect_changes(handle, idempotency_key="cc-2"))

    assert again == {"src/app.py": b"v1"}


# --- unknown workspace / configuration --------------------------------------


def test_every_method_rejects_an_unknown_workspace(execution: WorktreeExecution) -> None:
    unknown = WorkspaceHandle(workspace_id="ws-9999", repository=REPOSITORY, revision="abc123")

    with pytest.raises(KeyError):
        asyncio.run(execution.write_file(unknown, "note.md", b"x", idempotency_key="wf-1"))
    with pytest.raises(KeyError):
        asyncio.run(execution.run_command(unknown, ("true",), idempotency_key="cmd-1"))
    with pytest.raises(KeyError):
        asyncio.run(
            execution.collect_evidence(unknown, "reports/report.xml", idempotency_key="ev-1")
        )
    with pytest.raises(KeyError):
        asyncio.run(execution.collect_changes(unknown, idempotency_key="cc-1"))


def test_config_from_env_requires_both_path_variables() -> None:
    assert WorktreeExecutionConfig.from_env({}) is None
    assert WorktreeExecutionConfig.from_env({WORKSPACE_ROOT_ENV_VAR: "/f/workspaces"}) is None
    assert WorktreeExecutionConfig.from_env({WORKSPACE_MIRROR_ROOT_ENV_VAR: "/f/mirrors"}) is None


def test_config_from_env_reads_the_variables(tmp_path: Path) -> None:
    config = WorktreeExecutionConfig.from_env(
        {
            WORKSPACE_ROOT_ENV_VAR: str(tmp_path / "workspaces"),
            WORKSPACE_MIRROR_ROOT_ENV_VAR: str(tmp_path / "mirrors"),
            WORKSPACE_COMMAND_TIMEOUT_ENV_VAR: "12.5",
        }
    )

    assert config == WorktreeExecutionConfig(
        root=tmp_path / "workspaces", mirror_root=tmp_path / "mirrors", command_timeout=12.5
    )


def test_config_from_env_defaults_the_command_timeout(tmp_path: Path) -> None:
    config = WorktreeExecutionConfig.from_env(
        {
            WORKSPACE_ROOT_ENV_VAR: str(tmp_path / "workspaces"),
            WORKSPACE_MIRROR_ROOT_ENV_VAR: str(tmp_path / "mirrors"),
        }
    )

    assert config is not None
    assert config.command_timeout == DEFAULT_COMMAND_TIMEOUT_SECONDS


def test_config_from_env_rejects_relative_or_empty_paths() -> None:
    base = {WORKSPACE_ROOT_ENV_VAR: "/f/workspaces", WORKSPACE_MIRROR_ROOT_ENV_VAR: "/f/mirrors"}

    # A set-but-invalid variable is a misconfiguration, not an absence.
    with pytest.raises(ValueError, match=WORKSPACE_ROOT_ENV_VAR):
        WorktreeExecutionConfig.from_env({**base, WORKSPACE_ROOT_ENV_VAR: "relative/workspaces"})
    with pytest.raises(ValueError, match=WORKSPACE_ROOT_ENV_VAR):
        WorktreeExecutionConfig.from_env({**base, WORKSPACE_ROOT_ENV_VAR: ""})
    with pytest.raises(ValueError, match=WORKSPACE_MIRROR_ROOT_ENV_VAR):
        WorktreeExecutionConfig.from_env({**base, WORKSPACE_MIRROR_ROOT_ENV_VAR: ""})


def test_config_from_env_rejects_a_bad_command_timeout() -> None:
    base = {WORKSPACE_ROOT_ENV_VAR: "/f/workspaces", WORKSPACE_MIRROR_ROOT_ENV_VAR: "/f/mirrors"}

    for value in ("abc", "0", "-1"):
        with pytest.raises(ValueError, match="must be a positive number of seconds"):
            WorktreeExecutionConfig.from_env({**base, WORKSPACE_COMMAND_TIMEOUT_ENV_VAR: value})

    with pytest.raises(ValueError) as raised:
        WorktreeExecutionConfig.from_env(
            {**base, WORKSPACE_COMMAND_TIMEOUT_ENV_VAR: "not-a-number"}
        )

    # The message names the variable, never the value (ADR-009).
    assert WORKSPACE_COMMAND_TIMEOUT_ENV_VAR in str(raised.value)
    assert "not-a-number" not in str(raised.value)
