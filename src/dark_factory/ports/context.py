"""Context assembly and workspace execution DTOs behind Knowledge/Execution ports (T-012).

Minimal request/handle/result types: search and traversal contracts arrive
with the real source providers (YAGNI). Frozen pydantic models per the port
DTO pattern; ``RepositoryRef`` is the shared domain reference (ADR-019).
"""

from pydantic import BaseModel, ConfigDict, Field

from dark_factory.changes.refs import RepositoryRef


class ContextRequest(BaseModel):
    """Input of ``KnowledgePort.collect``."""

    model_config = ConfigDict(frozen=True)

    change_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)


class WorkspaceRequest(BaseModel):
    """Input of ``ExecutionPort.prepare_workspace``: an isolated worktree from a pinned revision."""

    model_config = ConfigDict(frozen=True)

    repository: RepositoryRef
    revision: str = Field(min_length=1)
    change_id: str = Field(min_length=1)


class WorkspaceHandle(BaseModel):
    """Reference to a workspace prepared by ``ExecutionPort.prepare_workspace``."""

    model_config = ConfigDict(frozen=True)

    workspace_id: str = Field(min_length=1)
    repository: RepositoryRef
    revision: str = Field(min_length=1)


class ExecutionResult(BaseModel):
    """Outcome of ``ExecutionPort.run_command``."""

    model_config = ConfigDict(frozen=True)

    ok: bool
    exit_code: int
    stdout: str = ""
    stderr: str = ""


class EvidenceFile(BaseModel):
    """File collected by ``ExecutionPort.collect_evidence``: content plus its sha256 hash."""

    model_config = ConfigDict(frozen=True)

    path: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)
    content: bytes
