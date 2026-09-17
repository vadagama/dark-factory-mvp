"""Real ``ExecutionPort`` adapter: isolated git worktrees (TD-022, T-012).

An agent stage edits real files and runs real commands inside one worktree
minted from the revision the stage pinned (``WorkspaceRequest.revision``), so
the workspace starts as an exact checkout of the input revision and
``collect_changes`` reports the *delta* against it — the file set that
``RepositoryPort.publish_commit`` carries (TD-024).

Where the source comes from: the adapter never talks to a provider over the
network. The operator prepares a local git mirror per repository under
``DARK_FACTORY_WORKSPACE_MIRROR_ROOT``, laid out ``<mirror_root>/<provider>/<slug>``
— the same operator-prepared-checkout pattern as ``DARK_FACTORY_RUNS_ROOT`` —
and the adapter fetches from the mirror's ``origin`` remote (when it has one)
before minting a worktree. Cloning from a provider with credentials is an
explicit non-goal of the MVP: the workspace path stays credential-free
(ADR-009, T-091 pods).

Idempotency and replay (FR-017, per-method contract of ``ExecutionPort``):

* ``prepare_workspace`` derives the workspace id and directory deterministically
  from the ``idempotency_key`` (bounded slug + sha256 prefix of the full key),
  so a replay — including after a cold restart — addresses the same directory.
  An existing valid worktree is reused as it is (agent edits survive); a broken
  or missing one is re-created from the pinned revision, so a replay always
  converges to a usable workspace and never mints a second one. The key is
  expected to be derived from the request (the stage keys its effects that way);
  a replay with a different request is a caller bug.
* ``write_file`` is idempotent by state (last write wins); the key never
  deduplicates a write.
* ``run_command`` / ``collect_evidence`` / ``collect_changes`` execute and read
  the *current* workspace state; the key only addresses the call in the effect
  ledger, and no result is ever cached under it.

Error policy (ADR-009): git failures raise value-free :class:`WorkspaceError`s —
the message names the failed step, never command output; the stage boundary
renders only the exception *type*, so no provider text can leak into a blocked
reason. An unknown workspace is a ``KeyError`` (the port's absent convention),
an unsafe path a :class:`UnsafeWorkspacePath`.

Limitations (MVP, recorded in ``docs/tech-dept.md``): deletions are not
expressible in the file-set unit of transfer, so ``collect_changes`` skips them
(TD-024); the delta is computed by ``git status`` against the worktree's HEAD,
which the port pins at the requested revision — the role tools never move it.
"""

import asyncio
import os
import re
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Final

from dark_factory.changes.refs import RepositoryRef
from dark_factory.ports import (
    EvidenceFile,
    ExecutionPort,
    ExecutionResult,
    WorkspaceHandle,
    WorkspaceRequest,
)

WORKSPACE_ROOT_ENV_VAR: Final[str] = "DARK_FACTORY_WORKSPACE_ROOT"
"""Directory that holds the minted workspaces (``<root>/workspaces/<id>``). Required."""

WORKSPACE_MIRROR_ROOT_ENV_VAR: Final[str] = "DARK_FACTORY_WORKSPACE_MIRROR_ROOT"
"""Root of the operator-prepared local git mirrors, laid out ``<root>/<provider>/<slug>``.

Required."""

WORKSPACE_COMMAND_TIMEOUT_ENV_VAR: Final[str] = "DARK_FACTORY_WORKSPACE_COMMAND_TIMEOUT"
"""Per-command timeout in seconds (optional; default 600).

Non-numeric or non-positive values fail closed."""

DEFAULT_COMMAND_TIMEOUT_SECONDS: Final[float] = 600.0
"""Timeout of one ``run_command`` call when the environment does not override it."""

MAX_COMMAND_OUTPUT_BYTES: Final[int] = 1024 * 1024
"""Per-stream cap of captured stdout/stderr (1 MiB); beyond it output is drained and discarded.

A module constant rather than an environment knob (YAGNI): the cap exists so a
runaway command cannot exhaust the process, not as a tunable."""

WORKSPACES_DIR_NAME: Final[str] = "workspaces"
"""Subdirectory of the workspace root that holds the minted worktrees."""

_WORKSPACE_SLUG_MAX_LENGTH: Final[int] = 80
_WORKSPACE_DIGEST_HEX_LENGTH: Final[int] = 12
_CHUNK_SIZE: Final[int] = 64 * 1024
_COMMAND_TIMED_OUT: Final[str] = "command timed out"
_SLUG_UNSAFE: Final[re.Pattern[str]] = re.compile(r"[^A-Za-z0-9._-]")
_UNSAFE_PARTS: Final[frozenset[str]] = frozenset({"", ".", ".."})


