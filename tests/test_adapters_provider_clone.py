"""``ProviderClone``: fail-closed credentials and the shared mirror convention (T068, ADR-031).

The contract suite drives ``ProviderClone`` through the port over the loopback git
emulator; this module pins the risk points the contract cannot see:

* credentials never reach git on the command line or in the URL — the installation
  token travels only inside the child-process environment (ADR-009), and neither a
  missing App configuration nor a rejected token echoes a secret;
* a repository the App is not installed on fails with an actionable instruction;
* the mirror the adapter prepares lands exactly where the execution adapter looks
  (the variable and the layout are duplicated between the two, so the agreement needs
  a pin) and is mintable by ``WorktreeExecution`` without any credential;
* the bootstrap (T069) turns an empty repository into its first commit from the real
  ``packs/`` payload, records the manifest version rather than a constant, aligns a
  stale mirror to the provider's current tip, forces an ignored ``.factory/`` in, and
  reports the packs the baseline commit records rather than the caller's request.

The mintability check runs against an emulator that does not require a token: the
execution adapter refreshes a mirror's ``origin`` without credentials by design
(ADR-009), so the remote it can pull from is a credential-free one.
"""

import asyncio
import base64
import subprocess
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml

from dark_factory.adapters.scm.github import (
    MIRROR_ROOT_ENV_VAR,
    PACKS_ROOT_ENV_VAR,
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
from dark_factory.ports import BaselineBootstrapResult, RepositoryState, WorkspaceRequest
from tests.contract.git_http_api import GIT_INSTALLATION_TOKEN, GitHttpEmulator

REPOSITORY = RepositoryRef(provider=Provider.GITHUB, slug="small/pilot")

PACKS_ROOT = Path(__file__).resolve().parents[1] / "packs"
"""The repository's real packs directory, as the composition root configures it."""

PACK = "product-baseline"


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
    mirror_root: Path,
    emulator: GitHttpEmulator,
    *,
    token: str | None = None,
    packs_root: Path | None = None,
) -> ProviderClone:
    """A clone adapter bound to the emulator, with the token the test wants it to send."""
    return ProviderClone(
        ProviderCloneConfig(
            mirror_root=mirror_root,
            github=GitHubConfig(api_base_url=emulator.base_url, clone_base_url=emulator.base_url),
            packs_root=packs_root,
        ),
        token_provider=StaticTokenProvider(token or emulator.token),
    )


def _write_pack(
    root: Path,
    name: str,
    version: str,
    baseline_files: Mapping[str, bytes],
    *,
    schema: str = "dark-factory.dev/pack/v1",
    identifier: str | None = None,
) -> None:
    """A minimal pack on disk: a manifest plus the ``baseline/`` payload it materialises.

    ``baseline_files`` keys are payload-relative (``baseline/``-relative), so the loader
    maps them to ``.factory/<rel>`` in the repository. ``schema``/``identifier`` drift the
    manifest on purpose, and rewriting an existing pack only changes its manifest, so a
    version bump without a content change is expressible.
    """
    pack_dir = root / name
    payload = pack_dir / "baseline"
    payload.mkdir(parents=True, exist_ok=True)
    pack_id = identifier if identifier is not None else f"pack:{name}"
    (pack_dir / "pack.yaml").write_text(
        f"schema: {schema}\nid: {pack_id}\nname: {name}\nversion: {version}\n",
        encoding="utf-8",
    )
    for relative, content in baseline_files.items():
        target = payload / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def _bootstrap(port: ProviderClone, key: str, pack: str = PACK) -> BaselineBootstrapResult:
    """One bootstrap of ``REPOSITORY`` through ``port``, keyed as the test asks."""
    return asyncio.run(port.bootstrap_baseline(REPOSITORY, packs=(pack,), idempotency_key=key))


