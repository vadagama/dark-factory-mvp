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
        self._baseline_packs: dict[str, tuple[AppliedPack, ...]] = {}

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
        # A seeded repository is the operator's, not a bootstrap's: nothing records
        # applied packs until a bootstrap creates the baseline itself.
        self._baseline_packs.pop(key, None)
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
        """Apply ``packs`` and record the revision they left, replay-dedup by key.

        Applying a baseline to an empty or baseline-less repository creates it, moves
        the seeded state to ``BASELINE_CURRENT`` and records the packs that produced
        it; a repository whose baseline is already current stages nothing and reports
        those recorded packs — never the request, and nothing when the baseline was
        seeded rather than bootstrapped. A new key never mints a second effect (T069,
        ADR-031 p.6).
        """
        cached = self._bootstraps.get(idempotency_key)
        if cached is not None:
            return cached
        if not packs:
            raise ValueError("bootstrap_baseline requires at least one pack")
        key = _key(repository)
        revision = self._revisions.get(key)
        if revision is None or self._states.get(key) is not RepositoryState.BASELINE_CURRENT:
            revision = f"baseline-{len(self._bootstraps) + 1}"
            self._states[key] = RepositoryState.BASELINE_CURRENT
            self._revisions[key] = revision
            self._baseline_packs[key] = tuple(
                AppliedPack(name=name, version=DEFAULT_PACK_VERSION) for name in packs
            )
        result = BaselineBootstrapResult(
            repository=repository,
            revision=revision,
            applied_packs=self._baseline_packs.get(key, ()),
        )
        self._bootstraps[idempotency_key] = result
        return result
