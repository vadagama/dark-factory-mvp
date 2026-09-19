"""``ProviderClone``: fail-closed credentials and the shared mirror convention (T068, ADR-031).

The contract suite drives ``ProviderClone`` through the port over the loopback git
emulator; this module pins the risk points the contract cannot see:

* credentials never reach git on the command line or in the URL — the installation
  token travels only inside the child-process environment (ADR-009), and neither a
  missing App configuration nor a rejected token echoes a secret;
* a repository the App is not installed on fails with an actionable instruction;
* the mirror the adapter prepares lands exactly where the execution adapter looks
  (the variable and the layout are duplicated between the two, so the agreement needs
  a pin) and is mintable by ``WorktreeExecution`` without any credential.

The mintability check runs against an emulator that does not require a token: the
execution adapter refreshes a mirror's ``origin`` without credentials by design
(ADR-009), so the remote it can pull from is a credential-free one.
"""

import asyncio
import base64
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest

from dark_factory.adapters.scm.github import (
    MIRROR_ROOT_ENV_VAR,
    GitHubAuthError,
    GitHubConfig,
    ProviderClone,
    ProviderCloneConfig,
    StaticTokenProvider,
)
from dark_factory.changes.enums import Provider
from dark_factory.changes.refs import RepositoryRef
from dark_factory.execution import (
    WORKSPACE_MIRROR_ROOT_ENV_VAR,
    WorktreeExecution,
    WorktreeExecutionConfig,
)
from dark_factory.ports import RepositoryState, WorkspaceRequest
from tests.contract.git_http_api import GIT_INSTALLATION_TOKEN, GitHttpEmulator

REPOSITORY = RepositoryRef(provider=Provider.GITHUB, slug="small/pilot")


@pytest.fixture
def emulator() -> Iterator[GitHttpEmulator]:
    """A loopback git smart-HTTP server of one test, torn down with the test."""
    server = GitHttpEmulator()
    try:
        yield server
    finally:
        server.close()


def _credential(token: str) -> str:
    """``base64("x-access-token:<token>")`` — the value of the ``Authorization`` header."""
    return base64.b64encode(f"x-access-token:{token}".encode()).decode("ascii")


def _port(
    mirror_root: Path, emulator: GitHttpEmulator, *, token: str | None = None
) -> ProviderClone:
    """A clone adapter bound to the emulator, with the token the test wants it to send."""
    return ProviderClone(
        ProviderCloneConfig(
            mirror_root=mirror_root,
            github=GitHubConfig(api_base_url=emulator.base_url, clone_base_url=emulator.base_url),
        ),
        token_provider=StaticTokenProvider(token or emulator.token),
    )


# --- (a) missing App credentials: fail closed, no secret in the message ------


def test_validate_without_app_credentials_fails_closed(tmp_path: Path) -> None:
    # No App credentials at all: the adapter must not report the repository as merely
    # unavailable — the contour is misconfigured and that has to be loud (ADR-009).
    port = ProviderClone(
        ProviderCloneConfig(
            mirror_root=tmp_path / "mirror",
            github=GitHubConfig(
                api_base_url="https://api.github.test", clone_base_url="https://github.test"
            ),
        )
    )
    with pytest.raises(GitHubAuthError) as excinfo:
        asyncio.run(port.validate(REPOSITORY))
    message = str(excinfo.value)
    assert "DARK_FACTORY_GITHUB_APP" in message  # actionable: names what to set
    assert "BEGIN" not in message  # never key material


def test_ensure_mirror_without_app_credentials_fails_closed(tmp_path: Path) -> None:
    port = ProviderClone(
        ProviderCloneConfig(
            mirror_root=tmp_path / "mirror",
            github=GitHubConfig(
                api_base_url="https://api.github.test", clone_base_url="https://github.test"
            ),
        )
    )
    with pytest.raises(GitHubAuthError):
        asyncio.run(port.ensure_mirror(REPOSITORY, idempotency_key="m-1"))
    # Nothing was left behind by the refused attempt.
    assert not (tmp_path / "mirror").exists()


# --- (b) rejected token and absent repository: secret-free instructions ------


