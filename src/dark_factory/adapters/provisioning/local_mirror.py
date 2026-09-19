"""``RepositoryProvisioningPort`` over an operator-prepared local mirror (T067, ADR-031).

``LocalMirror`` is the first of the two adapters ADR-031 p.2 binds to the port: it
serves the mirror an operator already prepared under
``DARK_FACTORY_WORKSPACE_MIRROR_ROOT``, laid out ``<root>/<provider>/<slug>`` — the
same convention the execution adapter (``execution.workspace``) works from. The two
read the variable and the layout by convention; the agreement is pinned by
``tests/contract/test_repository_provisioning_port.py`` so the two cannot drift (an
adapter may import the core only through ``dark_factory.ports``, so the constant
cannot be shared by import — ``tests/test_import_boundaries.py`` rule B).

Capabilities (ADR-031 p.2/p.4/p.5):

* ``validate`` probes the mirror and mutates nothing: an absent mirror is
  ``UNAVAILABLE``, an unborn HEAD is the normal ``EMPTY`` state, and a repository
  with commits is ``BASELINE_CURRENT`` or ``BASELINE_ABSENT`` by whether
  ``.factory/product`` (ADR-020) exists at HEAD. ``BASELINE_STALE`` is unreachable
  here — comparing the observed baseline against a desired one needs the packs of
  T069.
* ``ensure_mirror`` locates the operator-prepared mirror and returns its locator.
  Preparing the mirror is the operator's step, so nothing is fetched or created;
  a mirror that is not there is the port's ``KeyError`` (absent = 404).
* ``bootstrap_baseline`` is not performed: applying packs is T069, and an adapter
  must never report a bootstrap it did not perform (ADR-031 p.6).

The adapter reads a local path only: no network and no credentials (ADR-031 p.7).
"""

import asyncio
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

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
"""Root of the operator-prepared mirrors, laid out ``<root>/<provider>/<slug>``.

The execution adapter (``execution.workspace.WORKSPACE_MIRROR_ROOT_ENV_VAR``) reads
the same variable and the same layout. The two cannot import each other (rule B),
so the agreement is pinned by a contract test rather than a shared constant.
"""

BASELINE_PATH: Final[str] = ".factory/product"
"""Canonical Product Baseline inside the repository (ADR-020)."""


@dataclass(frozen=True, slots=True)
class LocalMirrorConfig:
    """Mirror root of the local provisioning adapter (ADR-031 p.2)."""

    mirror_root: Path

    def __post_init__(self) -> None:
        if not str(self.mirror_root) or not self.mirror_root.is_absolute():
            raise ValueError(f"{MIRROR_ROOT_ENV_VAR} must be an absolute path")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "LocalMirrorConfig | None":
        """Config read from ``env`` (default: the process environment); ``None`` when absent.

        An absent variable leaves the adapter absent; a variable that is set but
        empty or relative is a misconfiguration and fails closed, naming the
        variable and never the value (ADR-009).
        """
        source = os.environ if env is None else env
        value = source.get(MIRROR_ROOT_ENV_VAR)
        if value is None:
            return None
        return cls(mirror_root=Path(value))


class LocalMirror(RepositoryProvisioningPort):
    """``RepositoryProvisioningPort`` over an operator-prepared local mirror (ADR-031 p.2)."""

    def __init__(self, config: LocalMirrorConfig) -> None:
        self._config = config
        self._mirrors: dict[str, MirrorRef] = {}

    async def validate(self, repository: RepositoryRef, /) -> RepositoryValidation:
        mirror = self._mirror_path(repository)
        if not _is_git_repository(mirror):
            return RepositoryValidation(repository=repository, state=RepositoryState.UNAVAILABLE)
        branch = await _symbolic_branch(mirror)
        head = await _rev_parse(mirror, "HEAD")
        if head is None:
            return RepositoryValidation(
                repository=repository, state=RepositoryState.EMPTY, default_branch=branch
            )
        state = (
            RepositoryState.BASELINE_CURRENT
            if await _path_exists_at_head(mirror, BASELINE_PATH)
            else RepositoryState.BASELINE_ABSENT
        )
        return RepositoryValidation(
            repository=repository, state=state, default_branch=branch, head_revision=head
        )

    async def ensure_mirror(
        self, repository: RepositoryRef, /, *, idempotency_key: str
    ) -> MirrorRef:
        cached = self._mirrors.get(idempotency_key)
        if cached is not None:
            return cached
        mirror = self._mirror_path(repository)
        if not _is_git_repository(mirror):
            raise KeyError(f"the mirror of {repository.slug!r} is not available locally")
        ref = MirrorRef(
            repository=repository,
            location=str(mirror),
            default_branch=await _symbolic_branch(mirror),
            head_revision=await _rev_parse(mirror, "HEAD"),
        )
        self._mirrors[idempotency_key] = ref
        return ref

    async def bootstrap_baseline(
        self, repository: RepositoryRef, /, *, packs: Sequence[str], idempotency_key: str
    ) -> BaselineBootstrapResult:
        raise ProvisioningOperationUnsupportedError("bootstrap_baseline")

    def _mirror_path(self, repository: RepositoryRef) -> Path:
        return self._config.mirror_root / repository.provider.value / repository.slug


def _is_git_repository(path: Path) -> bool:
    """True for a working clone (``.git``) or a bare mirror (``HEAD``)."""
    return (path / ".git").exists() or (path / "HEAD").exists()


async def _symbolic_branch(mirror: Path) -> str | None:
    """The branch HEAD points at; ``None`` on a detached HEAD (ADR-031 p.4)."""
    code, out = await _run_git(mirror, ("symbolic-ref", "--short", "HEAD"))
    if code != 0:
        return None
    return out.strip() or None


async def _rev_parse(mirror: Path, *argv: str) -> str | None:
    """``git rev-parse`` output, or ``None`` when the revision does not resolve."""
    code, out = await _run_git(mirror, ("rev-parse", *argv))
    if code != 0:
        return None
    return out.strip() or None


async def _path_exists_at_head(mirror: Path, path: str) -> bool:
    """True when ``path`` is present in the tree of HEAD."""
    code, out = await _run_git(mirror, ("ls-tree", "--name-only", "HEAD", path))
    return code == 0 and out.strip() != ""


async def _run_git(cwd: Path, argv: tuple[str, ...]) -> tuple[int, str]:
    """Run one git command in ``cwd``; failures are reported, not raised."""
    process = await asyncio.create_subprocess_exec(
        "git",
        *argv,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await process.communicate()
    code = process.returncode
    return (code if code is not None else -1), stdout.decode("utf-8", errors="replace")