def _mirror_rev_parse(mirror: Path, revision: str) -> str:
    """``git rev-parse`` inside a prepared mirror (local repository, no network)."""
    process = subprocess.run(
        ("git", "-C", str(mirror), "rev-parse", "--verify", revision),
        check=True,
        capture_output=True,
        text=True,
    )
    return process.stdout.strip()


def _pack_version(name: str = PACK) -> str:
    """The version a pack manifest declares — the value the adapter must record."""
    manifest = yaml.safe_load((PACKS_ROOT / name / "pack.yaml").read_text(encoding="utf-8"))
    return str(manifest["version"])


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


# --- (e) bootstrap_baseline: packs land, replay is a no-op (T069) -------------


def test_bootstrap_of_an_empty_repository_creates_the_first_commit(
    tmp_path: Path, emulator: GitHttpEmulator
) -> None:
    # Unborn HEAD is the normal case of ADR-031 p.4: the bootstrap becomes the first
    # commit of the default branch, and its revision is the readiness evidence (p.6).
    emulator.seed(REPOSITORY, "empty")
    port = _port(tmp_path / "mirror", emulator, packs_root=PACKS_ROOT)
    result = asyncio.run(port.bootstrap_baseline(REPOSITORY, packs=(PACK,), idempotency_key="b-1"))

    assert result.repository == REPOSITORY
    assert result.revision != ""
    assert [(pack.name, pack.version) for pack in result.applied_packs] == [(PACK, _pack_version())]

    validation = asyncio.run(port.validate(REPOSITORY))
    assert validation.state is RepositoryState.BASELINE_CURRENT
    assert validation.head_revision == result.revision


def test_a_new_bootstrap_key_on_a_current_baseline_is_a_physical_no_op(
    tmp_path: Path, emulator: GitHttpEmulator
) -> None:
    # A second key is not a second effect: an already-current baseline stages nothing,
    # so the bootstrap returns the current revision instead of committing again.
    emulator.seed(REPOSITORY, "empty")
    port = _port(tmp_path / "mirror", emulator, packs_root=PACKS_ROOT)
    first = asyncio.run(port.bootstrap_baseline(REPOSITORY, packs=(PACK,), idempotency_key="b-1"))
    second = asyncio.run(port.bootstrap_baseline(REPOSITORY, packs=(PACK,), idempotency_key="b-2"))

    assert second.revision == first.revision
    assert second.applied_packs == first.applied_packs


def test_a_refused_push_is_repaired_by_the_next_bootstrap(
    tmp_path: Path, emulator: GitHttpEmulator
) -> None:
    # A transient push failure leaves the mirror with an unpushed baseline commit. The
    # next call must push it instead of deadlocking on the unborn remote (N1).
    emulator.seed(REPOSITORY, "empty")
    emulator.reject_push(REPOSITORY)
    port = _port(tmp_path / "mirror", emulator, packs_root=PACKS_ROOT)

    with pytest.raises(KeyError) as excinfo:
        _bootstrap(port, "b-1")
    message = str(excinfo.value)
    assert _credential(emulator.token) not in message  # fail closed, no credential
    assert "contents:write" in message  # actionable instruction

    emulator.allow_push(REPOSITORY)
    result = _bootstrap(port, "b-1")  # the same adapter, mirror, config and key

    assert result.revision != ""
    assert [pack.name for pack in result.applied_packs] == [PACK]
    validation = asyncio.run(port.validate(REPOSITORY))
    assert validation.state is RepositoryState.BASELINE_CURRENT
    assert validation.head_revision == result.revision


