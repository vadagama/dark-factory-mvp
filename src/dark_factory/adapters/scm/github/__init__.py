"""GitHub adapter of the source-control and CI ports (T-024, ADR-019 p.3).

Single public surface of the GitHub provider: the four ports behind
``GitHubAdapter`` (``repository``, ``pull_requests``, ``pipelines``, ``ci``),
the repository-provisioning adapter ``ProviderClone`` on a GitHub App
installation token (T068, ADR-031), its configuration (``GitHubConfig``, GitHub
App credentials from environment) and the injectable authentication surface
(``GitHubAppAuth``, ``StaticTokenProvider``). The adapter imports only
``dark_factory.ports`` and its own submodules (ADR-015 p.3); the REST API is
reached through httpx2.
"""

from dark_factory.adapters.scm.github.adapter import GitHubAdapter
from dark_factory.adapters.scm.github.auth import (
    GitHubAppAuth,
    GitHubAuthError,
    InstallationTokenFetcher,
    StaticTokenProvider,
    TokenProvider,
)
from dark_factory.adapters.scm.github.ci import CHECK_RUN_NAME_TEMPLATE, GitHubCI
from dark_factory.adapters.scm.github.client import GitHubAPIError, GitHubClient
from dark_factory.adapters.scm.github.config import (
    DEFAULT_API_BASE_URL,
    DEFAULT_CLONE_BASE_URL,
    DEFAULT_WORKFLOW_ID,
    GITHUB_API_URL_ENV_VAR,
    GITHUB_APP_ID_ENV_VAR,
    GITHUB_APP_PRIVATE_KEY_ENV_VAR,
    GITHUB_CLONE_URL_ENV_VAR,
    GITHUB_INSTALLATION_ID_ENV_VAR,
    GITHUB_REPOSITORY_SLUG_ENV_VAR,
    GITHUB_WORKFLOW_ID_ENV_VAR,
    GitHubConfig,
)
from dark_factory.adapters.scm.github.pipelines import GitHubPipelines
from dark_factory.adapters.scm.github.provisioning import (
    BASELINE_PATH,
    MIRROR_ROOT_ENV_VAR,
    ProviderClone,
    ProviderCloneConfig,
)
from dark_factory.adapters.scm.github.pull_requests import (
    CHANGE_MARKER_PREFIX,
    CHANGE_MARKER_SUFFIX,
    IDEMPOTENCY_MARKER_TEMPLATE,
    GitHubPullRequests,
)
from dark_factory.adapters.scm.github.repository import GitHubRepository
from dark_factory.adapters.scm.github.variables import GitHubCiStageToggles

__all__ = [
    "BASELINE_PATH",
    "CHANGE_MARKER_PREFIX",
    "CHANGE_MARKER_SUFFIX",
    "CHECK_RUN_NAME_TEMPLATE",
    "DEFAULT_API_BASE_URL",
    "DEFAULT_CLONE_BASE_URL",
    "DEFAULT_WORKFLOW_ID",
    "GITHUB_API_URL_ENV_VAR",
    "GITHUB_APP_ID_ENV_VAR",
    "GITHUB_APP_PRIVATE_KEY_ENV_VAR",
    "GITHUB_CLONE_URL_ENV_VAR",
    GITHUB_INSTALLATION_ID_ENV_VAR,
    GITHUB_REPOSITORY_SLUG_ENV_VAR,
    GITHUB_WORKFLOW_ID_ENV_VAR,
    "IDEMPOTENCY_MARKER_TEMPLATE",
    "MIRROR_ROOT_ENV_VAR",
    "GitHubAPIError",
    "GitHubAdapter",
    "GitHubAppAuth",
    "GitHubAuthError",
    "GitHubCI",
    "GitHubCiStageToggles",
    "GitHubClient",
    "GitHubConfig",
    "GitHubPipelines",
    "GitHubPullRequests",
    "GitHubRepository",
    "InstallationTokenFetcher",
    "ProviderClone",
    "ProviderCloneConfig",
    "StaticTokenProvider",
    "TokenProvider",
]