class WorkspaceError(RuntimeError):
    """A workspace operation failed at the boundary; the message stays value-free (ADR-009)."""


class UnsafeWorkspacePath(ValueError):
    """A port call addressed a path outside the isolated workspace."""


def _env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    """The given mapping or, by default, the process environment."""
    return os.environ if env is None else env


def _require_absolute_path(path: Path, variable: str) -> None:
    """Fail closed on an empty or relative configured path: a typo is an error, not an absence."""
    if not path.is_absolute():
        raise ValueError(f"{variable} must be an absolute path")


def _workspace_id(idempotency_key: str) -> str:
    """Deterministic, bounded, collision-safe directory name of one workspace.

    The readable prefix keeps the key recognizable in listings but is truncated,
    because keys are unbounded (``run:stage:revision:workspace:change``); the
    sha256 suffix keeps the name collision-safe over the *full* key. Long keys
    are folded, never rejected — every key must map onto one stable directory.
    """
    readable = _SLUG_UNSAFE.sub("-", idempotency_key).strip("-")
    readable = readable[:_WORKSPACE_SLUG_MAX_LENGTH]
    digest = sha256(idempotency_key.encode("utf-8")).hexdigest()[:_WORKSPACE_DIGEST_HEX_LENGTH]
    return f"{readable}-{digest}" if readable else f"workspace-{digest}"


def _safe_parts(path: str) -> tuple[str, ...]:
    """Validated workspace-relative path segments (the same rule the role tools apply)."""
    normalized = path.strip().replace("\\", "/")
    if not normalized:
        raise UnsafeWorkspacePath("the path must be a non-empty workspace-relative path")
    relative = PurePosixPath(normalized)
    if relative.is_absolute() or normalized.startswith("/"):
        raise UnsafeWorkspacePath(f"path {path!r} must be relative to the workspace")
    parts = tuple(part for part in relative.parts if part != ".")
    if not parts or any(part == ".." for part in parts):
        raise UnsafeWorkspacePath(f"path {path!r} must stay inside the workspace")
    return parts


def _require_inside(root: Path, path: Path) -> None:
    """Refuse a path that resolves outside the workspace (symlinks included)."""
    if not path.resolve().is_relative_to(root.resolve()):
        raise UnsafeWorkspacePath("the path escapes the workspace")


def _porcelain_paths(listing: str) -> tuple[str, ...]:
    """Workspace-relative paths of ``git status --porcelain=v1 -z`` records.

    With ``-z`` every record is NUL-terminated, and a renamed/copied record is
    followed by its original path as one extra NUL field. Deletions are filtered
    here: the file-set unit of transfer (TD-024) cannot express them.
    """
    paths: list[str] = []
    fields = listing.split("\0")
    index = 0
    while index < len(fields):
        record = fields[index]
        index += 1
        if len(record) <= 3:  # "XY P" is the minimum record; an empty trailing field is skipped
            continue
        status = record[:2]
        if "R" in status or "C" in status:
            index += 1  # consume the original path of a rename/copy
        if "D" in status:
            continue
        paths.append(record[3:])
    return tuple(paths)


async def _drain(stream: asyncio.StreamReader | None, cap: int) -> bytes:
    """Read a command stream to EOF, keeping at most ``cap`` bytes (truncated at the byte boundary).

    Reading continues past the cap — a process blocked on a full pipe would
    never finish — but the surplus is discarded, so memory stays bounded.
    """
    if stream is None:
        return b""
    kept: list[bytes] = []
    received = 0
    while True:
        chunk = await stream.read(_CHUNK_SIZE)
        if not chunk:
            return b"".join(kept)
        if received < cap:
            kept.append(chunk if received + len(chunk) <= cap else chunk[: cap - received])
        received += len(chunk)


def _decode(data: bytes) -> str:
    """Decode captured output; a truncated multibyte character becomes a replacement one."""
    return data.decode("utf-8", errors="replace")


