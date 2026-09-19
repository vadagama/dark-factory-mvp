"""Document artifacts of a change over ``RepositoryPort`` (T082-T085, ADR-035).

The service behind ``GET/PUT /changes/{id}/artifacts...`` and ``factory change
artifacts``: it reads the ChangeSet tree, a document at a revision, the
revisions of a document and the diff between two of them from the product
repository — git is the source of truth (ADR-035 p.1) — and lands an edit as
**one commit** on the change branch through ``RepositoryPort.publish_commit``
(ADR-035 p.3), idempotent by the caller's key.

What is decided here and nowhere else:

* **where** the artifacts are: the task branch of the change
  (``stages.agent.branch_name``) and the ``.factory/changes`` subtree
  (``context.artifacts.CHANGES_ROOT``); "no branch yet" and "branch without
  artifacts" are different answers (``ArtifactTree.revision``);
* **absent vs stale**: a missing document is a ``KeyError`` from the port and
  an :class:`ArtifactNotFoundError` here; a caller that names a
  ``base_revision`` older than the head gets an
  :class:`ArtifactConflictError` **only** when the file changed in between —
  a head that moved without touching the file is not a conflict (ADR-035 p.3:
  a conflict is never resolved silently, and a non-conflict is never invented);
* the edit message and the idempotency key: the commit carries the change id
  and the path, the key is the caller's (``Idempotency-Key``) or a digest of
  the content and the base revision, so a retried save lands once.

Synchronous like the driver's seams (``asyncio.run`` over the async port); the
API and the CLI call it inside their own transaction and store the draft and
the discussion effects themselves.
"""

import asyncio
import hashlib
from collections.abc import Sequence
from typing import Final

from dark_factory.changes.run import Change
from dark_factory.context.artifacts import (
    CHANGES_ROOT,
    ArtifactDiff,
    ArtifactDocument,
    ArtifactNode,
    ArtifactRevision,
    ArtifactTree,
    anchors_of,
    classify_path,
    document_properties,
    is_changeset_artifact,
    unified_diff,
)
from dark_factory.orchestration.stages.agent import DEFAULT_BRANCH_PREFIX, branch_name
from dark_factory.ports import RepositoryPort

__all__ = [
    "ArtifactConflictError",
    "ArtifactNotFoundError",
    "ArtifactService",
    "ArtifactWrite",
    "edit_effect_key",
]

_EDIT_EFFECT: Final[str] = "artifact-edit"


class ArtifactNotFoundError(KeyError):
    """The change has no branch, or the artifact is not at the requested revision."""


class ArtifactConflictError(RuntimeError):
    """The artifact changed after ``base_revision``: the edit is not applied (ADR-035 p.3)."""

    def __init__(self, path: str, base_revision: str | None, head_revision: str) -> None:
        super().__init__(
            f"{path} changed after revision {base_revision or '<none>'}: the branch head is now "
            f"{head_revision}; reload the document and re-apply the edit"
        )
        self.path = path
        self.base_revision = base_revision
        self.head_revision = head_revision


def edit_effect_key(change_id: str, path: str, content: str, base_revision: str | None) -> str:
    """Deterministic idempotency key of one save without a caller-supplied key."""
    digest = hashlib.sha256(f"{base_revision or ''}\n{content}".encode()).hexdigest()[:24]
    return f"{_EDIT_EFFECT}:{change_id}:{path}:{digest}"


class ArtifactWrite:
    """Outcome of one save: the new revision and whether a commit was created."""

    __slots__ = ("path", "previous_revision", "revision")

    def __init__(self, *, path: str, revision: str, previous_revision: str | None) -> None:
        self.path = path
        self.revision = revision
        self.previous_revision = previous_revision


