"""``RepositoryProvisioningPort`` over a provider clone on an installation token (T068, ADR-031).

``ProviderClone`` is the second of the two adapters ADR-031 p.2 binds to the port: it
clones/fetches the product repository from the provider and prepares the local mirror
the execution layer works from, laid out ``<root>/<provider>/<slug>`` under
``DARK_FACTORY_WORKSPACE_MIRROR_ROOT`` — the same convention ``LocalMirror`` and
``execution.workspace.WorktreeExecution`` use. The variable and the layout are kept
local (as ``LocalMirror`` keeps them) so the two provisioning adapters stay
independent; the agreement is pinned by ``tests/test_adapters_provider_clone.py``.

Authentication is the GitHub App installation token of the existing contour
(ADR-019 p.3, ADR-031 p.3): a PAT is never introduced. The token reaches git **only**
through the child-process environment — never on argv and never in the URL — as
``http.<base>/.extraheader`` carrying ``AUTHORIZATION: basic <base64>``, with
``GIT_TERMINAL_PROMPT=0`` so a missing or invalid credential fails closed instead of
prompting. Git output is dropped and failures are reported with a provider-neutral,
actionable instruction naming no secret (ADR-009).

Capabilities (ADR-031 p.2/p.4/p.5):

* ``validate`` — read-only probe that mutates nothing and never touches the local
  mirror: a temporary bare shallow clone observes availability (a non-zero exit,
  including 401 and 404, is ``UNAVAILABLE``), the default branch, the head revision
  and whether ``.factory/product`` (ADR-020) is present at HEAD. An unborn HEAD is the
  normal ``EMPTY`` state of p.4 — no error — and still reports the branch the
  repository will get. The probe is a clone rather than ``git ls-remote --symref``
  because the latter receives the ``unborn HEAD symref-target`` line of protocol v2
  but does not print it, so an empty repository would lose its default branch (the
  port contract requires it). ``BASELINE_STALE`` is unreachable until the packs
  comparison of T069.
* ``ensure_mirror`` — clone (absent path) or ``git fetch --prune`` (existing path)
  into ``<root>/<provider>/<slug>``, replay-dedup by key. A repository that cannot be
  materialised — absent, outside the App installation, invalid credentials — is the
  port's ``KeyError`` carrying an actionable, secret-free instruction.
* ``bootstrap_baseline`` is not performed: applying packs is T069, and an adapter must
  never report a bootstrap it did not perform (ADR-031 p.6).
"""

import asyncio
import base64
import os
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import httpx2

from dark_factory.adapters.scm.github.auth import GitHubAppAuth, TokenProvider
from dark_factory.adapters.scm.github.config import GitHubConfig
from dark_factory.ports import (
    BaselineBootstrapResult,
    MirrorRef,
    ProvisioningOperationUnsupportedError,
    RepositoryProvisioningPort,
    RepositoryRef,
    RepositoryState,
    RepositoryValidation,
)

MIRROR_ROOT_ENV_VAR: Final[str] = "DARK_FACTORY_WORKSPACE_MIRROR_ROOT"
"""Root of the prepared mirrors, laid out ``<root>/<provider>/<slug>``.

``LocalMirror`` (``adapters/provisioning/local_mirror.py``) and the execution adapter
(``execution.workspace.WORKSPACE_MIRROR_ROOT_ENV_VAR``) read the same variable and the
same layout; the agreement is pinned by ``tests/test_adapters_provider_clone.py``.
"""

BASELINE_PATH: Final[str] = ".factory/product"
"""Canonical Product Baseline inside the repository (ADR-020)."""

_VALIDATION_TMP_PREFIX: Final[str] = "dark-factory-validate-"
"""Prefix of the throwaway probe directory of ``validate`` (deleted before it returns)."""

_GIT_USERNAME: Final[str] = "x-access-token"
"""GitHub's conventional username for an installation token over git smart HTTP."""

_CLONE_ARGV: Final[tuple[str, ...]] = (
    "clone",
    "--bare",
    "--quiet",
    "--depth=1",
    "--no-tags",
    "--single-branch",
)
"""Throwaway probe of ``validate``: one commit of the default branch, no working tree.

``--depth=1`` bounds the transfer to the tip (the tree of the observed revision is what
baseline detection reads anyway), and ``--bare`` keeps the probe free of a checkout. A
clone also carries the *unborn* HEAD symref of an empty repository, which ``ls-remote``
does not report — that is what lets ``EMPTY`` keep its default branch.
"""


