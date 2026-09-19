"""``RepositoryProvisioningPort`` over a provider clone on an installation token (T068, ADR-031).

``ProviderClone`` is the second of the two adapters ADR-031 p.2 binds to the port: it
clones/fetches the product repository from the provider and prepares the local mirror
the execution layer works from, laid out ``<root>/<provider>/<slug>`` under
``DARK_FACTORY_WORKSPACE_MIRROR_ROOT`` — the same convention ``LocalMirror`` and
``execution.workspace.WorktreeExecution`` use. The variable and the layout are kept
local (as ``LocalMirror`` keeps them) so the two provisioning adapters stay
independent; the agreement is pinned by ``tests/test_adapters_provider_clone.py``.

Authentication is the GitHub App installation token of the existing contour
(ADR-019 p.3, ADR-031 p.3): a PAT is never introduced. Every command that reaches
the provider — clone, fetch and the bootstrap push — carries the token **only**
through the child-process environment: never on argv and never in the URL, but as
``http.<base>/.extraheader`` carrying ``AUTHORIZATION: basic <base64>``, with
``GIT_TERMINAL_PROMPT=0`` so a missing or invalid credential fails closed instead of
prompting. Git output is dropped and failures are reported with a provider-neutral,
actionable instruction naming no secret (ADR-009).

Capabilities (ADR-031 p.2/p.4/p.5/p.6):

* ``validate`` — read-only probe that mutates nothing and never touches the local
  mirror: a temporary bare shallow clone observes availability (a non-zero exit,
  including 401 and 404, is ``UNAVAILABLE``), the default branch, the head revision
  and whether ``.factory/product`` (ADR-020) is present at HEAD. An unborn HEAD is the
  normal ``EMPTY`` state of p.4 — no error — and still reports the branch the
  repository will get. The probe is a clone rather than ``git ls-remote --symref``
  because the latter receives the ``unborn HEAD symref-target`` line of protocol v2
  but does not print it, so an empty repository would lose its default branch (the
  port contract requires it). ``BASELINE_STALE`` stays reserved: the adapter keeps no
  desired baseline to compare the observed one against.
* ``ensure_mirror`` — clone (absent path) or ``git fetch --prune`` (existing path)
  into ``<root>/<provider>/<slug>``, replay-dedup by key. A repository that cannot be
  materialised — absent, outside the App installation, invalid credentials — is the
  port's ``KeyError`` carrying an actionable, secret-free instruction.
* ``bootstrap_baseline`` — bring the working mirror to the provider's current
  default-branch tip, apply the named packs and push the revision they produce (T069).
  Applying ``packs/<name>/baseline`` lays it down as the repository's ``.factory/``
  skeleton, so an *unborn* HEAD is the normal case of p.4: the bootstrap creates the
  repository's first commit. Replay-dedup by key (p.6, FR-017); a second key that
  stages no change while HEAD already carries the baseline is a physical no-op that
  returns the current revision instead of minting a second commit, and its evidence is
  the packs that commit records, never the caller's request. A payload that stages
  nothing while the baseline is absent at HEAD fails closed instead of reporting
  readiness that is not there. The outcome is then settled by one provider fact, not by
  which branch produced it: the default branch must carry the baseline revision, so a
  revision an earlier refused push left in the mirror is pushed by the next call — the
  bootstrap is retry-safe without a manual mirror cleanup. The push is why the adapter
  uses the App's write access — the same ``contents:write`` the contour already
  exercises for ``RepositoryPort.publish_commit`` (ADR-019 p.3).
  ``DARK_FACTORY_PACKS_ROOT`` names the packs root; without it the operation fails
  closed instead of reporting a baseline it did not create.
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

from dark_factory.adapters.provisioning.packs import load_pack
from dark_factory.adapters.scm.github.auth import GitHubAppAuth, TokenProvider
from dark_factory.adapters.scm.github.config import GitHubConfig
from dark_factory.adapters.scm.github.pull_requests import IDEMPOTENCY_MARKER_TEMPLATE
from dark_factory.ports import (
    AppliedPack,
    BaselineBootstrapResult,
    MirrorRef,
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

PACKS_ROOT_ENV_VAR: Final[str] = "DARK_FACTORY_PACKS_ROOT"
"""Root of the baseline packs ``bootstrap_baseline`` applies (T069).

Optional: cloning, validation and ``ensure_mirror`` need no packs, so a contour
without it still prepares a mirror; only the bootstrap fails closed, naming the
variable instead of reporting a baseline it did not create (ADR-031 p.6).
"""

_VALIDATION_TMP_PREFIX: Final[str] = "dark-factory-validate-"
"""Prefix of the throwaway probe directory of ``validate`` (deleted before it returns)."""

_GIT_USERNAME: Final[str] = "x-access-token"
"""GitHub's conventional username for an installation token over git smart HTTP."""