def test_validate_with_a_rejected_token_reports_unavailable(
    tmp_path: Path, emulator: GitHttpEmulator
) -> None:
    # A 401 is an availability fact (ADR-031 p.6), not a raised secret: the emulator
    # answers the wrong credential with its own challenge and the probe fails closed.
    emulator.seed(REPOSITORY, "baseline_absent")
    port = _port(tmp_path / "mirror", emulator, token="wrong-installation-token")
    result = asyncio.run(port.validate(REPOSITORY))
    assert result.state is RepositoryState.UNAVAILABLE
    assert result.head_revision is None


def test_ensure_mirror_with_a_rejected_token_is_a_secret_free_keyerror(
    tmp_path: Path, emulator: GitHttpEmulator
) -> None:
    secret = "wrong-installation-token"
    emulator.seed(REPOSITORY, "baseline_absent")
    port = _port(tmp_path / "mirror", emulator, token=secret)
    with pytest.raises(KeyError) as excinfo:
        asyncio.run(port.ensure_mirror(REPOSITORY, idempotency_key="m-1"))
    message = str(excinfo.value)
    assert secret not in message
    assert _credential(secret) not in message
    assert "x-access-token" not in message
    assert "contents:read" in message  # actionable instruction
    assert "installed" in message
    # A refused attempt leaves no mirror behind.
    assert not (tmp_path / "mirror" / "github" / "small" / "pilot").exists()


def test_ensure_mirror_of_a_repository_outside_the_installation_is_a_keyerror(
    tmp_path: Path, emulator: GitHttpEmulator
) -> None:
    # The App is not installed on the repository: the provider answers 404, and the
    # adapter says what to check instead of surfacing provider text (ADR-031 p.3).
    emulator.seed(REPOSITORY, "unavailable")
    port = _port(tmp_path / "mirror", emulator)
    with pytest.raises(KeyError) as excinfo:
        asyncio.run(port.ensure_mirror(REPOSITORY, idempotency_key="m-1"))
    assert "installed" in str(excinfo.value)


# --- (c) the installation token never leaves the child environment -----------


