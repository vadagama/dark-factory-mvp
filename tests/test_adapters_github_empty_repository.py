"""An empty product repository answers 409 on GitHub: absent, not an error (M3 live run)."""

import asyncio
import json

import httpx2 as httpx
import pytest

from dark_factory.adapters.scm.github.auth import StaticTokenProvider
from dark_factory.adapters.scm.github.client import GitHubClient
from dark_factory.adapters.scm.github.repository import GitHubRepository
from dark_factory.changes.enums import Provider
from dark_factory.changes.refs import RepositoryRef

REPO = RepositoryRef(provider=Provider.GITHUB, slug="acme/empty")


def _empty_repository_transport() -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        if "/commits" in request.url.path:
            return httpx.Response(
                409, json={"message": "Git Repository is empty.", "status": "409"}
            )
        return httpx.Response(200, content=json.dumps({}).encode())

    return httpx.MockTransport(handle)


def test_get_revision_of_an_empty_repository_is_absent() -> None:
    client = GitHubClient(
        "https://api.github.test",
        StaticTokenProvider("t"),
        transport=_empty_repository_transport(),
    )
    repository = GitHubRepository(client)
    with pytest.raises(KeyError):
        asyncio.run(repository.get_revision(REPO, "factory/chg-1"))
    with pytest.raises(KeyError):
        asyncio.run(repository.list_commits(REPO, "main"))
