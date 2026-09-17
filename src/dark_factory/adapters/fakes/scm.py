"""In-memory fakes of the source control ports (ADR-019 p.2/p.6)."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from dark_factory.ports import (
    ChangeRequestObservation,
    ChangeRequestRef,
    ChangeRequestStatus,
    HeadMismatchError,
    MergeRequestPort,
    OpenChangeRequest,
    PipelinePort,
    PipelineStatus,
    RepositoryPort,
    RepositoryRef,
    ReviewObservation,
)


def _repo_key(repository: RepositoryRef) -> tuple[str, str]:
    return (repository.provider.value, repository.slug)


class FakeRepository(RepositoryPort):
    """In-memory ``RepositoryPort``.

    ``ensure_branch`` is idempotent by ``idempotency_key``: a replay returns the
    revision recorded at the first call even if the branch head moved since. A
    call with a new key on an existing branch creates no second branch and
    returns its current head. Revisions resolve through created branches; the
    fake records no other refs.

    ``publish_commit`` lands the file set on the branch head as one commit:
    replay-dedup by ``idempotency_key`` (the SHA recorded at the first call,
    even if later calls carry different changes — same-key semantics, like
    ``ensure_branch``), an empty change set is a ``ValueError``, and a missing
    branch is a ``KeyError``. The commit's files are the parent's files merged
    with ``changes`` (deletions are not expressible, per the port contract);
    ``commits_of`` and ``commit_files`` are read views for tests, not port
    methods.
    """

    def __init__(self) -> None:
        self._branches: dict[tuple[str, str, str], str] = {}
        self._branch_keys: dict[str, str] = {}
        self._commits: dict[str, _CommitRecord] = {}
        self._commit_keys: dict[str, str] = {}
        self._next_commit = 0

    async def get_revision(self, repository: RepositoryRef, ref: str, /) -> str:
        try:
            return self._branches[(*_repo_key(repository), ref)]
        except KeyError:
            raise KeyError(f"no revision recorded for {repository.slug!r}@{ref!r}") from None

    async def ensure_branch(
        self,
        repository: RepositoryRef,
        branch: str,
        *,
        from_revision: str,
        idempotency_key: str,
    ) -> str:
        replayed = self._branch_keys.get(idempotency_key)
        if replayed is not None:
            return replayed
        head = self._branches.setdefault((*_repo_key(repository), branch), from_revision)
        self._branch_keys[idempotency_key] = head
        return head

    async def publish_commit(
        self,
        repository: RepositoryRef,
        branch: str,
        changes: Mapping[str, bytes],
        /,
        *,
        message: str,
        idempotency_key: str,
    ) -> str:
        if not changes:
            raise ValueError("publish_commit requires a non-empty change set")
        replayed = self._commit_keys.get(idempotency_key)
        if replayed is not None:
            return replayed
        try:
            parent = self._branches[(*_repo_key(repository), branch)]
        except KeyError:
            raise KeyError(f"no revision recorded for {repository.slug!r}@{branch!r}") from None
        parent_files = self._commits[parent].files if parent in self._commits else {}
        self._next_commit += 1
        sha = f"c{self._next_commit:04d}"
        self._commits[sha] = _CommitRecord(
            sha=sha, message=message, files={**parent_files, **changes}, parent=parent
        )
        self._branches[(*_repo_key(repository), branch)] = sha
        self._commit_keys[idempotency_key] = sha
        return sha

    def commits_of(self, repository: RepositoryRef, branch: str) -> tuple[str, ...]:
        """Read view: the commit SHAs of ``branch``, oldest first (not port state)."""
        sha = self._branches.get((*_repo_key(repository), branch))
        shas: list[str] = []
        while sha is not None and sha in self._commits:
            shas.append(sha)
            sha = self._commits[sha].parent
        return tuple(reversed(shas))

    def commit_files(self, sha: str) -> dict[str, bytes]:
        """Read view: the file set a commit carries (not port state)."""
        try:
            return dict(self._commits[sha].files)
        except KeyError:
            raise KeyError(f"unknown commit {sha!r}") from None


@dataclass
class _CommitRecord:
    """Provider-side state of one commit created by the fake."""

    sha: str
    message: str
    files: dict[str, bytes]
    parent: str | None


@dataclass
class _ChangeRequestRecord:
    """Provider-side state of one change request opened by the fake."""

    repository: RepositoryRef
    number: int
    change_id: str
    head_sha: str
    status: ChangeRequestStatus = ChangeRequestStatus.OPEN
    comments: list[str] = field(default_factory=list)
    comment_keys: set[str] = field(default_factory=set)
    reviews: list[ReviewObservation] = field(default_factory=list)


class FakeMergeRequests(MergeRequestPort):
    """In-memory ``MergeRequestPort`` (one fake provider for GitHub PR / GitLab MR).

    - ``open`` is idempotent by ``idempotency_key``: a replay returns the same
      change request and no duplicate is created. Deduplication by
      ``(repository, change_id)`` — the FR-011 lookup — is ``find_existing``.
    - ``merge`` verifies ``expected_sha`` against the head recorded at open
      time; on mismatch it raises ``HeadMismatchError`` and changes nothing. A
      replay is a no-op: the change request is already merged.
    - ``add_comment`` is idempotent by ``idempotency_key``: a replay appends
      nothing.
    - ``observe`` reports the live record as a ``ChangeRequestObservation``:
      head, status, merge SHA and the human reviews seeded through
      ``record_review`` (a test-side helper, not port state — reviews are
      made by humans on the provider, and the port has no write for them).
    """

    def __init__(self) -> None:
        self._records: dict[tuple[str, str, int], _ChangeRequestRecord] = {}
        self._by_change: dict[tuple[str, str, str], tuple[str, str, int]] = {}
        self._open_keys: dict[str, tuple[str, str, int]] = {}
        self._numbers: dict[tuple[str, str], int] = {}

    async def open(self, request: OpenChangeRequest, *, idempotency_key: str) -> ChangeRequestRef:
        replayed = self._open_keys.get(idempotency_key)
        if replayed is not None:
            return self._ref(self._records[replayed])
        repo_key = _repo_key(request.repository)
        number = self._numbers.get(repo_key, 0) + 1
        self._numbers[repo_key] = number
        record_key = (*repo_key, number)
        record = _ChangeRequestRecord(
            repository=request.repository,
            number=number,
            change_id=request.change_id,
            head_sha=request.head_sha,
        )
        self._records[record_key] = record
        self._by_change[(*repo_key, request.change_id)] = record_key
        self._open_keys[idempotency_key] = record_key
        return self._ref(record)

    async def find_existing(
        self, repository: RepositoryRef, change_id: str, /
    ) -> ChangeRequestRef | None:
        record_key = self._by_change.get((*_repo_key(repository), change_id))
        return None if record_key is None else self._ref(self._records[record_key])

    async def add_comment(self, cr: ChangeRequestRef, body: str, *, idempotency_key: str) -> None:
        record = self._record(cr)
        if idempotency_key in record.comment_keys:
            return
        record.comment_keys.add(idempotency_key)
        record.comments.append(body)

    async def merge(self, cr: ChangeRequestRef, *, expected_sha: str, idempotency_key: str) -> None:
        record = self._record(cr)
        if record.head_sha != expected_sha:
            raise HeadMismatchError(
                f"head of {cr.repository.slug}#{cr.number} is {record.head_sha},"
                f" expected {expected_sha}"
            )
        if record.status is ChangeRequestStatus.MERGED:
            return
        record.status = ChangeRequestStatus.MERGED

    async def observe(self, cr: ChangeRequestRef, /) -> ChangeRequestObservation:
        record = self._record(cr)
        merged = record.status is ChangeRequestStatus.MERGED
        return ChangeRequestObservation(
            status=record.status,
            head_sha=record.head_sha,
            # The fake has no separate merge object: a squash merge makes the
            # merged head the merge commit.
            merged_sha=record.head_sha if merged else None,
            reviews=tuple(record.reviews),
        )

    def record_review(
        self,
        cr: ChangeRequestRef,
        *,
        author: str,
        state: str,
        commit_sha: str | None = None,
        submitted_at: datetime | None = None,
    ) -> str:
        """Seed one human review on the record (test-side, not port state).

        ``state`` is the provider-neutral value ``observe`` returns; the id is
        minted deterministically from the record's review count. Returns it.
        """
        record = self._record(cr)
        review_id = f"review-{len(record.reviews) + 1}"
        record.reviews.append(
            ReviewObservation(
                review_id=review_id,
                author=author,
                state=state,
                commit_sha=commit_sha,
                submitted_at=submitted_at,
            )
        )
        return review_id

    def comments_of(self, cr: ChangeRequestRef) -> tuple[str, ...]:
        """Read view of recorded comments (not part of the port)."""
        return tuple(self._record(cr).comments)

    def _record(self, cr: ChangeRequestRef) -> _ChangeRequestRecord:
        try:
            return self._records[(*_repo_key(cr.repository), cr.number)]
        except KeyError:
            raise KeyError(f"unknown change request {cr.repository.slug!r}#{cr.number}") from None

    def _ref(self, record: _ChangeRequestRecord) -> ChangeRequestRef:
        return ChangeRequestRef(
            repository=record.repository,
            number=record.number,
            url=f"https://scm.fake/{record.repository.slug}/cr/{record.number}",
            status=record.status,
        )


class FakePipelines(PipelinePort):
    """In-memory ``PipelinePort`` with a deterministic default state.

    Unknown refs report ``queued`` with no URL; a real provider reports the
    observed pipeline state for the requested ref.
    """

    async def status(self, repository: RepositoryRef, ref: str, /) -> PipelineStatus:
        return PipelineStatus(ref=ref, status="queued", url=None)