@dataclass(frozen=True, slots=True)
class WorktreeExecutionConfig:
    """Paths and limits of the worktree execution adapter (TD-022)."""

    root: Path
    """Directory that holds the minted workspaces."""
    mirror_root: Path
    """Root of the operator-prepared local git mirrors."""
    command_timeout: float = DEFAULT_COMMAND_TIMEOUT_SECONDS

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "WorktreeExecutionConfig | None":
        """Config read from ``env`` (default: the process environment); ``None`` when absent.

        A required variable that is not set leaves the adapter absent — the
        composition root reports the gap instead of guessing a location. A
        variable that *is* set but names an empty or relative path is a
        misconfiguration, not an absence: construction fails closed with a
        ``ValueError`` that names the variable, never the value (ADR-009).
        """
        source = _env(env)
        root_value = source.get(WORKSPACE_ROOT_ENV_VAR)
        mirror_value = source.get(WORKSPACE_MIRROR_ROOT_ENV_VAR)
        if root_value is None or mirror_value is None:
            return None
        timeout = DEFAULT_COMMAND_TIMEOUT_SECONDS
        timeout_value = (source.get(WORKSPACE_COMMAND_TIMEOUT_ENV_VAR) or "").strip()
        if timeout_value:
            try:
                timeout = float(timeout_value)
            except ValueError:
                raise ValueError(
                    f"{WORKSPACE_COMMAND_TIMEOUT_ENV_VAR} must be a positive number of seconds"
                ) from None
            if timeout <= 0:
                raise ValueError(
                    f"{WORKSPACE_COMMAND_TIMEOUT_ENV_VAR} must be a positive number of seconds"
                )
        return cls(root=Path(root_value), mirror_root=Path(mirror_value), command_timeout=timeout)

    def __post_init__(self) -> None:
        _require_absolute_path(self.root, WORKSPACE_ROOT_ENV_VAR)
        _require_absolute_path(self.mirror_root, WORKSPACE_MIRROR_ROOT_ENV_VAR)


