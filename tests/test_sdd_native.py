"""NativeChangeSetAdapter: create/read/apply over a .factory root (T-016)."""

import asyncio
from pathlib import Path

import pytest

from dark_factory.context.sdd.baseline import current_revision
from dark_factory.context.sdd.errors import (
    ChangeNotFoundError,
    MissingArtifactError,
    SDDError,
)
from dark_factory.context.sdd.models import DeltaOperationKind
from dark_factory.context.sdd.native import NativeChangeSetAdapter, change_dir_name, next_change_id
from dark_factory.context.sdd.normalized import ChangeSet
from tests.sdd_factories import (
    SDD_ARTIFACT,
    SDD_CHANGE_ID,
    SDD_TARGET,
    make_change_set,
    make_delta,
    make_manifest,
    seed_baseline,
)


def _native(tmp_path: Path) -> NativeChangeSetAdapter:
    root = tmp_path / ".factory"
    seed_baseline(root)
    return NativeChangeSetAdapter(root)


def _change(tmp_path: Path, native: NativeChangeSetAdapter) -> ChangeSet:
    revision = current_revision(native.factory_root)
    return make_change_set(
        manifest=make_manifest(baseline={"revision": revision}),
        delta=make_delta(baseline_revision=revision),
    )


def test_create_change_materializes_the_canonical_layout(tmp_path: Path) -> None:
    native = _native(tmp_path)
    change = _change(tmp_path, native)
    change_id = asyncio.run(native.create_change(change))
    assert change_id == SDD_CHANGE_ID
    change_dir = tmp_path / ".factory" / "changes" / "2026" / "CHG-0002-reservation-timeout"
    assert (change_dir / "change.yaml").is_file()
    assert (change_dir / "intent.md").is_file()
    assert (change_dir / "spec" / "delta.yaml").is_file()
    assert (change_dir / "spec" / SDD_ARTIFACT).is_file()
    assert (change_dir / "design" / "overview.md").is_file()
    assert (change_dir / "tasks" / "graph.yaml").is_file()
    assert (change_dir / "verification" / "plan.yaml").is_file()
    assert (change_dir / "evidence" / "index.yaml").is_file()
    # reconciliation is not declared by the R1 sample: no empty directories
    assert not (change_dir / "reconciliation").exists()


def test_create_change_rejects_an_existing_directory(tmp_path: Path) -> None:
    native = _native(tmp_path)
    change = _change(tmp_path, native)
    asyncio.run(native.create_change(change))
    with pytest.raises(SDDError, match="already exists"):
        asyncio.run(native.create_change(change))


def test_read_change_round_trips_the_aggregate(tmp_path: Path) -> None:
    native = _native(tmp_path)
    change = _change(tmp_path, native)
    asyncio.run(native.create_change(change))
    restored = native.read_change(SDD_CHANGE_ID)
    # Documents are files on disk: read_change returns them in path order, so
    # compare them keyed by path instead of list position.
    assert restored.manifest == change.manifest
    assert restored.delta == change.delta
    assert restored.tasks == change.tasks
    assert restored.verification == change.verification
    assert restored.evidence == change.evidence
    assert {doc.path: doc for doc in restored.documents} == {
        doc.path: doc for doc in change.documents
    }


def test_read_requirements_maps_all_operation_kinds(tmp_path: Path) -> None:
    native = _native(tmp_path)
    revision = current_revision(native.factory_root)
    change = make_change_set(
        manifest=make_manifest(baseline={"revision": revision}),
        delta=make_delta(
            baseline_revision=revision,
            operations=[
                {"operation": "add", "target": SDD_TARGET, "artifact": SDD_ARTIFACT},
                {
                    "operation": "supersede",
                    "target": "req:pilot:reservation:legacy",
                    "superseded_by": SDD_TARGET,
                },
                {"operation": "retire", "target": "req:pilot:reservation:obsolete"},
            ],
        ),
    )
    asyncio.run(native.create_change(change))
    snapshot = asyncio.run(native.read_requirements(SDD_CHANGE_ID))
    assert snapshot.change_id == SDD_CHANGE_ID
    assert snapshot.baseline_revision == revision
    kinds = [(entry.id, entry.operation) for entry in snapshot.requirements]
    assert kinds == [
        (SDD_TARGET, DeltaOperationKind.ADD),
        ("req:pilot:reservation:legacy", DeltaOperationKind.SUPERSEDE),
        ("req:pilot:reservation:obsolete", DeltaOperationKind.RETIRE),
    ]
    assert snapshot.requirements[1].superseded_by == SDD_TARGET


def test_read_requirements_of_unknown_change_raises(tmp_path: Path) -> None:
    native = _native(tmp_path)
    with pytest.raises(ChangeNotFoundError):
        asyncio.run(native.read_requirements("chg:pilot:2026:9999"))


def test_apply_delta_reconciles_and_writes_result(tmp_path: Path) -> None:
    native = _native(tmp_path)
    revision = current_revision(native.factory_root)
    asyncio.run(native.create_change(_change(tmp_path, native)))
    new_revision = asyncio.run(native.apply_delta(SDD_CHANGE_ID, expected_revision=revision))
    assert new_revision != revision
    baseline_doc = native.factory_root / "product" / SDD_ARTIFACT
    assert baseline_doc.is_file()
    result_path = (
        native.factory_root
        / "changes"
        / "2026"
        / "CHG-0002-reservation-timeout"
        / "reconciliation"
        / "result.yaml"
    )
    assert result_path.is_file()


def test_apply_delta_rejects_stale_revision(tmp_path: Path) -> None:
    from dark_factory.context.sdd.errors import BaselineMismatchError

    native = _native(tmp_path)
    asyncio.run(native.create_change(_change(tmp_path, native)))
    with pytest.raises(BaselineMismatchError):
        asyncio.run(native.apply_delta(SDD_CHANGE_ID, expected_revision="stale"))


def test_apply_delta_without_delta_file_raises(tmp_path: Path) -> None:
    native = _native(tmp_path)
    bare = make_change_set(delta=None, tasks=None, verification=None, evidence=None, documents=[])
    asyncio.run(native.create_change(bare))
    revision = current_revision(native.factory_root)
    with pytest.raises(MissingArtifactError, match=r"no spec/delta\.yaml"):
        asyncio.run(native.apply_delta(SDD_CHANGE_ID, expected_revision=revision))


def test_next_change_id_increments_per_year(tmp_path: Path) -> None:
    native = _native(tmp_path)
    assert next_change_id(native.factory_root, "pilot").endswith(":0001")
    asyncio.run(native.create_change(_change(tmp_path, native)))
    assert next_change_id(native.factory_root, "pilot").endswith(":0003")


def test_change_dir_name_matches_the_contract() -> None:
    manifest = make_manifest()
    assert change_dir_name(manifest) == "CHG-0002-reservation-timeout"
