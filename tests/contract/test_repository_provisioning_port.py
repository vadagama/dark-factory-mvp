"""Contract tests for RepositoryProvisioningPort (T067, ADR-031)."""

import asyncio

import pytest

from dark_factory.ports import (
    BaselineBootstrapResult,
    ProvisioningOperationUnsupportedError,
    RepositoryProvisioningPort,
    RepositoryState,
)
from tests.contract.conftest import _ProvisioningBinding

PACKS = ("product-baseline",)


def _bootstrap(binding: _ProvisioningBinding, key: str) -> BaselineBootstrapResult:
    """One bootstrap of the binding's repository, keyed as the test asks."""
    return asyncio.run(
        binding.port.bootstrap_baseline(binding.repository, packs=PACKS, idempotency_key=key)
    )


def _require_bootstrap(binding: _ProvisioningBinding) -> None:
    """Skip the post-bootstrap assertions an adapter without the capability cannot make."""
    if not binding.supports_bootstrap:
        pytest.skip("the adapter deliberately does not apply packs (ADR-031 p.2/p.7)")


def test_adapter_satisfies_protocol(provisioning_binding: _ProvisioningBinding) -> None:
    assert isinstance(provisioning_binding.port, RepositoryProvisioningPort)
    assert not isinstance(object(), RepositoryProvisioningPort)


# --- validate: read-only, no idempotency key (ADR-031 p.4/p.5) --------------


def test_validate_of_an_absent_repository_is_unavailable(
    provisioning_binding: _ProvisioningBinding,
) -> None:
    provisioning_binding.seed("unavailable")
    result = asyncio.run(provisioning_binding.port.validate(provisioning_binding.repository))
    assert result.repository == provisioning_binding.repository
    assert result.state is RepositoryState.UNAVAILABLE
    assert result.head_revision is None
    assert result.default_branch is None


def test_validate_of_an_unborn_head_is_empty(provisioning_binding: _ProvisioningBinding) -> None:
    # An empty repository is the normal case of ADR-031 p.4, not an error: the
    # baseline bootstrap is what gives it its first revision.
    provisioning_binding.seed("empty")
    result = asyncio.run(provisioning_binding.port.validate(provisioning_binding.repository))
    assert result.state is RepositoryState.EMPTY
    assert result.head_revision is None
    assert result.default_branch == "main"


def test_validate_reports_commits_without_a_baseline(
    provisioning_binding: _ProvisioningBinding,
) -> None:
    revision = provisioning_binding.seed("baseline_absent")
    result = asyncio.run(provisioning_binding.port.validate(provisioning_binding.repository))
    assert result.state is RepositoryState.BASELINE_ABSENT
    assert result.head_revision == revision
    assert result.default_branch == "main"


def test_validate_reports_a_present_baseline(provisioning_binding: _ProvisioningBinding) -> None:
    revision = provisioning_binding.seed("baseline_current")
    result = asyncio.run(provisioning_binding.port.validate(provisioning_binding.repository))
    assert result.state is RepositoryState.BASELINE_CURRENT
    assert result.head_revision == revision


def test_validate_mutates_nothing(provisioning_binding: _ProvisioningBinding) -> None:
    revision = provisioning_binding.seed("baseline_absent")
    first = asyncio.run(provisioning_binding.port.validate(provisioning_binding.repository))
    second = asyncio.run(provisioning_binding.port.validate(provisioning_binding.repository))
    assert first == second
    assert second.head_revision == revision


# --- ensure_mirror: replay-dedup by key, absent is a KeyError (ADR-031 p.3) --


def test_ensure_mirror_returns_a_locator(provisioning_binding: _ProvisioningBinding) -> None:
    provisioning_binding.seed("baseline_absent")
    ref = asyncio.run(
        provisioning_binding.port.ensure_mirror(
            provisioning_binding.repository, idempotency_key="m-1"
        )
    )
    assert ref.repository == provisioning_binding.repository
    assert ref.location != ""
    assert ref.head_revision is not None


def test_ensure_mirror_replays_the_same_key_to_the_same_locator(
    provisioning_binding: _ProvisioningBinding,
) -> None:
    provisioning_binding.seed("baseline_absent")
    first = asyncio.run(
        provisioning_binding.port.ensure_mirror(
            provisioning_binding.repository, idempotency_key="m-1"
        )
    )
    second = asyncio.run(
        provisioning_binding.port.ensure_mirror(
            provisioning_binding.repository, idempotency_key="m-1"
        )
    )
    assert second == first


