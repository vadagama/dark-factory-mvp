"""Role tools bound to one isolated workspace (T-092 S2, ADR-007 p.3).

An ``AgentProfile`` declares its tools by name only (``profile.tools``); binding
those names to callables is the harness wiring ADR-007 p.3 left open. This module
is that binding for the three core roles: one :class:`WorkspaceTools` instance is
bound to the isolated workspace of a single stage attempt, and
:meth:`WorkspaceTools.tools_for` resolves the names of a profile against the
bound callables — in the profile's order, and loudly on an unknown name, because
a name without a callable would silently hand the agent fewer tools than its
profile promises.

The tools speak ``ExecutionPort`` (prepare/write/run/evidence), never a command
line of their own: the workspace is isolated by the port, so a tool cannot touch
anything outside it. Workspace-relative paths are validated here (``resolve_path``)
so a tool call can never escape the workspace through ``..`` or an absolute path
— the same path-safety rule ``execution.runs.layout`` applies to run records.

Failure policy: a command that fails is reported *to the model* as a rendered
result (exit code, stdout, stderr), never raised. An agent must see a failing
test or a rejected patch to react to it; raising would abort the whole stage on
the first non-zero exit, which is the opposite of what a coding agent needs.
"""

import hashlib
from collections.abc import Awaitable, Callable, Mapping
from typing import Final

from dark_factory.agents.profiles.manifest import AgentProfile
from dark_factory.ports import (
    ExecutionPort,
    ExecutionResult,
    WorkspaceHandle,
)

type ToolFunction = Callable[..., Awaitable[str]]
"""One role tool as the harness consumes it: an async callable returning text."""

TOOL_NAMES: Final[tuple[str, ...]] = (
    "read_file",
    "write_file",
    "apply_patch",
    "run_command",
    "run_tests",
    "search_repo",
)
"""Every tool name the built-in binders can resolve (ADR-007 core profiles)."""

_PATCH_DIRECTORY: Final[str] = ".factory/patches"
"""Workspace-relative directory holding the patch files ``apply_patch`` applies."""

_DEFAULT_TEST_COMMAND: Final[tuple[str, ...]] = ("pytest", "-q")
"""Repository check ``run_tests`` runs by default.

The blueprint/pack owns the product-specific check command (T-041/T-070); until
that wiring lands the tool runs the check every factory-managed repository
exposes. The tool is bound per stage, so a later profile or pack can override it
without touching the executor.
"""


class UnknownToolError(ValueError):
    """A profile names a tool with no callable binding: a typo must fail loudly."""


class UnsafeWorkspacePath(ValueError):
    """A tool call addressed a path outside the isolated workspace."""


def resolve_path(path: str) -> str:
    """Validate a workspace-relative path and return its normalized form.

    Absolute paths and any ``..`` segment are rejected: the isolated workspace is
    the agent's whole world, so a path that escapes it is a defect, not a bypass
    to tolerate (same rule as ``execution.runs.layout``).
    """
    normalized = path.strip().replace("\\", "/")
    if not normalized:
        raise UnsafeWorkspacePath("path must be a non-empty workspace-relative path")
    if normalized.startswith("/") or normalized.startswith("~"):
        raise UnsafeWorkspacePath(f"path {path!r} must be relative to the workspace")
    segments = [segment for segment in normalized.split("/") if segment not in ("", ".")]
    if any(segment == ".." for segment in segments):
        raise UnsafeWorkspacePath(f"path {path!r} must not escape the workspace")
    return "/".join(segments)


def _key(workspace: WorkspaceHandle, operation: str, target: str) -> str:
    """Idempotency key of one tool call: workspace + operation + target (FR-017).

    The key is the *address* of the call, and what it addresses differs by
    operation: a write carries the sha256 of its payload in ``target``, so the
    key addresses the state the call produces and a repeated write of the same
    content is a genuine no-op. Reads and command runs address their request
    (path, argv) and feed the effect ledger only: the port must still execute
    against the *current* workspace state, never replay a result cached under
    the key — the agent loop edits a file and re-runs the check, so a cached
    result would be stale (see ``ExecutionPort``).
    """
    digest = hashlib.sha256(f"{workspace.workspace_id}:{operation}:{target}".encode()).hexdigest()
    return f"tool:{operation}:{digest[:32]}"


def _render(result: ExecutionResult) -> str:
    """Model-facing rendering of a command result; the exit code is never hidden."""
    parts = [f"exit_code={result.exit_code}"]
    if result.stdout:
        parts.append(f"stdout:\n{result.stdout}")
    if result.stderr:
        parts.append(f"stderr:\n{result.stderr}")
    return "\n".join(parts)


