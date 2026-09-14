"""``RepositoryPort`` over the GitHub REST API (T-024, ADR-019 p.3).

Branch/revision semantics mirror the fake the contract suite runs first:
``ensure_branch`` is idempotent by ``idempotency_key`` — a replay returns the
revision recorded at the first call even if the branch head moved since; a new
key on an existing branch creates no second branch and returns its current
head; a missing branch is created at ``from_revision``. ``get_revision``
resolves branches (and raw SHAs) through ``GET /repos/.../commits/{ref}``.
"""

from dark_factory.adapters.scm.github.client import GitHubClient
from dark_factory.ports import RepositoryPort, RepositoryRef


class GitHubRepository(RepositoryPort):
    """``RepositoryPort`` backed by the Git refs API."""

    def __init__(self, client: GitHubClient) -> None:
        self._client = client
        self._branch_keys: dict[str, str] = {}

    async def get_revision(self, repository: RepositoryRef, ref: str, /) -> str:
        response = await self._client.request("GET", f"/repos/{repository.slug}/commits/{ref}")
        if response.status_code == 404:
            raise KeyError(f"no revision recorded for {repository.slug!r}@{ref!r}")
        return str(self._client.expect(response, 200).json()["sha"])

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
        ref_response = await self._client.request(
            "GET", f"/repos/{repository.slug}/git/ref/heads/{branch}"
        )
        if ref_response.status_code == 200:
            head = str(ref_response.json()["object"]["sha"])
        elif ref_response.status_code == 404:
            head = from_revision
            created = await self._client.request(
                "POST",
                f"/repos/{repository.slug}/git/refs",
                json={"ref": f"refs/heads/{branch}", "sha": from_revision},
            )
            self._client.expect(created, 201)
        else:
            self._client.expect(ref_response, 200)
        self._branch_keys[idempotency_key] = head
        return head