_BOOTSTRAP_IDENTITY: Final[tuple[str, ...]] = (
    "-c",
    "commit.gpgsign=false",
    "-c",
    "user.name=Dark Factory",
    "-c",
    "user.email=factory@example.com",
)
"""Commit identity of a bootstrap: the mirrored repository is not the operator's clone."""

_BOOTSTRAP_SUBJECT_PREFIX: Final[str] = "bootstrap: apply "
"""Prefix of a bootstrap commit subject; it records the packs the commit landed (p.6)."""

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


def _push_failure(repository: RepositoryRef) -> KeyError:
    """The port's absent convention for a refused push (403/404), secret-free and actionable."""
    return KeyError(
        f"the baseline of {repository.slug!r} could not be pushed: verify that the "
        "GitHub App is installed on it with contents:write access and that the "
        "DARK_FACTORY_GITHUB_* credentials are valid"
    )


def _local_git_failure(action: str) -> RuntimeError:
    """A local step of the bootstrap failed; git output is dropped, so only the step is named."""
    return RuntimeError(f"git {action} failed while applying the baseline packs")


def _no_effect_failure(repository: RepositoryRef) -> RuntimeError:
    """The payload staged nothing while HEAD carries no baseline: no evidence to report."""
    return RuntimeError(
        f"applying the baseline packs to {repository.slug!r} changed nothing while "
        f"{BASELINE_PATH} is absent at HEAD: refusing to report a baseline that is not there"
    )


def _unverified_baseline_failure(repository: RepositoryRef) -> RuntimeError:
    """The provider's default branch is not the revision the bootstrap would report (p.6)."""
    return RuntimeError(
        f"the baseline of {repository.slug!r} is not at the provider's default branch after "
        "the bootstrap: refusing to report a revision the provider does not have"
    )


def _basic_credential(token: str) -> str:
    """``base64("x-access-token:<token>")`` — the value of the ``Authorization`` header."""
    return base64.b64encode(f"{_GIT_USERNAME}:{token}".encode()).decode("ascii")


def _require_absolute_mirror_root(path: Path) -> Path:
    """The configured mirror root, or a ``ValueError`` naming the variable (never the value)."""
    if not str(path) or not path.is_absolute():
        raise ValueError(f"{MIRROR_ROOT_ENV_VAR} must be an absolute path")
    return path


def _require_absolute_packs_root(path: Path) -> Path:
    """The configured packs root, or a ``ValueError`` naming the variable (never the value)."""
    if not str(path) or not path.is_absolute():
        raise ValueError(f"{PACKS_ROOT_ENV_VAR} must be an absolute path")
    return path