class ArtifactService:
    """Read model and write-through of the ChangeSet artifacts of a change (ADR-035)."""

    def __init__(
        self, repository: RepositoryPort, *, branch_prefix: str = DEFAULT_BRANCH_PREFIX
    ) -> None:
        self._repository = repository
        self._branch_prefix = branch_prefix

    def branch_of(self, change: Change) -> str:
        return branch_name(change.id, prefix=self._branch_prefix)

    # --- reads ---------------------------------------------------------------

    def head(self, change: Change) -> str | None:
        """Current revision of the change branch, or ``None`` before the branch exists."""
        return asyncio.run(self._head(change))

    def tree(self, change: Change) -> ArtifactTree:
        """The ChangeSet artifacts on the change branch at its current head."""
        return asyncio.run(self._tree(change))

    def get(self, change: Change, path: str, *, revision: str | None = None) -> ArtifactDocument:
        """One document at ``revision`` (default: the branch head), parsed."""
        return asyncio.run(self._get(change, path, revision))

    def read_many(
        self, change: Change, paths: Sequence[str], *, revision: str | None = None
    ) -> dict[str, str | None]:
        """Current text of several artifacts (``None`` = absent) for anchor resolution."""
        return asyncio.run(self._read_many(change, paths, revision))

    def versions(self, change: Change, path: str) -> list[ArtifactRevision]:
        """Revisions of one document on the change branch, newest first."""
        return asyncio.run(self._versions(change, path))

    def latest_revision(self, change: Change, paths: Sequence[str]) -> str | None:
        """The newest commit that touched any of ``paths`` — the revision of a phase (M3).

        A phase approval binds to the revision of the phase's *own* artifacts
        (ADR-039), so a later round of another phase does not stale it. ``None``
        without a branch or when none of the paths has a commit yet.
        """
        return asyncio.run(self._latest_revision(change, paths))

    def diff(
        self, change: Change, path: str, *, from_revision: str, to_revision: str
    ) -> ArtifactDiff:
        """Unified diff of one document between two revisions."""
        return asyncio.run(self._diff(change, path, from_revision, to_revision))

    # --- write-through ---------------------------------------------------------

    def write(
        self,
        change: Change,
        path: str,
        content: str,
        *,
        base_revision: str | None,
        actor: str,
        idempotency_key: str | None = None,
        message: str | None = None,
    ) -> ArtifactWrite:
        """Land ``content`` as one commit on the change branch (ADR-035 p.3).

        ``base_revision`` is the revision the operator edited from; when the
        branch head moved *and* the file differs between the base and the head,
        the edit is refused with :class:`ArtifactConflictError` — never merged
        silently. Unchanged content is a no-op that reports the head. The
        branch is created from the change's base ref when it does not exist yet
        (a first edit before any agent stage ran).
        """
        return asyncio.run(
            self._write(
                change,
                path,
                content,
                base_revision=base_revision,
                actor=actor,
                idempotency_key=idempotency_key,
                message=message,
            )
        )

    # --- async internals -------------------------------------------------------

    async def _head(self, change: Change) -> str | None:
        try:
            return await self._repository.get_revision(change.product, self.branch_of(change))
        except KeyError:
            return None

    async def _tree(self, change: Change) -> ArtifactTree:
        branch = self.branch_of(change)
        head = await self._head(change)
        if head is None:
            return ArtifactTree(change_id=change.id, branch=branch, revision=None, nodes=())
        paths = await self._repository.list_tree(change.product, head, prefix=f"{CHANGES_ROOT}/")
        nodes = tuple(
            ArtifactNode(path=path, kind=classify_path(path), revision=head)
            for path in paths
            if is_changeset_artifact(path)
        )
        return ArtifactTree(change_id=change.id, branch=branch, revision=head, nodes=nodes)

    async def _resolve_revision(self, change: Change, revision: str | None) -> str:
        if revision is not None:
            return revision
        head = await self._head(change)
        if head is None:
            raise ArtifactNotFoundError(f"change {change.id!r} has no branch yet")
        return head

    async def _read(self, change: Change, path: str, revision: str) -> str | None:
        try:
            raw = await self._repository.read_file(change.product, revision, path)
        except KeyError:
            return None
        return raw.decode("utf-8", errors="replace")

    async def _get(self, change: Change, path: str, revision: str | None) -> ArtifactDocument:
        resolved = await self._resolve_revision(change, revision)
        content = await self._read(change, path, resolved)
        if content is None:
            raise ArtifactNotFoundError(f"artifact {path!r} is not at revision {resolved}")
        properties, body, error = document_properties(content)
        return ArtifactDocument(
            path=path,
            kind=classify_path(path),
            revision=resolved,
            content=content,
            properties=properties,
            body=body,
            anchors=anchors_of(content),
            frontmatter_error=error,
        )

    async def _read_many(
        self, change: Change, paths: Sequence[str], revision: str | None
    ) -> dict[str, str | None]:
        try:
            resolved = await self._resolve_revision(change, revision)
        except ArtifactNotFoundError:
            return dict.fromkeys(paths)
        return {path: await self._read(change, path, resolved) for path in paths}

    async def _versions(self, change: Change, path: str) -> list[ArtifactRevision]:
        head = await self._head(change)
        if head is None:
            return []
        commits = await self._repository.list_commits(change.product, head, path=path)
        return [
            ArtifactRevision(
                revision=commit.sha,
                message=commit.message,
                author=commit.author,
                authored_at=commit.authored_at,
            )
            for commit in commits
        ]

    async def _latest_revision(self, change: Change, paths: Sequence[str]) -> str | None:
        head = await self._head(change)
        if head is None or not paths:
            return None
        heads: set[str] = set()
        for path in paths:
            commits = await self._repository.list_commits(change.product, head, path=path)
            if commits:
                heads.add(commits[0].sha)
        if not heads:
            return None
        if len(heads) == 1:
            return next(iter(heads))
        # Several paths, several latest commits: the newest is the first one the
        # branch history (newest first) names among them.
        for commit in await self._repository.list_commits(change.product, head):
            if commit.sha in heads:
                return commit.sha
        return head

    async def _diff(
        self, change: Change, path: str, from_revision: str, to_revision: str
    ) -> ArtifactDiff:
        before = await self._read(change, path, from_revision)
        after = await self._read(change, path, to_revision)
        if before is None and after is None:
            raise ArtifactNotFoundError(f"artifact {path!r} is at neither revision")
        return unified_diff(
            before or "",
            after or "",
            path=path,
            from_revision=from_revision,
            to_revision=to_revision,
        )

    async def _write(
        self,
        change: Change,
        path: str,
        content: str,
        *,
        base_revision: str | None,
        actor: str,
        idempotency_key: str | None,
        message: str | None,
    ) -> ArtifactWrite:
        branch = self.branch_of(change)
        head = await self._head(change)
        if head is None:
            base = await self._repository.get_revision(change.product, "main")
            head = await self._repository.ensure_branch(
                change.product,
                branch,
                from_revision=base,
                idempotency_key=f"{_EDIT_EFFECT}:{change.id}:branch",
            )
        current = await self._read(change, path, head)
        if current is not None and current == content:
            # Already the head's content: an unchanged save, or the retry of a
            # save whose first attempt landed — nothing to commit, no conflict.
            return ArtifactWrite(path=path, revision=head, previous_revision=head)
        if base_revision is not None and base_revision != head:
            at_base = await self._read(change, path, base_revision)
            if at_base != current:
                raise ArtifactConflictError(path, base_revision, head)
        key = idempotency_key or edit_effect_key(change.id, path, content, base_revision)
        revision = await self._repository.publish_commit(
            change.product,
            branch,
            {path: content.encode("utf-8")},
            message=message or f"docs({change.id}): edit {path} by {actor}",
            idempotency_key=key,
        )
        return ArtifactWrite(path=path, revision=revision, previous_revision=head)
