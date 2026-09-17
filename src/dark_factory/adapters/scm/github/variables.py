"""``CiStageTogglePort`` over GitHub repository variables (T059, ADR-027).

GitHub Actions variables store non-secret configuration; the factory uses them
as the switchboard of its own pipeline (T058): the console writes a stage
toggle and the workflow reads it back as ``vars.CI_SKIP_<JOB>`` (ADR-026).

Credential: the deployment's GitHub App installation token carrying the
"Variables" repository permission (read/write) — the App-based flow of
``GitHubAdapter`` (ADR-019 p.3), never a long-lived PAT. The adapter is bound to
one repository slug at construction, so no request can retarget a read or a
write at another repository.

Writes are value-level and idempotent: ``set_value(name, None)`` deletes the
variable (404 means it is already absent), any other value updates it and
creates it when the API answers 404 with the variable unknown — the variables
API has no upsert.
"""

from collections.abc import Mapping
from typing import Any, Final

from dark_factory.adapters.scm.github.client import GitHubClient
from dark_factory.ports import CiStageTogglePort

PAGE_SIZE: Final[int] = 100
"""Variables per page; the REST API caps ``per_page`` at 100 for this endpoint."""


class GitHubCiStageToggles(CiStageTogglePort):
    """Repository variables of one GitHub repository exposed as the stage switchboard."""

    def __init__(self, client: GitHubClient, *, slug: str) -> None:
        self._client = client
        self._slug = slug

    async def values(self) -> Mapping[str, str]:
        """Every repository variable of the bound repository (name → value).

        Pages until the listing stops growing: stopping at the first page would
        report a switched-off stage as enabled once a repository holds more
        variables than one page carries.
        """
        collected: dict[str, str] = {}
        page = 1
        while True:
            response = await self._client.request(
                "GET",
                f"/repos/{self._slug}/actions/variables",
                params={"per_page": str(PAGE_SIZE), "page": str(page)},
            )
            payload: dict[str, Any] = self._client.expect(response, 200).json()
            variables = list(payload.get("variables", []))
            before = len(collected)
            for variable in variables:
                collected[str(variable["name"])] = str(variable.get("value", ""))
            # A short page ends the listing; an unchanged name count means the
            # provider stopped varying its answer (a wrong total_count, or a
            # provider that ignores `page`) — never loop on it.
            if len(variables) < PAGE_SIZE or len(collected) == before:
                return collected
            page += 1

    async def set_value(self, variable: str, value: str | None) -> None:
        """Set ``variable`` to ``value``, or delete it when ``value`` is ``None``.

        Both directions are idempotent: deleting an absent variable is a no-op
        (404), and writing a value the variable already holds is a plain
        update. Errors are mapped onto ``GitHubAPIError`` carrying only the
        method, path and status (ADR-009).
        """
        path = f"/repos/{self._slug}/actions/variables/{variable}"
        if value is None:
            response = await self._client.request("DELETE", path)
            if response.status_code == 404:
                return  # already absent: the wanted state already holds
            self._client.expect(response, 204)
            return
        response = await self._client.request(
            "PATCH", path, json={"name": variable, "value": value}
        )
        if response.status_code == 404:
            created = await self._client.request(
                "POST",
                f"/repos/{self._slug}/actions/variables",
                json={"name": variable, "value": value},
            )
            self._client.expect(created, 201)
            return
        self._client.expect(response, 204)