def _optional_packs_root(source: Mapping[str, str]) -> Path | None:
    """Optional packs root: absent variable means no bootstrap payload, a bad one fails closed."""
    value = source.get(PACKS_ROOT_ENV_VAR)
    return None if value is None else _require_absolute_packs_root(Path(value))


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
    packs_root: Path | None = None
    """Root of the baseline packs ``bootstrap_baseline`` applies (T069), or ``None``.

    Optional because cloning and validation need no packs: a contour without
    ``DARK_FACTORY_PACKS_ROOT`` keeps provisioning a mirror, while the bootstrap
    fails closed instead of reporting a baseline it did not create (ADR-031 p.6).
    """

    def __post_init__(self) -> None:
        _require_absolute_mirror_root(self.mirror_root)
        if self.packs_root is not None:
            _require_absolute_packs_root(self.packs_root)

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
        building a clone that cannot authenticate. ``DARK_FACTORY_PACKS_ROOT`` is read
        only once the adapter is actually selected (an absolute mirror root and the
        App credentials): a stray packs variable must not make a ``LocalMirror``
        contour fail, while a set-but-empty or relative one on a selected clone still
        fails closed, naming the variable and never the value.
        """
        source = os.environ if env is None else env
        value = source.get(MIRROR_ROOT_ENV_VAR)
        if value is None:
            return None
        mirror_root = _require_absolute_mirror_root(Path(value))
        github = GitHubConfig.from_env(source)
        if github is None:
            return None
        packs_root = _optional_packs_root(source)
        return cls(mirror_root=mirror_root, github=github, packs_root=packs_root)


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
        self._bootstraps: dict[str, BaselineBootstrapResult] = {}

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
        await self._materialise(path, repository, env)
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
        """Apply ``packs`` to the repository and push the revision they produce (T069).

        An empty repository (unborn HEAD) is the normal case of ADR-031 p.4: the
        bootstrap lays the packs' ``baseline/`` subtrees down as the repository's
        ``.factory/`` skeleton (ADR-020) and pushes the commit that carries them,
        which becomes the remote default branch's first revision. The mirror is first
        brought to the provider's current default-branch tip, so the commit lands on
        top of the provider's history rather than a stale one and the returned revision
        is one the provider actually has. Replay-dedup by key; a second key that stages
        no change while HEAD already carries the baseline is a physical no-op whose
        evidence names the packs HEAD records, not the packs the caller asked for. A
        payload that stages nothing while the baseline is absent at HEAD fails closed
        instead of reporting readiness that is not there. The postcondition is a
        provider fact: the default branch must carry the revision the bootstrap returns,
        so a revision an earlier refused push left in the mirror is pushed by the next
        call and a retry recovers without touching the mirror. The installation token
        travels in the child environment only (ADR-009), and a misconfigured packs root
        or a missing pack fails closed before any write.
        """
        cached = self._bootstraps.get(idempotency_key)
        if cached is not None:
            return cached
        packs_root = self._config.packs_root
        if packs_root is None:
            raise ValueError(
                f"bootstrap_baseline needs {PACKS_ROOT_ENV_VAR}: the baseline packs root"
                " is not configured"
            )
        if not packs:
            raise ValueError("bootstrap_baseline requires at least one pack")
        resolved = tuple(load_pack(packs_root, name) for name in packs)
        applied = tuple(AppliedPack(name=pack.name, version=pack.version) for pack in resolved)
        payload: dict[str, bytes] = {}
        for pack in resolved:
            payload.update(pack.files)
        env = self._git_env(await self._auth.token())
        path = self._mirror_path(repository)
        await self._materialise(path, repository, env)
        default_branch = await self._remote_default_branch(path)
        await self._align_to_default_branch(path, default_branch)
        _write_payload(path, payload)
        result = await self._land_baseline(
            path, repository, default_branch, applied, idempotency_key, env
        )
        self._bootstraps[idempotency_key] = result
        return result

    # --- internals ---------------------------------------------------------

    async def _materialise(
        self, path: Path, repository: RepositoryRef, env: Mapping[str, str]
    ) -> None:
        """Prepare the working mirror of ``repository`` at ``path`` (clone or refresh).

        Shared by ``ensure_mirror`` and ``bootstrap_baseline`` so the bootstrap does
        not mint an unrelated ``MirrorRef`` cache entry under its own key (T069).
        """
        if _is_git_repository(path):
            await self._refresh(path, repository, env)
            return
        # A leftover of an interrupted attempt is not a mirror: never clone into
        # a directory git would refuse, and never leave the wreck behind.
        shutil.rmtree(path, ignore_errors=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        code, _ = await _run_git(None, ("clone", self._clone_url(repository), str(path)), env=env)
        if code != 0:
            shutil.rmtree(path, ignore_errors=True)
            raise _clone_failure(repository)

    async def _land_baseline(
        self,
        path: Path,
        repository: RepositoryRef,
        default_branch: str,
        applied: tuple[AppliedPack, ...],
        idempotency_key: str,
        env: Mapping[str, str],
    ) -> BaselineBootstrapResult:
        """Land the staged payload and leave the provider's default branch carrying it.

        ``--force`` stages the payload even in a repository that ignores ``.factory/``
        (T069). A commit is created only when staging changed something; otherwise the
        existing revision is reused, and it must carry the baseline — a payload that
        stages nothing while ``.factory/product`` is absent at HEAD has no evidence to
        report (ADR-031 p.6). The outcome is then settled by one postcondition rather
        than by which branch produced it: the provider's ``refs/heads/<default>`` must
        equal HEAD. A revision an earlier refused push left in the mirror is pushed by
        this call, which makes the bootstrap retry-safe; a diverged mirror or a refused
        push still fails closed, and evidence is returned only once that ref is
        confirmed. The commit message carries the same replay marker
        ``RepositoryPort.publish_commit`` and ``MergeRequestPort.add_comment`` use, so a
        bootstrap effect is identifiable on the provider like every other keyed effect
        (FR-017).
        """
        code, _ = await _run_git(path, ("add", "--all", "--force"), env=None)
        if code != 0:
            raise _local_git_failure("add")
        if await _tree_changed(path):
            message = _bootstrap_message(applied)
            marker = IDEMPOTENCY_MARKER_TEMPLATE.format(key=idempotency_key)
            code, _ = await _run_git(
                path,
                (*_BOOTSTRAP_IDENTITY, "commit", "--quiet", "--message", f"{message}\n\n{marker}"),
                env=None,
            )
            if code != 0:
                raise _local_git_failure("commit")
            revision = await _rev_parse(path, "HEAD")
            if revision is None:
                raise _local_git_failure("commit")
            applied_packs = applied
        else:
            revision = await _rev_parse(path, "HEAD")
            if revision is None or not await _path_exists_at_head(path, BASELINE_PATH):
                # Nothing to commit and no baseline at HEAD: a pre-existing revision is
                # not evidence of a baseline that is not there (ADR-031 p.6).
                raise _no_effect_failure(repository)
            applied_packs = await _recorded_packs(path)
        remote = await self._remote_revision(path, default_branch, env)
        if remote != revision:
            # The provider does not have this revision: push it (never forced — a
            # provider that moved under us is a failure, not an overwrite).
            code, _ = await _run_git(
                path, ("push", "--quiet", "origin", f"HEAD:refs/heads/{default_branch}"), env=env
            )
            if code != 0:
                raise _push_failure(repository)
            remote = await self._remote_revision(path, default_branch, env)
        if remote != revision:
            raise _unverified_baseline_failure(repository)
        return BaselineBootstrapResult(
            repository=repository, revision=revision, applied_packs=applied_packs
        )

    async def _remote_default_branch(self, path: Path) -> str:
        """The remote's default branch, read from ``origin/HEAD`` (the local HEAD as fallback)."""
        code, out = await _run_git(
            path, ("symbolic-ref", "--short", "refs/remotes/origin/HEAD"), env=None
        )
        remote_head = out.strip()
        if code == 0 and remote_head.startswith("origin/"):
            return remote_head.split("/", 1)[1]
        local = await _symbolic_branch(path)
        if local is None:
            raise _local_git_failure("symbolic-ref")
        return local

    async def _align_to_default_branch(self, path: Path, default_branch: str) -> None:
        """Reset the working branch to ``origin/<default>``; an unborn remote is skipped.

        ``_refresh`` fetches objects but never moves the local branch, so a mirror left
        behind by an earlier attempt can sit on a stale tip. The bootstrap aligns before
        writing: committing on the provider's current tip keeps the push a fast-forward
        and the returned revision one the provider really has (T069).
        """
        remote_ref = f"origin/{default_branch}"
        if await _rev_parse(path, remote_ref) is None:
            return
        code, _ = await _run_git(path, ("reset", "--hard", remote_ref), env=None)
        if code != 0:
            raise _local_git_failure("reset")

    async def _remote_revision(
        self, path: Path, default_branch: str, env: Mapping[str, str]
    ) -> str | None:
        """The provider's revision of ``refs/heads/<default>``, or ``None`` when it is not there.

        An unborn branch, an unreadable remote and a refused read all answer ``None``:
        the caller decides whether to push or to fail closed, instead of trusting a local
        revision the provider may not have (ADR-031 p.6).
        """
        code, out = await _run_git(
            path, ("ls-remote", "origin", f"refs/heads/{default_branch}"), env=env
        )
        fields = out.split()
        if code != 0 or not fields:
            return None
        return fields[0]

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