def test_a_wrong_token_fails_closed_and_the_correct_one_recovers(
    tmp_path: Path, emulator: GitHttpEmulator
) -> None:
    # The refused credential cannot reach the provider and leaves nothing behind, so the
    # same configuration with the right token lands the baseline (N1).
    secret = "wrong-installation-token"
    emulator.seed(REPOSITORY, "empty")
    wrong = _port(tmp_path / "mirror", emulator, token=secret, packs_root=PACKS_ROOT)
    with pytest.raises(KeyError) as excinfo:
        _bootstrap(wrong, "b-1")
    message = str(excinfo.value)
    assert secret not in message
    assert _credential(secret) not in message

    recovered = _port(tmp_path / "mirror", emulator, packs_root=PACKS_ROOT)
    result = _bootstrap(recovered, "b-1")

    assert result.revision != ""
    validation = asyncio.run(recovered.validate(REPOSITORY))
    assert validation.state is RepositoryState.BASELINE_CURRENT
    assert validation.head_revision == result.revision


def test_a_no_op_does_not_push_a_second_time(
    tmp_path: Path, emulator: GitHttpEmulator, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The postcondition is "the provider already carries this revision": when it holds,
    # the bootstrap must not push, so it cannot mint a second commit either (N1).
    emulator.seed(REPOSITORY, "empty")
    port = _port(tmp_path / "mirror", emulator, packs_root=PACKS_ROOT)
    first = _bootstrap(port, "b-1")

    calls: list[tuple[str, ...]] = []
    real = asyncio.create_subprocess_exec

    async def recorder(*argv: str, **kwargs: Any) -> Any:
        calls.append(argv)
        return await real(*argv, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", recorder)
    second = _bootstrap(port, "b-2")

    assert second.revision == first.revision
    assert [argv for argv in calls if "ls-remote" in argv], "the postcondition is checked"
    assert not [argv for argv in calls if "push" in argv], "a published revision is not pushed"


def test_the_bootstrap_records_the_manifest_version_not_a_constant(
    tmp_path: Path, emulator: GitHttpEmulator
) -> None:
    # A pack whose manifest declares another version must land that version: the
    # loader reads the manifest instead of assuming the product-baseline one.
    packs_root = tmp_path / "packs"
    _write_pack(packs_root, "sample", "9.9.9", {"product/product.md": b"name: sample\n"})
    emulator.seed(REPOSITORY, "empty")
    port = _port(tmp_path / "mirror", emulator, packs_root=packs_root)
    result = asyncio.run(
        port.bootstrap_baseline(REPOSITORY, packs=("sample",), idempotency_key="b-1")
    )

    assert [(pack.name, pack.version) for pack in result.applied_packs] == [("sample", "9.9.9")]
    validation = asyncio.run(port.validate(REPOSITORY))
    assert validation.state is RepositoryState.BASELINE_CURRENT


def test_bootstrap_without_a_packs_root_fails_closed(
    tmp_path: Path, emulator: GitHttpEmulator
) -> None:
    # Cloning works without packs, but the bootstrap must never report a baseline it
    # did not create: an unset packs root is a misconfiguration, not an absence (p.6).
    emulator.seed(REPOSITORY, "empty")
    port = _port(tmp_path / "mirror", emulator)
    with pytest.raises(ValueError) as excinfo:
        asyncio.run(port.bootstrap_baseline(REPOSITORY, packs=(PACK,), idempotency_key="b-1"))
    assert PACKS_ROOT_ENV_VAR in str(excinfo.value)
    # Nothing was written to the provider.
    validation = asyncio.run(port.validate(REPOSITORY))
    assert validation.state is RepositoryState.EMPTY


def test_bootstrap_without_packs_fails_closed(tmp_path: Path, emulator: GitHttpEmulator) -> None:
    # A bootstrap without packs would claim a baseline it did not create: refuse it
    # instead of inventing readiness evidence (ADR-031 p.6).
    emulator.seed(REPOSITORY, "empty")
    port = _port(tmp_path / "mirror", emulator, packs_root=PACKS_ROOT)
    with pytest.raises(ValueError, match="at least one pack"):
        asyncio.run(port.bootstrap_baseline(REPOSITORY, packs=(), idempotency_key="b-1"))


def test_bootstrap_with_a_missing_pack_fails_closed_naming_the_pack(
    tmp_path: Path, emulator: GitHttpEmulator
) -> None:
    emulator.seed(REPOSITORY, "empty")
    port = _port(tmp_path / "mirror", emulator, packs_root=PACKS_ROOT)
    with pytest.raises(ValueError) as excinfo:
        asyncio.run(
            port.bootstrap_baseline(REPOSITORY, packs=("no-such-pack",), idempotency_key="b-1")
        )
    assert "no-such-pack" in str(excinfo.value)  # actionable: names the pack
    validation = asyncio.run(port.validate(REPOSITORY))
    assert validation.state is RepositoryState.EMPTY  # the refused attempt left no effect


def test_bootstrap_with_a_rejected_token_is_a_secret_free_error(
    tmp_path: Path, emulator: GitHttpEmulator
) -> None:
    secret = "wrong-installation-token"
    emulator.seed(REPOSITORY, "empty")
    port = _port(tmp_path / "mirror", emulator, token=secret, packs_root=PACKS_ROOT)
    with pytest.raises(KeyError) as excinfo:
        asyncio.run(port.bootstrap_baseline(REPOSITORY, packs=(PACK,), idempotency_key="b-1"))
    message = str(excinfo.value)
    assert secret not in message
    assert _credential(secret) not in message
    assert "x-access-token" not in message
    assert "installed" in message  # actionable instruction, no provider text
    # A refused attempt leaves no mirror behind.
    assert not (tmp_path / "mirror" / "github" / "small" / "pilot").exists()


def test_the_bootstrap_carries_the_token_only_through_the_child_environment(
    tmp_path: Path, emulator: GitHttpEmulator, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = GIT_INSTALLATION_TOKEN
    emulator.seed(REPOSITORY, "empty")
    calls: list[tuple[tuple[str, ...], Mapping[str, str] | None]] = []
    real = asyncio.create_subprocess_exec

    async def recorder(*argv: str, **kwargs: Any) -> Any:
        calls.append((argv, kwargs.get("env")))
        return await real(*argv, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", recorder)
    port = _port(tmp_path / "mirror", emulator, token=secret, packs_root=PACKS_ROOT)
    asyncio.run(port.bootstrap_baseline(REPOSITORY, packs=(PACK,), idempotency_key="b-1"))

    assert calls, "the clone, the local steps and the push must have run"
    credential = _credential(secret)
    for argv, _ in calls:
        assert "x-access-token" not in " ".join(argv)
        assert credential not in " ".join(argv)
        assert secret not in " ".join(argv)
    carrying = [env for _, env in calls if env is not None]
    assert carrying, "the clone and the push must carry the credential environment"
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
    assert emulator.clone_url(REPOSITORY) in " ".join(calls[0][0])


def test_a_stale_mirror_lands_the_baseline_on_the_provider_tip(
    tmp_path: Path, emulator: GitHttpEmulator
) -> None:
    # The mirror is prepared from the first revision and the provider then advances:
    # the bootstrap must align to the provider's current default-branch tip before
    # writing, so the commit is a fast-forward and the returned revision is one the
    # provider really has (R1, ADR-031 p.6).
    first = emulator.seed(REPOSITORY, "baseline_absent")
    assert first is not None
    port = _port(tmp_path / "mirror", emulator, packs_root=PACKS_ROOT)
    mirror = Path(asyncio.run(port.ensure_mirror(REPOSITORY, idempotency_key="m-1")).location)

    advanced = emulator.seed(REPOSITORY, "baseline_current")
    assert advanced is not None and advanced != first

    result = _bootstrap(port, "b-1")

    assert result.revision not in {first, advanced}
    assert _mirror_rev_parse(mirror, f"{result.revision}^") == advanced  # on the advanced tip
    validation = asyncio.run(port.validate(REPOSITORY))
    assert validation.state is RepositoryState.BASELINE_CURRENT
    assert validation.head_revision == result.revision  # the provider really has it


def test_a_gitignored_factory_directory_still_gets_the_baseline(
    tmp_path: Path, emulator: GitHttpEmulator
) -> None:
    # ``git add --all`` alone respects .gitignore: the payload would stage nothing and
    # the bootstrap would report a revision that carries no baseline (R2). Forcing the
    # add lands the payload regardless of the ignore rules (ADR-031 p.6).
    emulator.seed(REPOSITORY, "baseline_ignored")
    port = _port(tmp_path / "mirror", emulator, packs_root=PACKS_ROOT)

    result = _bootstrap(port, "b-1")

    assert result.revision != ""
    validation = asyncio.run(port.validate(REPOSITORY))
    assert validation.state is RepositoryState.BASELINE_CURRENT
    assert validation.head_revision == result.revision


def test_a_no_op_without_a_baseline_at_head_fails_closed(
    tmp_path: Path, emulator: GitHttpEmulator
) -> None:
    # The payload already sits at HEAD, so it stages nothing, while ``.factory/product``
    # is absent: there is no baseline to report and the bootstrap must fail closed
    # instead of returning the pre-existing revision as readiness (R2, ADR-031 p.6).
    packs_root = tmp_path / "packs"
    _write_pack(packs_root, "sample", "1.0.0", {"README.md": b"partial\n"})
    emulator.seed(REPOSITORY, "baseline_partial")
    port = _port(tmp_path / "mirror", emulator, packs_root=packs_root)

    with pytest.raises(RuntimeError, match="absent at HEAD"):
        _bootstrap(port, "b-1", "sample")


def test_a_no_op_reports_the_packs_the_baseline_commit_records(
    tmp_path: Path, emulator: GitHttpEmulator
) -> None:
    # The manifest bumps without a content change: the repository still carries 1.0.0,
    # so the no-op must report what the baseline commit records, not the requested
    # 2.0.0 — evidence never claims a version the repository does not carry (R3).
    packs_root = tmp_path / "packs"
    _write_pack(packs_root, "sample", "1.0.0", {"product/product.md": b"name: sample\n"})
    emulator.seed(REPOSITORY, "empty")
    port = _port(tmp_path / "mirror", emulator, packs_root=packs_root)
    first = _bootstrap(port, "b-1", "sample")
    assert [(pack.name, pack.version) for pack in first.applied_packs] == [("sample", "1.0.0")]

    _write_pack(packs_root, "sample", "2.0.0", {"product/product.md": b"name: sample\n"})
    second = _bootstrap(port, "b-2", "sample")

    assert second.revision == first.revision
    assert [(pack.name, pack.version) for pack in second.applied_packs] == [("sample", "1.0.0")]


def test_a_no_op_on_a_foreign_baseline_claims_no_packs(
    tmp_path: Path, emulator: GitHttpEmulator
) -> None:
    # The seeded baseline carries exactly this file, so the payload stages nothing. A
    # commit a bootstrap did not write records no packs, and the evidence claims none
    # instead of inventing them (R3, ADR-031 p.6).
    packs_root = tmp_path / "packs"
    _write_pack(packs_root, "sample", "1.0.0", {"product/product.yaml": b"name: pilot\n"})
    emulator.seed(REPOSITORY, "baseline_current")
    port = _port(tmp_path / "mirror", emulator, packs_root=packs_root)

    result = _bootstrap(port, "b-1", "sample")

    assert result.applied_packs == ()


def test_bootstrap_with_a_foreign_manifest_schema_fails_closed(
    tmp_path: Path, emulator: GitHttpEmulator
) -> None:
    packs_root = tmp_path / "packs"
    _write_pack(
        packs_root,
        "sample",
        "1.0.0",
        {"product/product.md": b"x\n"},
        schema="other.dev/pack/v9",
    )
    emulator.seed(REPOSITORY, "empty")
    port = _port(tmp_path / "mirror", emulator, packs_root=packs_root)

    with pytest.raises(ValueError) as excinfo:
        _bootstrap(port, "b-1", "sample")

    assert "sample" in str(excinfo.value)  # actionable: names the pack


def test_bootstrap_with_a_manifest_id_that_names_another_pack_fails_closed(
    tmp_path: Path, emulator: GitHttpEmulator
) -> None:
    packs_root = tmp_path / "packs"
    _write_pack(
        packs_root,
        "sample",
        "1.0.0",
        {"product/product.md": b"x\n"},
        identifier="pack:another-pack",
    )
    emulator.seed(REPOSITORY, "empty")
    port = _port(tmp_path / "mirror", emulator, packs_root=packs_root)

    with pytest.raises(ValueError) as excinfo:
        _bootstrap(port, "b-1", "sample")

    assert "sample" in str(excinfo.value)  # actionable: names the pack


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


def test_config_from_env_fails_closed_on_a_relative_packs_root() -> None:
    env = {
        MIRROR_ROOT_ENV_VAR: "/tmp/mirror",
        PACKS_ROOT_ENV_VAR: "relative/packs",
        "DARK_FACTORY_GITHUB_APP_ID": "123",
        "DARK_FACTORY_GITHUB_APP_PRIVATE_KEY": "key",
        "DARK_FACTORY_GITHUB_INSTALLATION_ID": "42",
    }
    with pytest.raises(ValueError) as excinfo:
        ProviderCloneConfig.from_env(env)
    message = str(excinfo.value)
    assert PACKS_ROOT_ENV_VAR in message  # names the variable ...
    assert "relative/packs" not in message  # ... never the value (ADR-009)


def test_config_from_env_fails_closed_on_an_empty_packs_root() -> None:
    env = {
        MIRROR_ROOT_ENV_VAR: "/tmp/mirror",
        PACKS_ROOT_ENV_VAR: "",
        "DARK_FACTORY_GITHUB_APP_ID": "123",
        "DARK_FACTORY_GITHUB_APP_PRIVATE_KEY": "key",
        "DARK_FACTORY_GITHUB_INSTALLATION_ID": "42",
    }
    with pytest.raises(ValueError, match=PACKS_ROOT_ENV_VAR):
        ProviderCloneConfig.from_env(env)


def test_config_from_env_ignores_a_bad_packs_root_while_the_clone_is_not_selected() -> None:
    # Without App credentials the adapter is absent and the composition root falls back
    # to LocalMirror: a stray packs variable must not break that contour (R4).
    env = {MIRROR_ROOT_ENV_VAR: "/tmp/mirror", PACKS_ROOT_ENV_VAR: "relative/packs"}
    assert ProviderCloneConfig.from_env(env) is None


def test_config_from_env_reads_the_packs_root() -> None:
    env = {
        MIRROR_ROOT_ENV_VAR: "/tmp/mirror",
        PACKS_ROOT_ENV_VAR: "/srv/factory/packs",
        "DARK_FACTORY_GITHUB_APP_ID": "123",
        "DARK_FACTORY_GITHUB_APP_PRIVATE_KEY": "key",
        "DARK_FACTORY_GITHUB_INSTALLATION_ID": "42",
    }
    config = ProviderCloneConfig.from_env(env)
    assert config is not None
    assert config.packs_root == Path("/srv/factory/packs")


def test_config_from_env_leaves_the_packs_root_optional() -> None:
    env = {
        MIRROR_ROOT_ENV_VAR: "/tmp/mirror",
        "DARK_FACTORY_GITHUB_APP_ID": "123",
        "DARK_FACTORY_GITHUB_APP_PRIVATE_KEY": "key",
        "DARK_FACTORY_GITHUB_INSTALLATION_ID": "42",
    }
    config = ProviderCloneConfig.from_env(env)
    assert config is not None
    assert config.packs_root is None


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
