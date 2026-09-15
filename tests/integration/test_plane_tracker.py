"""Plane tracker adapter integration test against a disposable Plane workspace (T-033).

Runs only when real credentials are configured:

- ``DARK_FACTORY_TEST_PLANE_BASE_URL`` — Plane web root of a disposable instance;
- ``DARK_FACTORY_TEST_PLANE_API_KEY`` — API token of the automation user
  (a secret, never logged);
- ``DARK_FACTORY_TEST_PLANE_WORKSPACE_SLUG``, ``DARK_FACTORY_TEST_PLANE_PROJECT_ID``
  — workspace and project the test issue lives in;
- ``DARK_FACTORY_TEST_PLANE_ISSUE_REF`` — handle of an existing issue the
  automation user may read and comment on;
- ``DARK_FACTORY_TEST_PLANE_REPOSITORY_SLUG`` — product repository of that task
  (the GitHub provider is assumed, as in the rest of the MVP: ADR-019 p.1).

Without them (the default locally and in CI) the test skips; the contract suite
covers the behavior offline against the API emulator. The flow is replay-safe:
reruns reuse the same idempotency keys, so no duplicate comments accumulate.
Comment-level assertions live in the emulator suite — this test proves the
adapter speaks to a real Plane: the task maps to a ``Change`` and the status and
approval writes reach the issue.
"""

import asyncio
import os

import pytest

from dark_factory.adapters.tracker import PlaneConfig, PlaneTrackerAdapter
from dark_factory.ports import ChangeSource, Gate, Provider, RepositoryRef

BASE_URL_ENV = "DARK_FACTORY_TEST_PLANE_BASE_URL"
API_KEY_ENV = "DARK_FACTORY_TEST_PLANE_API_KEY"
WORKSPACE_ENV = "DARK_FACTORY_TEST_PLANE_WORKSPACE_SLUG"
PROJECT_ENV = "DARK_FACTORY_TEST_PLANE_PROJECT_ID"
ISSUE_REF_ENV = "DARK_FACTORY_TEST_PLANE_ISSUE_REF"
REPOSITORY_ENV = "DARK_FACTORY_TEST_PLANE_REPOSITORY_SLUG"


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} is not set; skipping Plane tracker integration test")
    return value


def test_tracker_roundtrip_against_a_plane_workspace() -> None:
    issue_ref = _required_env(ISSUE_REF_ENV)
    config = PlaneConfig(
        base_url=_required_env(BASE_URL_ENV),
        workspace_slug=_required_env(WORKSPACE_ENV),
        project_id=_required_env(PROJECT_ENV),
        repository=RepositoryRef(
            provider=Provider.GITHUB,
            slug=_required_env(REPOSITORY_ENV),
        ),
        api_key=_required_env(API_KEY_ENV),
    )
    status_key = f"{issue_ref}:status:t-033-integration"
    approval_key = f"{issue_ref}:approval:t-033-integration"
    adapter = PlaneTrackerAdapter(config)
    try:
        change = asyncio.run(adapter.get_change(issue_ref))

        assert change is not None
        assert change.id
        assert change.title
        assert change.external_ref == issue_ref
        assert change.source is ChangeSource.TRACKER
        assert change.product == config.repository

        # Both writes are replay safe: the second call must be a no-op on the issue.
        asyncio.run(adapter.publish_status(change.id, "in_progress", idempotency_key=status_key))
        asyncio.run(adapter.publish_status(change.id, "in_progress", idempotency_key=status_key))
        asyncio.run(adapter.request_approval(change.id, Gate.REVIEW, idempotency_key=approval_key))
        asyncio.run(adapter.request_approval(change.id, Gate.REVIEW, idempotency_key=approval_key))
    finally:
        asyncio.run(adapter.aclose())