def _clone_failure(repository: RepositoryRef) -> KeyError:
    """The port's absent convention (404) with a secret-free, actionable instruction."""
    return KeyError(
        f"the repository {repository.slug!r} could not be cloned or fetched: verify that "
        "the GitHub App is installed on it with contents:read access and that the "
        "DARK_FACTORY_GITHUB_* credentials are valid"
    )


def _basic_credential(token: str) -> str:
    """``base64("x-access-token:<token>")`` — the value of the ``Authorization`` header."""
    return base64.b64encode(f"{_GIT_USERNAME}:{token}".encode()).decode("ascii")


def _require_absolute_mirror_root(path: Path) -> Path:
    """The configured mirror root, or a ``ValueError`` naming the variable (never the value)."""
    if not str(path) or not path.is_absolute():
        raise ValueError(f"{MIRROR_ROOT_ENV_VAR} must be an absolute path")
    return path


def _is_git_repository(path: Path) -> bool:
    """True for a working clone (``.git``) or a bare mirror (``HEAD``)."""
    return (path / ".git").exists() or (path / "HEAD").exists()


@dataclass(frozen=True, slots=True)
class ProviderCloneConfig:
    """Mirror root and provider endpoints of the clone adapter (ADR-031 p.2/p.3)."""

    mirror_root: Path
    """Root the prepared mirror is materialised under, laid out ``<root>/<provider>/<slug>``."""
    github: GitHubConfig
    """App credentials plus the REST and clone base URLs of the provider contour."""

    def __post_init__(self) -> None:
        _require_absolute_mirror_root(self.mirror_root)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "ProviderCloneConfig | None":
        """Config read from ``env`` (default: the process environment); ``None`` when absent.

        The mirror root selects the adapter, exactly as it does for ``LocalMirror``: an
        absent variable leaves the adapter absent, while a variable that is set but
        empty or relative is a misconfiguration and fails closed, naming the variable
        and never the value (ADR-009) — the path typo is reported even while the App
        side is still incomplete. The GitHub side comes from
        ``GitHubConfig.from_env``; while the App credentials are incomplete the
        adapter stays absent, and the composition root reports the gap instead of
        building a clone that cannot authenticate.
        """
        source = os.environ if env is None else env
        value = source.get(MIRROR_ROOT_ENV_VAR)
        if value is None:
            return None
        mirror_root = _require_absolute_mirror_root(Path(value))
        github = GitHubConfig.from_env(source)
        if github is None:
            return None
        return cls(mirror_root=mirror_root, github=github)


