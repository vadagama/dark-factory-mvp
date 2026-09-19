"""LocalMirror and WorktreeExecution share one mirror convention (T067, ADR-031).

The provisioning adapter and the execution adapter cannot import each other: an
adapter may import the core only through ``dark_factory.ports``
(``tests/test_import_boundaries.py`` rule B). The variable and the layout they both
depend on are therefore pinned here — the same variable selects the mirror root,
and a mirror validated for provisioning is mintable as a workspace by the execution
adapter, which is what "the existing mirror behaviour moved into the adapter
without regression" means in practice.
"""

import asyncio
from pathlib import Path

from dark_factory.adapters.provisioning import LocalMirror, LocalMirrorConfig
from dark_factory.adapters.provisioning.local_mirror import MIRROR_ROOT_ENV_VAR
from dark_factory.changes.enums import Provider
from dark_factory.changes.refs import RepositoryRef
from dark_factory.execution import (
    WORKSPACE_MIRROR_ROOT_ENV_VAR,
    WorktreeExecution,
    WorktreeExecutionConfig,
)
from dark_factory.ports import RepositoryState, WorkspaceRequest
from tests.contract.conftest import _seed_local_mirror

REPOSITORY = RepositoryRef(provider=Provider.GITHUB, slug="small/pilot")


def test_the_mirror_root_variable_is_the_one_execution_reads() -> None:
    assert MIRROR_ROOT_ENV_VAR == WORKSPACE_MIRROR_ROOT_ENV_VAR


def test_a_validated_mirror_is_mintable_by_the_execution_adapter(tmp_path: Path) -> None:
    mirror_root = tmp_path / "mirror"
    revision = _seed_local_mirror(mirror_root, REPOSITORY, "baseline_absent")
    assert revision is not None

    validation = asyncio.run(
        LocalMirror(LocalMirrorConfig(mirror_root=mirror_root)).validate(REPOSITORY)
    )
    assert validation.state is RepositoryState.BASELINE_ABSENT

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
