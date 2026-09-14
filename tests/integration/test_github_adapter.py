"""GitHub adapter integration test against a disposable test repository (ADR-019 p.6).

Runs only when real credentials are configured:

- ``DARK_FACTORY_TEST_GITHUB_REPOSITORY`` — ``owner/repo`` of a disposable
  test repository the App can push branches and merge pull requests to;
- ``DARK_FACTORY_TEST_GITHUB_APP_ID``,
  ``DARK_FACTORY_TEST_GITHUB_APP_PRIVATE_KEY`` — GitHub App credentials
  (PEM text; a secret, never logged);
- ``DARK_FACTORY_TEST_GITHUB_APP_INSTALLATION_ID`` — the App's installation
  on that repository.

Without them (the default locally and in CI) the test skips; the contract
suite covers the behavior offline against the API emulator. The flow is
replay-safe: reruns against the same repository reuse the branch and PR
through the idempotency mechanisms (head-branch lookup, comment markers,
merged no-op), until ``main`` advances — then the stale ``expected_sha``
raises ``HeadMismatchError`` by design.
"""

import asyncio
import os

import pytest

from dark_factory.adapters.scm.github import GitHubAdapter, GitHubConfig
from dark_factory.changes.enums import ChangeRequestStatus
from dark_factory.ports import OpenChangeRequest, Provider, RepositoryRef

REPOSITORY_ENV = "DARK_FACTORY_TEST_GITHUB_REPOSITORY"
APP_ID_ENV = "DARK_FACTORY_TEST_GITHUB_APP_ID"
PRIVATE_KEY_ENV = "DARK_FACTORY_TEST_GITHUB_APP_PRIVATE_KEY"
INSTALLATION_ID_ENV = "DARK_FACTORY_TEST_GITHUB_APP_INSTALLATION_ID"

CHANGE_ID = "chg-t024-integration"
BRANCH = "dark-factory/t-024-integration"


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} is not set; skipping GitHub integration test")
    return value


def test_adapter_roundtrip_against_test_repository() -> None:
    slug = _required_env(REPOSITORY_ENV)
    config = GitHubConfig(
        app_id=_required_env(APP_ID_ENV),
        installation_id=_required_env(INSTALLATION_ID_ENV),
        private_key=_required_env(PRIVATE_KEY_ENV),
    )
    repository = RepositoryRef(provider=Provider.GITHUB, slug=slug)
    adapter = GitHubAdapter(config)
    try:
        head = asyncio.run(adapter.repository.get_revision(repository, "main"))

        branch_head = asyncio.run(
            adapter.repository.ensure_branch(
                repository, BRANCH, from_revision=head, idempotency_key=f"{CHANGE_ID}:branch"
            )
        )
        assert branch_head == head

        request = OpenChangeRequest(
            repository=repository,
            change_id=CHANGE_ID,
            source_branch=BRANCH,
            target_branch="main",
            title="dark-factory T-024 integration",
            description="Automated roundtrip of the GitHub adapter; safe to close.",
            head_sha=head,
        )
        opened = asyncio.run(
            adapter.pull_requests.open(request, idempotency_key=f"{CHANGE_ID}:open")
        )
        assert (
            asyncio.run(adapter.pull_requests.open(request, idempotency_key=f"{CHANGE_ID}:open"))
            == opened
        )
        found = asyncio.run(adapter.pull_requests.find_existing(repository, CHANGE_ID))
        assert found is not None and found.number == opened.number

        asyncio.run(
            adapter.pull_requests.add_comment(
                opened, "dark-factory integration check", idempotency_key=f"{CHANGE_ID}:comment"
            )
        )
        asyncio.run(
            adapter.pull_requests.add_comment(
                opened, "dark-factory integration check", idempotency_key=f"{CHANGE_ID}:comment"
            )
        )

        asyncio.run(
            adapter.pull_requests.merge(
                opened, expected_sha=head, idempotency_key=f"{CHANGE_ID}:merge"
            )
        )
        asyncio.run(
            adapter.pull_requests.merge(
                opened, expected_sha=head, idempotency_key=f"{CHANGE_ID}:merge"
            )
        )
        merged = asyncio.run(adapter.pull_requests.find_existing(repository, CHANGE_ID))
        assert merged is not None and merged.status is ChangeRequestStatus.MERGED
    finally:
        asyncio.run(adapter.aclose())