class ProviderClone(RepositoryProvisioningPort):
    """``RepositoryProvisioningPort`` over a provider clone (ADR-031 p.2/p.3)."""

    def __init__(
        self,
        config: ProviderCloneConfig,
        *,
        token_provider: TokenProvider | None = None,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config
        self._auth: TokenProvider = (
            token_provider
            if token_provider is not None
            else GitHubAppAuth(config.github, transport=transport)
        )
        self._mirrors: dict[str, MirrorRef] = {}

    async def validate(self, repository: RepositoryRef, /) -> RepositoryValidation:
        """Observe the repository without mutating it or touching the local mirror (p.5)."""
        env = self._git_env(await self._auth.token())
        with tempfile.TemporaryDirectory(prefix=_VALIDATION_TMP_PREFIX) as tmp:
            probe = Path(tmp) / "probe.git"
            code, _ = await _run_git(
                None, (*_CLONE_ARGV, self._clone_url(repository), str(probe)), env=env
            )
            if code != 0:
                return RepositoryValidation(
                    repository=repository, state=RepositoryState.UNAVAILABLE
                )
            branch = await _symbolic_branch(probe)
            head = await _rev_parse(probe, "HEAD")
            if head is None:
                return RepositoryValidation(
                    repository=repository, state=RepositoryState.EMPTY, default_branch=branch
                )
            state = (
                RepositoryState.BASELINE_CURRENT
                if await _path_exists_at_head(probe, BASELINE_PATH)
                else RepositoryState.BASELINE_ABSENT
            )
        return RepositoryValidation(
            repository=repository, state=state, default_branch=branch, head_revision=head
        )

    async def ensure_mirror(
        self, repository: RepositoryRef, /, *, idempotency_key: str
    ) -> MirrorRef:
        """Clone or refresh the mirror of ``repository``; absent is the port's ``KeyError``."""
        cached = self._mirrors.get(idempotency_key)
        if cached is not None:
            return cached
        path = self._mirror_path(repository)
        env = self._git_env(await self._auth.token())
        if _is_git_repository(path):
            await self._refresh(path, repository, env)
        else:
            # A leftover of an interrupted attempt is not a mirror: never clone into
            # a directory git would refuse, and never leave the wreck behind.
            shutil.rmtree(path, ignore_errors=True)
            path.parent.mkdir(parents=True, exist_ok=True)
            code, _ = await _run_git(
                None, ("clone", self._clone_url(repository), str(path)), env=env
            )
            if code != 0:
                shutil.rmtree(path, ignore_errors=True)
                raise _clone_failure(repository)
        ref = MirrorRef(
            repository=repository,
            location=str(path),
            default_branch=await _symbolic_branch(path),
            head_revision=await _rev_parse(path, "HEAD"),
        )
        self._mirrors[idempotency_key] = ref
        return ref

    async def bootstrap_baseline(
        self, repository: RepositoryRef, /, *, packs: Sequence[str], idempotency_key: str
    ) -> BaselineBootstrapResult:
        raise ProvisioningOperationUnsupportedError("bootstrap_baseline")

    # --- internals ---------------------------------------------------------

    async def _refresh(self, path: Path, repository: RepositoryRef, env: Mapping[str, str]) -> None:
        """Bring an existing mirror up to date; a mirror without ``origin`` is used as it is.

        An operator-prepared mirror (the ``LocalMirror`` layout is shared) has no remote
        of ours to refresh, and the execution adapter treats it the same way.
        """
        _, remotes = await _run_git(path, ("remote",), env=None)
        if "origin" not in remotes.split():
            return
        code, _ = await _run_git(path, ("fetch", "--prune", "origin"), env=env)
        if code != 0:
            raise _clone_failure(repository)
        # Best effort: a provider that does not advertise HEAD leaves the local
        # symref as it is (the clone already set it) — refresh never fails here.
        await _run_git(path, ("remote", "set-head", "origin", "-a"), env=env)

    def _mirror_path(self, repository: RepositoryRef) -> Path:
        return self._config.mirror_root / repository.provider.value / repository.slug

    def _clone_base_url(self) -> str:
        return self._config.github.clone_base_url.rstrip("/")

    def _clone_url(self, repository: RepositoryRef) -> str:
        return f"{self._clone_base_url()}/{repository.slug}.git"

    def _git_env(self, token: str) -> dict[str, str]:
        """Child environment carrying the installation token off the command line (ADR-009).

        ``http.<base>/.extraheader`` is scoped to the configured git host, so the
        credential is sent to the provider and nowhere else; ``GIT_TERMINAL_PROMPT=0``
        turns a rejected credential into a failure instead of an interactive prompt.
        """
        env = dict(os.environ)
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = f"http.{self._clone_base_url()}/.extraheader"
        env["GIT_CONFIG_VALUE_0"] = f"AUTHORIZATION: basic {_basic_credential(token)}"
        return env


async def _run_git(
    cwd: Path | None, argv: tuple[str, ...], *, env: Mapping[str, str] | None
) -> tuple[int, str]:
    """Run one git command; failures are reported, not raised, and stderr is dropped.

    ``env`` is passed only to commands that reach the provider: local probes inherit
    the process environment, so the installation token is handed to as few processes
    as possible. Dropping stderr keeps provider text (which may echo a URL) out of
    callers' messages.
    """
    process = await asyncio.create_subprocess_exec(
        "git",
        *argv,
        cwd=cwd,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await process.communicate()
    code = process.returncode
    return (code if code is not None else -1), stdout.decode("utf-8", errors="replace")


async def _symbolic_branch(mirror: Path) -> str | None:
    """The branch HEAD points at; ``None`` on a detached HEAD (ADR-031 p.4)."""
    code, out = await _run_git(mirror, ("symbolic-ref", "--short", "HEAD"), env=None)
    if code != 0:
        return None
    return out.strip() or None


async def _rev_parse(mirror: Path, *argv: str) -> str | None:
    """``git rev-parse --verify`` output, or ``None`` when the revision does not resolve.

    ``--verify`` (not a bare ``rev-parse``) is what distinguishes an *unborn* HEAD
    from a resolved one: in a bare repository a plain ``git rev-parse HEAD`` prints the
    literal ``HEAD`` and exits 0 when there is no commit, so the empty state would be
    mistaken for a present revision.
    """
    code, out = await _run_git(mirror, ("rev-parse", "--verify", *argv), env=None)
    if code != 0:
        return None
    return out.strip() or None


async def _path_exists_at_head(mirror: Path, path: str) -> bool:
    """True when ``path`` is present in the tree of HEAD."""
    code, out = await _run_git(mirror, ("ls-tree", "--name-only", "HEAD", path), env=None)
    return code == 0 and out.strip() != ""
