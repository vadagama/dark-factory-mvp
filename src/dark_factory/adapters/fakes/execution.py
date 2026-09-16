"""In-memory fake of the execution port (real worktree adapter arrives later)."""

from hashlib import sha256

from dark_factory.ports import (
    EvidenceFile,
    ExecutionPort,
    ExecutionResult,
    WorkspaceHandle,
    WorkspaceRequest,
)


class FakeExecution(ExecutionPort):
    """In-memory ``ExecutionPort`` keeping all workspace state in memory.

    ``idempotency_key`` follows the per-method contract of ``ExecutionPort``:

    * ``prepare_workspace`` is idempotent by ``idempotency_key`` — a replay
      returns the same handle and mints no second workspace; ids are
      deterministic ``ws-NNNN``;
    * ``write_file`` puts content into the workspace and is idempotent by
      *state*, not by key: the path holds exactly the last written bytes
      afterwards, and a repeated key never turns the write into a no-op. It is
      the write half of ``collect_evidence``: both address the seeded file
      store, so content written by an agent tool is readable back as evidence;
    * ``run_command`` derives its result from ``argv`` only and
      ``collect_evidence`` from the seeded files only, so both read the *current*
      state. Neither keeps a per-key result ledger: a replay with the same key
      still reports the state as it is now (an agent edits and re-runs).

    An unknown workspace or path raises ``KeyError``.
    """

    def __init__(self) -> None:
        self._workspaces: dict[str, WorkspaceHandle] = {}
        self._prepare_keys: dict[str, str] = {}
        self._files: dict[str, dict[str, bytes]] = {}
        self._failures: dict[tuple[str, ...], tuple[int, str]] = {}

    def seed_failure(self, argv: tuple[str, ...], *, exit_code: int = 1) -> None:
        """Make ``run_command`` fail deterministically for ``argv`` (simulation hook)."""
        self._failures[argv] = (exit_code, f"fake:failed:{' '.join(argv)}")

    def seed_file(self, workspace: WorkspaceHandle, path: str, content: bytes) -> None:
        """Register an evidence file for ``collect_evidence`` (simulation hook)."""
        self._files.setdefault(workspace.workspace_id, {})[path] = content

    async def prepare_workspace(
        self, request: WorkspaceRequest, /, *, idempotency_key: str
    ) -> WorkspaceHandle:
        existing = self._prepare_keys.get(idempotency_key)
        if existing is not None:
            return self._workspaces[existing]
        workspace_id = f"ws-{len(self._workspaces) + 1:04d}"
        handle = WorkspaceHandle(
            workspace_id=workspace_id, repository=request.repository, revision=request.revision
        )
        self._workspaces[workspace_id] = handle
        self._prepare_keys[idempotency_key] = workspace_id
        return handle

    async def write_file(
        self, workspace: WorkspaceHandle, path: str, content: bytes, /, *, idempotency_key: str
    ) -> None:
        # Idempotent by state, not by key: the path holds exactly ``content``
        # afterwards, so a replay is a no-op in effect (FR-017).
        self._workspace(workspace)
        self._files.setdefault(workspace.workspace_id, {})[path] = content

    async def run_command(
        self, workspace: WorkspaceHandle, argv: tuple[str, ...], /, *, idempotency_key: str
    ) -> ExecutionResult:
        self._workspace(workspace)
        failure = self._failures.get(argv)
        if failure is not None:
            exit_code, stderr = failure
            return ExecutionResult(ok=False, exit_code=exit_code, stdout="", stderr=stderr)
        return ExecutionResult(ok=True, exit_code=0, stdout=f"fake:run:{' '.join(argv)}")

    async def collect_evidence(
        self, workspace: WorkspaceHandle, path: str, /, *, idempotency_key: str
    ) -> EvidenceFile:
        self._workspace(workspace)
        try:
            content = self._files[workspace.workspace_id][path]
        except KeyError:
            raise KeyError(
                f"no evidence file {path!r} in workspace {workspace.workspace_id!r}"
            ) from None
        return EvidenceFile(path=path, content_hash=sha256(content).hexdigest(), content=content)

    def _workspace(self, workspace: WorkspaceHandle) -> WorkspaceHandle:
        try:
            return self._workspaces[workspace.workspace_id]
        except KeyError:
            raise KeyError(f"unknown workspace {workspace.workspace_id!r}") from None
