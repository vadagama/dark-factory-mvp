"""In-memory ``RepositoryProvisioningPort`` fake (ADR-019 p.6, T067, ADR-031).

All state stays in memory — no filesystem, no network — and every keyed mutation
is replay-idempotent. ``seed`` materialises one repository state, so the contract
suite drives the same assertions against the fake and the real ``LocalMirror``.
"""

from collections.abc import Sequence

from dark_factory.ports import (
    AppliedPack,
    BaselineBootstrapResult,
    MirrorRef,
    RepositoryProvisioningPort,
    RepositoryRef,
    RepositoryState,
    RepositoryValidation,
)

DEFAULT_BRANCH: str = "main"
"""Branch the fake reports for a seeded, non-empty repository."""

DEFAULT_REVISION: str = "f4c1e0d"
"""Head revision the fake reports when ``seed`` is not given one."""

DEFAULT_PACK_VERSION: str = "1"
"""Version the fake stamps on every pack it applies."""

_DEFAULT_STATE: RepositoryState = RepositoryState.UNAVAILABLE
"""An unseeded repository behaves like an absent mirror (ADR-031 p.1)."""


def _key(repository: RepositoryRef) -> str:
    return f"{repository.provider.value}/{repository.slug}"


class FakeRepositoryProvisioning(RepositoryProvisioningPort):
    """In-memory provisioning: seeded repository states with keyed replay (ADR-019 p.6)."""

    def __init__(self) -> None:
        self._states: dict[str, RepositoryState] = {}
        self._branches: dict[str, str | None] = {}
        self._revisions: dict[str, str | None] = {}
        self._mirrors: dict[str, MirrorRef] = {}
        self._bootstraps: dict[str, BaselineBootstrapResult] = {}

    def seed(
        self,
        repository: RepositoryRef,
        state: RepositoryState | str,
        *,
        head_revision: str | None = None,
        default_branch: str = DEFAULT_BRANCH,
    ) -> str | None:
        """Materialise one repository state; returns its head revision (``None`` if none)."""
        resolved = RepositoryState(state)
        key = _key(repository)
        self._states[key] = resolved
        if resolved is RepositoryState.UNAVAILABLE:
            self._branches[key] = None
            self._revisions[key] = None
            return None
        self._branches[key] = default_branch
        if resolved is RepositoryState.EMPTY:
            self._revisions[key] = None
            return None
        revision = head_revision or DEFAULT_REVISION
        self._revisions[key] = revision
        return revision

    async def validate(self, repository: RepositoryRef, /) -> RepositoryValidation:
        key = _key(repository)
        return RepositoryValidation(
            repository=repository,
            state=self._states.get(key, _DEFAULT_STATE),
            default_branch=self._branches.get(key),
            head_revision=self._revisions.get(key),
        )

    async def ensure_mirror(
        self, repository: RepositoryRef, /, *, idempotency_key: str
    ) -> MirrorRef:
        cached = self._mirrors.get(idempotency_key)
        if cached is not None:
            return cached
        key = _key(repository)
        if self._states.get(key, _DEFAULT_STATE) is RepositoryState.UNAVAILABLE:
            raise KeyError(f"the mirror of {repository.slug!r} is not available")
        ref = MirrorRef(
            repository=repository,
            location=f"fake://{key}",
            default_branch=self._branches.get(key),
            head_revision=self._revisions.get(key),
        )
        self._mirrors[idempotency_key] = ref
        return ref

    async def bootstrap_baseline(
        self, repository: RepositoryRef, /, *, packs: Sequence[str], idempotency_key: str
    ) -> BaselineBootstrapResult:
        cached = self._bootstraps.get(idempotency_key)
        if cached is not None:
            return cached
        applied = tuple(AppliedPack(name=name, version=DEFAULT_PACK_VERSION) for name in packs)
        result = BaselineBootstrapResult(
            repository=repository,
            revision=f"baseline-{len(self._bootstraps) + 1}",
            applied_packs=applied,
        )
        self._bootstraps[idempotency_key] = result
        return result
