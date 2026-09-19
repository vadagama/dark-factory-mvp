"""Repository provisioning DTOs behind ``RepositoryProvisioningPort`` (T067, ADR-031).

Result values of the three provisioning operations. Validation is explicit
about the states of ADR-031 p.4: an empty repository (unborn HEAD) is a normal
case in which the baseline will be created, while a repository that has commits
either lacks the baseline or carries one. ``BASELINE_STALE`` completes the
vocabulary for adapters that can compare the observed baseline against a
desired one (the packs comparison lands with T069).

Frozen pydantic models per the port DTO pattern; ``RepositoryRef`` is the shared
domain reference (ADR-019).
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from dark_factory.changes.refs import RepositoryRef


class RepositoryState(StrEnum):
    """State of a product repository as provisioning observes it (ADR-031 p.1/p.4).

    ``UNAVAILABLE`` is the availability half of validation (p.1): the adapter
    cannot reach, or has not been given, the repository — no other fact is
    known about it. The remaining values describe a repository the adapter can
    observe (p.4):

    * ``EMPTY`` — the repository exists but HEAD is unborn: no commits yet.
      Normal, not an error (p.4); the baseline bootstrap creates the first one.
    * ``BASELINE_ABSENT`` — commits exist, no product baseline at HEAD.
    * ``BASELINE_CURRENT`` — commits exist and the product baseline is present.
    * ``BASELINE_STALE`` — a baseline is present but no longer matches the
      desired one.
    """

    UNAVAILABLE = "unavailable"
    EMPTY = "empty"
    BASELINE_ABSENT = "baseline_absent"
    BASELINE_CURRENT = "baseline_current"
    BASELINE_STALE = "baseline_stale"


class RepositoryValidation(BaseModel):
    """Read-only result of ``RepositoryProvisioningPort.validate`` (ADR-031 p.4/p.5).

    ``default_branch`` and ``head_revision`` are observed facts, not requests:
    an unborn HEAD has no revision and a detached HEAD has no branch, so both
    stay ``None`` rather than being guessed. Validation mutates nothing (p.5),
    so the value describes the repository as it is at the call.
    """

    model_config = ConfigDict(frozen=True)

    repository: RepositoryRef
    state: RepositoryState
    default_branch: str | None = None
    head_revision: str | None = None


class MirrorRef(BaseModel):
    """Locator of a prepared mirror (result of ``ensure_mirror``).

    ``location`` is the adapter's own address of the mirror — a local path for
    ``LocalMirror``, a remote URL for ``ProviderClone`` — opaque to the port so
    the core never depends on where the mirror lives.
    """

    model_config = ConfigDict(frozen=True)

    repository: RepositoryRef
    location: str = Field(min_length=1)
    default_branch: str | None = None
    head_revision: str | None = None


class AppliedPack(BaseModel):
    """One baseline pack landed by ``bootstrap_baseline``: its name and version."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)


class BaselineBootstrapResult(BaseModel):
    """Evidence of one baseline bootstrap (ADR-031 p.6: revision, packs, versions).

    ``revision`` is the repository revision the bootstrap left behind — for an
    empty repository it is the revision the baseline commit created. The value
    is evidence, not a message: "the repository is fine" is read from it.
    """

    model_config = ConfigDict(frozen=True)

    repository: RepositoryRef
    revision: str = Field(min_length=1)
    applied_packs: tuple[AppliedPack, ...] = ()