class WorkspaceTools:
    """The bound role tools of one isolated workspace (T-092 S2).

    One instance serves one stage attempt: the workspace handle is fixed at
    construction, so every tool call is scoped to that workspace and no tool
    takes a workspace argument the model could forge.
    """

    def __init__(
        self,
        execution: ExecutionPort,
        workspace: WorkspaceHandle,
        *,
        test_command: tuple[str, ...] = _DEFAULT_TEST_COMMAND,
    ) -> None:
        self._execution = execution
        self._workspace = workspace
        self._test_command = test_command

    def tools_for(self, profile: AgentProfile) -> tuple[ToolFunction, ...]:
        """The profile's tools as bound callables, in the profile's order.

        Raises :class:`UnknownToolError` for a profile tool without a binding:
        the allowlist of a profile must never be quietly reduced (ADR-007 p.3).
        """
        return tuple(self._resolve(name) for name in profile.tools)

    def _resolve(self, name: str) -> ToolFunction:
        tool = getattr(self, name, None)
        if tool is None or name not in TOOL_NAMES:
            raise UnknownToolError(
                f"no binding for tool {name!r}; known tools: {', '.join(TOOL_NAMES)}"
            )
        return tool  # type: ignore[no-any-return]

    # --- tools -------------------------------------------------------------

    async def read_file(self, path: str) -> str:
        """Read a workspace file as text (the read half of ``collect_evidence``)."""
        target = resolve_path(path)
        evidence = await self._execution.collect_evidence(
            self._workspace, target, idempotency_key=_key(self._workspace, "read", target)
        )
        return evidence.content.decode("utf-8", errors="replace")

    async def write_file(self, path: str, content: str) -> str:
        """Write text to a workspace file, creating parent directories as needed."""
        target = resolve_path(path)
        payload = content.encode("utf-8")
        # Content-addressed, not length-addressed: two payloads of equal size
        # must not share a key, or an adapter that deduplicates by key would
        # silently drop the second write (FR-017).
        digest = hashlib.sha256(payload).hexdigest()
        await self._execution.write_file(
            self._workspace,
            target,
            payload,
            idempotency_key=_key(self._workspace, "write", f"{target}:{digest}"),
        )
        return f"wrote {target} ({len(payload)} bytes)"

    async def apply_patch(self, patch: str) -> str:
        """Apply a unified diff inside the workspace through ``git apply``.

        The patch is first written to a deterministic path of its own content, so
        the same patch always resolves to the same file and a replay does not
        leave a second copy behind.
        """
        digest = hashlib.sha256(patch.encode()).hexdigest()[:32]
        patch_path = resolve_path(f"{_PATCH_DIRECTORY}/{digest}.patch")
        await self._execution.write_file(
            self._workspace,
            patch_path,
            patch.encode("utf-8"),
            idempotency_key=_key(self._workspace, "patch", digest),
        )
        result = await self._execution.run_command(
            self._workspace,
            ("git", "apply", "--whitespace=nowarn", "--", patch_path),
            idempotency_key=_key(self._workspace, "apply", digest),
        )
        return _render(result)

    async def run_command(self, argv: list[str]) -> str:
        """Run a command inside the workspace; a failure is reported, not raised."""
        if not argv:
            raise ValueError("run_command requires a non-empty argv")
        result = await self._execution.run_command(
            self._workspace,
            tuple(argv),
            idempotency_key=_key(self._workspace, "run", "\u0000".join(argv)),
        )
        return _render(result)

    async def run_tests(self) -> str:
        """Run the repository check command inside the workspace."""
        result = await self._execution.run_command(
            self._workspace,
            self._test_command,
            idempotency_key=_key(self._workspace, "tests", "\u0000".join(self._test_command)),
        )
        return _render(result)

    async def search_repo(self, pattern: str) -> str:
        """Search the workspace for ``pattern`` (a plain, literal grep)."""
        if not pattern.strip():
            raise ValueError("search_repo requires a non-empty pattern")
        result = await self._execution.run_command(
            self._workspace,
            ("grep", "-rn", "-F", "--", pattern, "."),
            idempotency_key=_key(self._workspace, "search", pattern),
        )
        if result.exit_code == 1 and not result.stdout:
            # grep found nothing: a normal answer, not a command failure.
            return "no matches"
        return _render(result)


def tools_for(profile: AgentProfile, tools: WorkspaceTools) -> Mapping[str, ToolFunction]:
    """Name → callable of the profile's tools, keyed by the profile's own names.

    The single place that turns the declarative allowlist of a profile into the
    mapping the harness needs; keeping the names as keys lets a caller log or
    assert exactly which tools a role received (``tools_for(profile).keys()``).
    """
    return dict(zip(profile.tools, tools.tools_for(profile), strict=True))
