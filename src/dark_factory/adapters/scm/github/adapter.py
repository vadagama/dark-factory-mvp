"""``GitHubAdapter``: the GitHub provider as one set of ports (T-024, ADR-019 p.3).

One adapter owns one authenticated HTTP client; the four ports it exposes —
``repository``, ``pull_requests``, ``pipelines`` and ``ci`` — share it, so
installation tokens are fetched, cached and refreshed in exactly one place.
When the configuration names a repository (``repository_slug``), it
exposes ``ci_stage_toggles`` as well — the same client and the same
installation token, now used to read and switch the CI stage variables of that
repository (T059, ADR-027); without a slug that port stays ``None`` and the
console is told the toggles are unconfigured (fail-closed).
The token source and HTTP transport are injectable: tests bind the contract
emulator (``httpx2.MockTransport``) and a static token, production wiring uses
``GitHubConfig.from_env`` with the GitHub App credentials.

Deferred on purpose (not in the port contract): webhooks as the event-driven
accelerator (later task), push/diff/rebase helpers (the ports cover the Change
Flow needs), the GitLab twin (T-034 on the same contract suite).
"""

import httpx2

from dark_factory.adapters.scm.github.auth import GitHubAppAuth, TokenProvider
from dark_factory.adapters.scm.github.ci import GitHubCI
from dark_factory.adapters.scm.github.client import GitHubClient
from dark_factory.adapters.scm.github.config import GitHubConfig
from dark_factory.adapters.scm.github.pipelines import GitHubPipelines
from dark_factory.adapters.scm.github.pull_requests import GitHubPullRequests
from dark_factory.adapters.scm.github.repository import GitHubRepository
from dark_factory.adapters.scm.github.variables import GitHubCiStageToggles
from dark_factory.ports import CiStageTogglePort


class GitHubAdapter:
    """GitHub provider adapter: source-control and CI ports over the REST API."""

    def __init__(
        self,
        config: GitHubConfig,
        *,
        token_provider: TokenProvider | None = None,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        """Build the four ports over one client.

        ``token_provider`` replaces the GitHub App flow (tests, offline
        wiring); ``transport`` replaces the HTTP transport (the contract
        emulator). With neither, authentication is the configured App's
        installation-token flow.
        """
        auth = token_provider or GitHubAppAuth(config, transport=transport)
        client = GitHubClient(config.api_base_url, auth, transport=transport)
        self._client = client
        self.repository = GitHubRepository(client)
        self.pull_requests = GitHubPullRequests(client)
        self.pipelines = GitHubPipelines(client)
        self.ci = GitHubCI(client, workflow_id=config.workflow_id)
        self.ci_stage_toggles: CiStageTogglePort | None = (
            GitHubCiStageToggles(client, slug=config.repository_slug)
            if config.repository_slug
            else None
        )

    async def aclose(self) -> None:
        """Release the underlying HTTP connection pool."""
        await self._client.aclose()