def test_ensure_mirror_of_an_absent_repository_is_a_keyerror(
    provisioning_binding: _ProvisioningBinding,
) -> None:
    provisioning_binding.seed("unavailable")
    with pytest.raises(KeyError):
        asyncio.run(
            provisioning_binding.port.ensure_mirror(
                provisioning_binding.repository, idempotency_key="m-1"
            )
        )


# --- bootstrap_baseline: capability split, never a silent success (p.2/p.6) --


def test_bootstrap_baseline_is_explicit_about_the_adapter_capability(
    provisioning_binding: _ProvisioningBinding,
) -> None:
    provisioning_binding.seed("empty")
    if not provisioning_binding.supports_bootstrap:
        # ADR-031 p.2: LocalMirror serves an operator-prepared mirror and does not
        # apply packs (p.2/p.7). It must say so, not report a bootstrap it never did.
        with pytest.raises(ProvisioningOperationUnsupportedError) as excinfo:
            asyncio.run(
                provisioning_binding.port.bootstrap_baseline(
                    provisioning_binding.repository, packs=PACKS, idempotency_key="b-1"
                )
            )
        assert excinfo.value.operation == "bootstrap_baseline"
        return

    first = asyncio.run(
        provisioning_binding.port.bootstrap_baseline(
            provisioning_binding.repository, packs=PACKS, idempotency_key="b-1"
        )
    )
    second = asyncio.run(
        provisioning_binding.port.bootstrap_baseline(
            provisioning_binding.repository, packs=PACKS, idempotency_key="b-1"
        )
    )

    assert second == first  # one external effect per key (ADR-006 p.3)
    assert first.revision != ""
    assert [pack.name for pack in first.applied_packs] == list(PACKS)


def test_a_bootstrapped_repository_reads_back_as_baseline_current(
    provisioning_binding: _ProvisioningBinding,
) -> None:
    # Evidence, not a message (ADR-031 p.6): after a successful bootstrap the
    # repository reads back as BASELINE_CURRENT at exactly the returned revision.
    _require_bootstrap(provisioning_binding)
    provisioning_binding.seed("empty")
    result = _bootstrap(provisioning_binding, "b-1")

    validation = asyncio.run(provisioning_binding.port.validate(provisioning_binding.repository))
    assert validation.state is RepositoryState.BASELINE_CURRENT
    assert validation.head_revision == result.revision


def test_a_replayed_bootstrap_key_leaves_the_revision_untouched(
    provisioning_binding: _ProvisioningBinding,
) -> None:
    _require_bootstrap(provisioning_binding)
    provisioning_binding.seed("empty")
    first = _bootstrap(provisioning_binding, "b-1")
    second = _bootstrap(provisioning_binding, "b-1")

    assert second == first  # the same key returns the same revision (FR-017)
    validation = asyncio.run(provisioning_binding.port.validate(provisioning_binding.repository))
    assert validation.head_revision == first.revision


def test_a_new_bootstrap_key_on_a_current_baseline_creates_no_second_commit(
    provisioning_binding: _ProvisioningBinding,
) -> None:
    # A key the bootstrap has not seen on a HEAD that already carries the baseline
    # stages nothing: the effect is the physical no-op, not a second commit (T069).
    _require_bootstrap(provisioning_binding)
    provisioning_binding.seed("empty")
    first = _bootstrap(provisioning_binding, "b-1")
    second = _bootstrap(provisioning_binding, "b-2")

    assert second.revision == first.revision
    assert second.applied_packs == first.applied_packs
    validation = asyncio.run(provisioning_binding.port.validate(provisioning_binding.repository))
    assert validation.head_revision == first.revision


def test_bootstrap_on_a_baseline_less_repository_commits_on_top(
    provisioning_binding: _ProvisioningBinding,
) -> None:
    # Commits exist but no baseline: the bootstrap adds a commit instead of reusing the
    # head, and the baseline reads back at the new revision (ADR-031 p.4/p.6).
    _require_bootstrap(provisioning_binding)
    seeded = provisioning_binding.seed("baseline_absent")
    result = _bootstrap(provisioning_binding, "b-1")

    assert seeded is not None
    assert result.revision != seeded
    validation = asyncio.run(provisioning_binding.port.validate(provisioning_binding.repository))
    assert validation.state is RepositoryState.BASELINE_CURRENT
    assert validation.head_revision == result.revision