def test_the_token_travels_only_through_the_child_environment(
    tmp_path: Path, emulator: GitHttpEmulator, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = GIT_INSTALLATION_TOKEN  # the credential this emulator accepts
    emulator.seed(REPOSITORY, "baseline_absent")
    calls: list[tuple[tuple[str, ...], Mapping[str, str] | None]] = []
    real = asyncio.create_subprocess_exec

    async def recorder(*argv: str, **kwargs: Any) -> Any:
        calls.append((argv, kwargs.get("env")))
        return await real(*argv, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", recorder)
    port = _port(tmp_path / "mirror", emulator, token=secret)
    asyncio.run(port.ensure_mirror(REPOSITORY, idempotency_key="m-1"))

    assert calls, "the clone and its local probes must have run"
    credential = _credential(secret)
    for argv, _ in calls:
        assert "x-access-token" not in " ".join(argv)
        assert credential not in " ".join(argv)
        assert secret not in " ".join(argv)
    carrying = [env for _, env in calls if env is not None]
    assert carrying, "the provider call must carry the credential environment"
    for env in carrying:
        # The raw token appears in no environment value: it is only inside the
        # base64 header value, which git sends to the configured host and nowhere else.
        assert all(secret not in value for value in env.values())
        holders = [name for name, value in env.items() if credential in value]
        assert holders == ["GIT_CONFIG_VALUE_0"]
        assert env["GIT_CONFIG_VALUE_0"] == f"AUTHORIZATION: basic {credential}"
        assert env["GIT_CONFIG_KEY_0"] == f"http.{emulator.base_url}/.extraheader"
        assert env["GIT_TERMINAL_PROMPT"] == "0"
    # The URL git is addressed at is credential-free.
    assert emulator.clone_url(REPOSITORY) in calls[0][0]


def test_the_token_header_is_scoped_to_the_configured_clone_host() -> None:
    # The extraheader is scoped to the clone base URL, not to every http remote: a
    # request redirected to another host must not receive the installation token.
    config = ProviderCloneConfig(
        mirror_root=Path("/tmp/mirror"),
        github=GitHubConfig(clone_base_url="https://github.example.com/"),
    )
    port = ProviderClone(config, token_provider=StaticTokenProvider(GIT_INSTALLATION_TOKEN))
    env = port._git_env(GIT_INSTALLATION_TOKEN)
    assert env["GIT_CONFIG_KEY_0"] == "http.https://github.example.com/.extraheader"
    credential = _credential(GIT_INSTALLATION_TOKEN)
    assert env["GIT_CONFIG_VALUE_0"] == f"AUTHORIZATION: basic {credential}"


# --- config: absent var means absent adapter, a typo fails closed ------------


def test_config_from_env_is_absent_without_the_mirror_root() -> None:
    assert ProviderCloneConfig.from_env({}) is None


def test_config_from_env_fails_closed_on_a_relative_mirror_root() -> None:
    env = {MIRROR_ROOT_ENV_VAR: "relative/mirror"}
    with pytest.raises(ValueError) as excinfo:
        ProviderCloneConfig.from_env(env)
    message = str(excinfo.value)
    assert MIRROR_ROOT_ENV_VAR in message  # names the variable ...
    assert "relative/mirror" not in message  # ... never the value (ADR-009)


def test_config_from_env_is_absent_while_the_app_credentials_are_incomplete() -> None:
    env = {MIRROR_ROOT_ENV_VAR: "/tmp/mirror"}
    assert ProviderCloneConfig.from_env(env) is None


def test_config_from_env_reads_the_clone_host() -> None:
    env = {
        MIRROR_ROOT_ENV_VAR: "/tmp/mirror",
        "DARK_FACTORY_GITHUB_APP_ID": "123",
        "DARK_FACTORY_GITHUB_APP_PRIVATE_KEY": "key",
        "DARK_FACTORY_GITHUB_INSTALLATION_ID": "42",
        "DARK_FACTORY_GITHUB_CLONE_URL": "https://github.example.com",
    }
    config = ProviderCloneConfig.from_env(env)
    assert config is not None
    assert config.mirror_root == Path("/tmp/mirror")
    assert config.github.clone_base_url == "https://github.example.com"


def test_the_default_clone_host_is_the_public_one() -> None:
    env = {
        MIRROR_ROOT_ENV_VAR: "/tmp/mirror",
        "DARK_FACTORY_GITHUB_APP_ID": "123",
        "DARK_FACTORY_GITHUB_APP_PRIVATE_KEY": "key",
        "DARK_FACTORY_GITHUB_INSTALLATION_ID": "42",
    }
    config = ProviderCloneConfig.from_env(env)
    assert config is not None
    assert config.github.clone_base_url == "https://github.com"


# --- (d) the prepared mirror is the execution adapter's mirror ---------------


def test_the_mirror_root_variable_is_the_one_execution_reads() -> None:
    assert MIRROR_ROOT_ENV_VAR == WORKSPACE_MIRROR_ROOT_ENV_VAR


def test_a_provider_clone_mirror_is_mintable_by_the_execution_adapter(tmp_path: Path) -> None:
    emulator = GitHttpEmulator(require_auth=False)
    try:
        revision = emulator.seed(REPOSITORY, "baseline_absent")
        assert revision is not None
        mirror_root = tmp_path / "mirror"
        ref = asyncio.run(
            _port(mirror_root, emulator).ensure_mirror(REPOSITORY, idempotency_key="m-1")
        )
        assert ref.location == str(mirror_root / "github" / "small" / "pilot")
        assert ref.default_branch == "main"
        assert ref.head_revision == revision

        execution = WorktreeExecution(
            WorktreeExecutionConfig(root=tmp_path / "workspaces", mirror_root=mirror_root)
        )
        handle = asyncio.run(
            execution.prepare_workspace(
                WorkspaceRequest(repository=REPOSITORY, revision=revision, change_id="chg-001"),
                idempotency_key="ws-1",
            )
        )
        assert handle.repository == REPOSITORY
        assert handle.revision == revision
    finally:
        emulator.close()
