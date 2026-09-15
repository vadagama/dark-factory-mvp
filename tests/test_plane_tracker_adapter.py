"""Unit tests of the Plane tracker adapter (T-033, ADR-013).

The contract suite already binds the adapter to ``TrackerPort``; these tests
pin what is specific to Plane behind that port: the issue → ``Change`` mapping
(risk label, timestamps, description), the comment-based write path with its
idempotency markers, path encoding, error mapping that never echoes secrets,
the ``NoOpTracker`` degradation (FR-020) and the environment configuration.
"""

import asyncio
from datetime import UTC, datetime

import httpx2
import pytest

from dark_factory.adapters.tracker import (
    NoOpTracker,
    PlaneAPIError,
    PlaneConfig,
    PlaneTrackerAdapter,
)
from dark_factory.ports import (
    ChangeSource,
    Gate,
    Provider,
    RepositoryRef,
    RiskClass,
    TrackerPort,
)
from tests.contract.plane_api import (
    ISSUE_KEY,
    PLANE_API_BASE_URL,
    PLANE_API_TOKEN,
    PROJECT_ID,
    WORKSPACE_SLUG,
    PlaneApiEmulator,
)

REPOSITORY = RepositoryRef(provider=Provider.GITHUB, slug="small/pilot")

CREATED_AT = datetime(2026, 9, 13, 9, 30, tzinfo=UTC)


def _config(
    *,
    api_key: str = PLANE_API_TOKEN,
    base_url: str = PLANE_API_BASE_URL,
    webhook_secret: str | None = None,
    previous_webhook_secret: str | None = None,
) -> PlaneConfig:
    return PlaneConfig(
        base_url=base_url,
        workspace_slug=WORKSPACE_SLUG,
        project_id=PROJECT_ID,
        repository=REPOSITORY,
        api_key=api_key,
        webhook_secret=webhook_secret,
        previous_webhook_secret=previous_webhook_secret,
    )


def _adapter(emulator: PlaneApiEmulator, config: PlaneConfig | None = None) -> PlaneTrackerAdapter:
    return PlaneTrackerAdapter(config or _config(), transport=emulator.transport())