def _write_payload(path: Path, files: Mapping[str, bytes]) -> None:
    """Write a resolved pack payload into the working tree, parents included."""
    for relative, content in files.items():
        target = path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def _bootstrap_message(applied: tuple[AppliedPack, ...]) -> str:
    """Commit subject recording the exact packs and versions the bootstrap landed (p.6)."""
    return _BOOTSTRAP_SUBJECT_PREFIX + ", ".join(f"{pack.name}@{pack.version}" for pack in applied)


async def _recorded_packs(path: Path) -> tuple[AppliedPack, ...]:
    """The packs the HEAD commit records as applied, or ``()`` for a foreign commit.

    The bootstrap writes the packs and their versions into its commit subject
    (``_BOOTSTRAP_SUBJECT_PREFIX``), so a physical no-op can report what the
    repository actually carries instead of the caller's request (ADR-031 p.6). A
    commit a bootstrap did not write records nothing.
    """
    code, out = await _run_git(path, ("log", "-1", "--format=%s"), env=None)
    if code != 0:
        return ()
    subject = out.strip()
    if not subject.startswith(_BOOTSTRAP_SUBJECT_PREFIX):
        return ()
    recorded: list[AppliedPack] = []
    for chunk in subject.removeprefix(_BOOTSTRAP_SUBJECT_PREFIX).split(", "):
        name, separator, version = chunk.partition("@")
        if not separator or not name or not version:
            return ()
        recorded.append(AppliedPack(name=name, version=version))
    return tuple(recorded)


async def _tree_changed(path: Path) -> bool:
    """True when the working tree has something staged; an unborn HEAD counts as a change."""
    code, out = await _run_git(path, ("status", "--porcelain"), env=None)
    if code != 0:
        raise _local_git_failure("status")
    return out.strip() != ""


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