class WorktreeExecution(ExecutionPort):
    """``ExecutionPort`` over real git worktrees minted from a local mirror (TD-022).

    One directory per stage attempt under ``<root>/workspaces/<id>``, detached
    at the pinned revision; all git access goes through the local mirror, never
    through the network. See the module docstring for the idempotency and error
    policy.
    """

    def __init__(self, config: WorktreeExecutionConfig) -> None:
        self._config = config

    async def prepare_workspace(
        self, request: WorkspaceRequest, /, *, idempotency_key: str
    ) -> WorkspaceHandle:
        """Mint — or find on replay — the isolated worktree of the pinned revision.

        Replay semantics: the id and path derive from the key only, so the same
        key always addresses the same directory. An existing *valid* worktree is
        returned untouched (idempotent by state: agent edits survive a retry);
        an existing *invalid* one (truncated crashed state) is removed and
        re-created from the pinned revision. Both paths return the same handle.
        """
        workspace_id = _workspace_id(idempotency_key)
        path = self._workspace_path(workspace_id)
        handle = WorkspaceHandle(
            workspace_id=workspace_id, repository=request.repository, revision=request.revision
        )
        if path.exists():
            if await self._is_valid_worktree(path):
                return handle
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        await self._mint(request, path)
        return handle

    async def write_file(
        self, workspace: WorkspaceHandle, path: str, content: bytes, /, *, idempotency_key: str
    ) -> None:
        """Write bytes into the workspace (last write wins, the key never deduplicates)."""
        root = self._workspace_dir(workspace)
        parts = _safe_parts(path)
        target = root.joinpath(*parts)
        # Containment is checked on the deepest *existing* ancestor before
        # anything is created, so a symlink planted in the workspace cannot
        # redirect parent-directory creation outside it.
        probe = target.parent
        while not probe.exists():
            probe = probe.parent
        _require_inside(root, probe)
        target.parent.mkdir(parents=True, exist_ok=True)
        _require_inside(root, target.parent)
        if target.is_symlink():
            raise UnsafeWorkspacePath(f"path {path!r} must not overwrite a symlink")
        target.write_bytes(content)

    async def run_command(
        self, workspace: WorkspaceHandle, argv: tuple[str, ...], /, *, idempotency_key: str
    ) -> ExecutionResult:
        """Run ``argv`` in the worktree without a shell, with timeout and output caps."""
        root = self._workspace_dir(workspace)
        if not argv:
            raise ValueError("run_command requires a non-empty argv")
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                asyncio.gather(
                    _drain(process.stdout, MAX_COMMAND_OUTPUT_BYTES),
                    _drain(process.stderr, MAX_COMMAND_OUTPUT_BYTES),
                ),
                timeout=self._config.command_timeout,
            )
        except TimeoutError:
            process.kill()
            # Output captured before the kill is best-effort (the cancelled read
            # may drop a chunk); the stderr marker stays value-free.
            stdout, stderr = await asyncio.gather(
                _drain(process.stdout, MAX_COMMAND_OUTPUT_BYTES),
                _drain(process.stderr, MAX_COMMAND_OUTPUT_BYTES),
            )
            exit_code = await process.wait()
            return ExecutionResult(
                ok=False, exit_code=exit_code, stdout=_decode(stdout), stderr=_COMMAND_TIMED_OUT
            )
        exit_code = await process.wait()
        return ExecutionResult(
            ok=(exit_code == 0), exit_code=exit_code, stdout=_decode(stdout), stderr=_decode(stderr)
        )

    async def collect_evidence(
        self, workspace: WorkspaceHandle, path: str, /, *, idempotency_key: str
    ) -> EvidenceFile:
        """Read any file of the workspace back as evidence with its sha256 hash."""
        root = self._workspace_dir(workspace)
        parts = _safe_parts(path)
        resolved = root.joinpath(*parts).resolve()
        _require_inside(root, resolved)
        if not resolved.is_file():
            raise KeyError(f"no evidence file {path!r} in workspace {workspace.workspace_id!r}")
        content = resolved.read_bytes()
        return EvidenceFile(
            path="/".join(parts), content_hash=sha256(content).hexdigest(), content=content
        )

    async def collect_changes(
        self, workspace: WorkspaceHandle, /, *, idempotency_key: str
    ) -> Mapping[str, bytes]:
        """The workspace's current delta against the pinned revision (the read half of publication).

        ``git status`` against the worktree's HEAD — pinned at the requested
        revision — lists modified and untracked files; deletions are skipped
        (not expressible in the file set, TD-024). The mapping is read from disk
        on every call and is a fresh copy: a later write never mutates a
        previously returned one.
        """
        root = self._workspace_dir(workspace)
        listing = await self._run_git(
            root, ("status", "--porcelain=v1", "-z", "--untracked-files=all"), "status"
        )
        changes: dict[str, bytes] = {}
        for name in _porcelain_paths(listing):
            relative = PurePosixPath(name)
            if relative.is_absolute() or any(part in _UNSAFE_PARTS for part in relative.parts):
                continue  # never expected from git; skipped defensively
            target = root.joinpath(*relative.parts)
            try:
                inside = target.resolve().is_relative_to(root.resolve())
            except OSError:
                inside = False
            if not inside or not target.is_file():
                continue
            changes[name] = target.read_bytes()
        return changes

    # --- internals ---------------------------------------------------------

    def _workspace_path(self, workspace_id: str) -> Path:
        return self._config.root / WORKSPACES_DIR_NAME / workspace_id

    def _workspace_dir(self, workspace: WorkspaceHandle) -> Path:
        workspace_id = workspace.workspace_id
        if (
            not workspace_id
            or workspace_id in _UNSAFE_PARTS
            or PurePosixPath(workspace_id).name != workspace_id
        ):
            raise KeyError(f"unknown workspace {workspace_id!r}")
        directory = self._workspace_path(workspace_id)
        if not directory.is_dir():
            raise KeyError(f"unknown workspace {workspace_id!r}")
        return directory

    def _mirror_path(self, repository: RepositoryRef) -> Path:
        return self._config.mirror_root / repository.provider.value / repository.slug

    async def _mint(self, request: WorkspaceRequest, path: Path) -> None:
        """Fetch the mirror, prune stale registrations and detach a worktree at the revision."""
        mirror = self._mirror_path(request.repository)
        if not ((mirror / ".git").exists() or (mirror / "HEAD").exists()):
            raise WorkspaceError("the source repository is missing from the mirror root")
        remotes = await self._run_git(mirror, ("remote",), "remote")
        if "origin" in remotes.split():
            await self._run_git(mirror, ("fetch", "origin"), "fetch")
        await self._run_git(mirror, ("worktree", "prune"), "worktree prune")
        path.parent.mkdir(parents=True, exist_ok=True)
        await self._run_git(
            mirror,
            ("worktree", "add", "--detach", str(path), request.revision),
            "worktree add",
        )

    async def _is_valid_worktree(self, path: Path) -> bool:
        """True when ``path`` is a usable worktree rooted exactly at itself.

        ``--show-toplevel`` (not ``--is-inside-work-tree``) is the check: a
        truncated or foreign directory under a managed parent must not pass as
        a workspace just because some ancestor happens to be a repository.
        """
        process = await asyncio.create_subprocess_exec(
            "git",
            "rev-parse",
            "--show-toplevel",
            cwd=path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await process.communicate()
        if process.returncode != 0:
            return False
        return Path(stdout.decode("utf-8", errors="replace").strip()).resolve() == path.resolve()

    async def _run_git(self, cwd: Path, argv: tuple[str, ...], step: str) -> str:
        """Run one git step in ``cwd`` and return stdout; a failure stays value-free (ADR-009)."""
        process = await asyncio.create_subprocess_exec(
            "git",
            *argv,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await process.communicate()
        if process.returncode != 0:
            raise WorkspaceError(f"git {step} failed")
        return stdout.decode("utf-8", errors="replace")