def _failing_transport(status_code: int, payload: dict[str, object]) -> httpx2.MockTransport:
    """Transport that answers every request with one fixed error payload."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status_code=status_code, json=payload)

    return httpx2.MockTransport(handler)


# --- reads -----------------------------------------------------------------


def test_adapter_satisfies_the_tracker_port() -> None:
    assert isinstance(_adapter(PlaneApiEmulator()), TrackerPort)


def test_get_change_maps_the_issue_to_a_domain_change() -> None:
    emulator = PlaneApiEmulator()
    emulator.seed_issue(
        "issue-1",
        key=ISSUE_KEY,
        name="Add export button",
        description="Export the run table as CSV",
        labels=("enhancement", "risk:R3"),
        created_at="2026-09-13T09:30:00+00:00",
    )

    change = asyncio.run(_adapter(emulator).get_change(ISSUE_KEY))

    assert change is not None
    assert change.id == "issue-1"
    assert change.title == "Add export button"
    assert change.description == "Export the run table as CSV"
    assert change.source is ChangeSource.TRACKER
    assert change.external_ref == ISSUE_KEY
    assert change.product == REPOSITORY
    assert change.risk_class is RiskClass.R3
    assert change.created_at == CREATED_AT


def test_get_change_without_a_risk_label_keeps_the_intake_default() -> None:
    emulator = PlaneApiEmulator()
    emulator.seed_issue("issue-1", key=ISSUE_KEY, name="Tidy the README")

    change = asyncio.run(_adapter(emulator).get_change(ISSUE_KEY))

    assert change is not None
    assert change.risk_class is RiskClass.R1
    assert change.description is None


def test_get_change_ignores_an_unknown_risk_label() -> None:
    emulator = PlaneApiEmulator()
    emulator.seed_issue("issue-1", key=ISSUE_KEY, name="Tidy the README", labels=("risk:R9",))

    change = asyncio.run(_adapter(emulator).get_change(ISSUE_KEY))

    assert change is not None
    assert change.risk_class is RiskClass.R1


def test_get_change_unknown_handle_is_none() -> None:
    assert asyncio.run(_adapter(PlaneApiEmulator()).get_change("PLANE-404")) is None


def test_get_change_resolves_the_issue_by_id_as_well_as_by_key() -> None:
    emulator = PlaneApiEmulator()
    emulator.seed_issue("issue-1", key=ISSUE_KEY, name="Add export button")

    change = asyncio.run(_adapter(emulator).get_change("issue-1"))

    assert change is not None
    assert change.id == "issue-1"
    assert change.external_ref == "issue-1"


def test_get_change_encodes_the_handle_in_the_path() -> None:
    emulator = PlaneApiEmulator()
    emulator.seed_issue("issue-1", key="PLANE 42/../etc", name="Odd handle")

    change = asyncio.run(_adapter(emulator).get_change("PLANE 42/../etc"))

    assert change is not None
    assert change.external_ref == "PLANE 42/../etc"
    # Space and slash are both percent-encoded, so the handle cannot escape its segment.
    assert any(path.endswith("/issues/PLANE%2042%2F..%2Fetc/") for path in emulator.request_paths())


# --- failures --------------------------------------------------------------


def test_get_change_maps_an_unauthorized_status_to_a_port_error() -> None:
    emulator = PlaneApiEmulator(token="another-token")

    with pytest.raises(PlaneAPIError) as error:
        asyncio.run(_adapter(emulator).get_change(ISSUE_KEY))

    assert "401" in str(error.value)
    assert PLANE_API_TOKEN not in str(error.value)


def test_get_change_error_message_echoes_neither_body_nor_secret() -> None:
    adapter = PlaneTrackerAdapter(
        _config(api_key="super-secret-token"),
        transport=_failing_transport(500, {"detail": "super-secret-token leaked"}),
    )

    with pytest.raises(PlaneAPIError) as error:
        asyncio.run(adapter.get_change(ISSUE_KEY))

    message = str(error.value)
    assert "500" in message
    assert "super-secret-token" not in message


# --- writes ----------------------------------------------------------------


def test_publish_status_comments_the_status_without_the_marker() -> None:
    emulator = PlaneApiEmulator()
    emulator.seed_issue("issue-1", key=ISSUE_KEY, name="Add export button")
    adapter = _adapter(emulator)

    asyncio.run(adapter.publish_status("issue-1", "in_progress", idempotency_key="evt-1"))

    assert emulator.visible_comments_of("issue-1") == ("factory status: in_progress",)
    assert "dark-factory:idempotency:evt-1" in emulator.comments_of("issue-1")[0]


def test_publish_status_is_idempotent_by_key() -> None:
    emulator = PlaneApiEmulator()
    emulator.seed_issue("issue-1", key=ISSUE_KEY, name="Add export button")
    adapter = _adapter(emulator)

    asyncio.run(adapter.publish_status("issue-1", "in_progress", idempotency_key="evt-1"))
    asyncio.run(adapter.publish_status("issue-1", "in_progress", idempotency_key="evt-1"))
    asyncio.run(adapter.publish_status("issue-1", "in_review", idempotency_key="evt-2"))

    assert emulator.statuses_of("issue-1") == ("in_progress", "in_review")


def test_request_approval_names_the_gate_and_is_idempotent() -> None:
    emulator = PlaneApiEmulator()
    emulator.seed_issue("issue-1", key=ISSUE_KEY, name="Add export button")
    adapter = _adapter(emulator)

    asyncio.run(adapter.request_approval("issue-1", Gate.REVIEW, idempotency_key="evt-1"))
    asyncio.run(adapter.request_approval("issue-1", Gate.REVIEW, idempotency_key="evt-1"))
    asyncio.run(adapter.request_approval("issue-1", Gate.RELEASE, idempotency_key="evt-2"))

    assert emulator.approvals_of("issue-1") == (Gate.REVIEW, Gate.RELEASE)
    assert emulator.visible_comments_of("issue-1")[0] == "factory approval requested: review"


def test_writes_to_an_unknown_issue_raise_a_port_error() -> None:
    emulator = PlaneApiEmulator()

    with pytest.raises(PlaneAPIError):
        asyncio.run(
            _adapter(emulator).publish_status("issue-404", "in_progress", idempotency_key="e")
        )


def test_close_releases_the_http_pool() -> None:
    asyncio.run(_adapter(PlaneApiEmulator()).aclose())


# --- degradation without a tracker (FR-020) --------------------------------


def test_noop_tracker_knows_nothing_and_never_fails() -> None:
    tracker = NoOpTracker()

    assert isinstance(tracker, TrackerPort)
    assert asyncio.run(tracker.get_change("PLANE-42")) is None
    assert (
        asyncio.run(tracker.publish_status("chg-001", "in_progress", idempotency_key="e")) is None
    )
    assert asyncio.run(tracker.request_approval("chg-001", Gate.CODE, idempotency_key="e")) is None


# --- end-to-end (DoD: task → Change → status reflected) --------------------


def test_e2e_issue_becomes_a_change_and_the_status_comes_back() -> None:
    emulator = PlaneApiEmulator()
    emulator.seed_issue("issue-7", key=ISSUE_KEY, name="Add export button", labels=("risk:R2",))
    adapter = _adapter(emulator)

    change = asyncio.run(adapter.get_change(ISSUE_KEY))
    assert change is not None
    assert change.risk_class is RiskClass.R2

    asyncio.run(adapter.publish_status(change.id, "in_progress", idempotency_key="evt-1"))
    asyncio.run(adapter.publish_status(change.id, "in_progress", idempotency_key="evt-1"))
    asyncio.run(adapter.request_approval(change.id, Gate.REVIEW, idempotency_key="evt-2"))

    assert emulator.statuses_of(change.id) == ("in_progress",)
    assert emulator.approvals_of(change.id) == (Gate.REVIEW,)
    assert len(emulator.visible_comments_of(change.id)) == 2


# --- configuration ---------------------------------------------------------


_ENV_KEYS = (
    "DARK_FACTORY_PLANE_BASE_URL",
    "DARK_FACTORY_PLANE_API_KEY",
    "DARK_FACTORY_PLANE_WORKSPACE_SLUG",
    "DARK_FACTORY_PLANE_PROJECT_ID",
    "DARK_FACTORY_PLANE_REPOSITORY_SLUG",
    "DARK_FACTORY_PLANE_REPOSITORY_PROVIDER",
    "DARK_FACTORY_PLANE_WEBHOOK_SECRET",
    "DARK_FACTORY_PLANE_WEBHOOK_SECRET_PREVIOUS",
)

_COMPLETE_ENV = {
    "DARK_FACTORY_PLANE_BASE_URL": "http://plane.local",
    "DARK_FACTORY_PLANE_API_KEY": "plane-token",
    "DARK_FACTORY_PLANE_WORKSPACE_SLUG": "small",
    "DARK_FACTORY_PLANE_PROJECT_ID": "project-1",
    "DARK_FACTORY_PLANE_REPOSITORY_SLUG": "small/pilot",
}


def test_config_from_env_is_none_while_variables_are_missing() -> None:
    assert PlaneConfig.from_env({}) is None
    assert PlaneConfig.missing_env_vars({}) == _ENV_KEYS[:5]

    partial = {**_COMPLETE_ENV, "DARK_FACTORY_PLANE_API_KEY": "  "}
    assert PlaneConfig.from_env(partial) is None
    assert PlaneConfig.missing_env_vars(partial) == ("DARK_FACTORY_PLANE_API_KEY",)


def test_config_from_env_reads_values_and_defaults_the_provider() -> None:
    config = PlaneConfig.from_env(_COMPLETE_ENV)

    assert config is not None
    assert config.base_url == "http://plane.local"
    assert config.workspace_slug == "small"
    assert config.project_id == "project-1"
    assert config.repository == RepositoryRef(provider=Provider.GITHUB, slug="small/pilot")
    assert config.active_webhook_secrets() == ()


def test_config_from_env_reads_the_provider_and_the_rotating_webhook_secrets() -> None:
    config = PlaneConfig.from_env(
        {
            **_COMPLETE_ENV,
            "DARK_FACTORY_PLANE_REPOSITORY_PROVIDER": "gitlab",
            "DARK_FACTORY_PLANE_WEBHOOK_SECRET": "new-secret",
            "DARK_FACTORY_PLANE_WEBHOOK_SECRET_PREVIOUS": "old-secret",
        }
    )

    assert config is not None
    assert config.repository.provider is Provider.GITLAB
    assert config.active_webhook_secrets() == ("new-secret", "old-secret")


def test_config_from_env_rejects_an_unknown_provider() -> None:
    with pytest.raises(ValueError, match="DARK_FACTORY_PLANE_REPOSITORY_PROVIDER"):
        PlaneConfig.from_env(
            {**_COMPLETE_ENV, "DARK_FACTORY_PLANE_REPOSITORY_PROVIDER": "mercurial"}
        )


def test_config_repr_hides_the_secrets() -> None:
    config = _config(
        api_key="plane-secret",
        webhook_secret="webhook-secret",
        previous_webhook_secret="webhook-secret-old",
    )

    assert "plane-secret" not in repr(config)
    assert "webhook-secret" not in repr(config)
    assert "small/pilot" in repr(config)
