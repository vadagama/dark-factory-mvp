"""OpenSpecAdapter: OpenSpec change directories ↔ native ChangeSets (T-016)."""

import asyncio
from pathlib import Path

import pytest
import yaml

from dark_factory.context.sdd.baseline import current_revision
from dark_factory.context.sdd.errors import MissingArtifactError
from dark_factory.context.sdd.models import AddOperation, Delta, DeltaOperationKind
from dark_factory.context.sdd.native import NativeChangeSetAdapter
from dark_factory.context.sdd.normalized import ChangeSet
from dark_factory.context.sdd.openspec import OpenSpecAdapter, parse_spec_sections
from tests.sdd_factories import (
    SDD_ARTIFACT,
    SDD_PRODUCT,
    SDD_REVISION,
    make_change_set,
    seed_baseline,
)

SPEC = "\n".join(
    [
        "# reservation specification",
        "",
        "## ADDED Requirements",
        "",
        "### Requirement: Reservation timeout",
        "",
        "Reservations must expire after 30 minutes.",
        "",
        "### Requirement: Grace period",
        "",
        "A grace period of 5 minutes precedes deletion.",
        "",
        "## MODIFIED Requirements",
        "",
        "### Requirement: Cancellation policy",
        "",
        "Cancellations are allowed until the start time.",
        "",
        "## REMOVED Requirements",
        "",
        "### Requirement: Legacy quota",
    ]
)


def _seed(tmp_path: Path) -> tuple[OpenSpecAdapter, Path]:
    root = tmp_path / ".factory"
    seed_baseline(root)
    openspec_root = tmp_path / "openspec"
    return OpenSpecAdapter(NativeChangeSetAdapter(root), openspec_root), openspec_root


def _fake_change(openspec_root: Path) -> None:
    change_dir = openspec_root / "changes" / "reservation-timeout" / "specs" / "reservation"
    change_dir.mkdir(parents=True)
    (change_dir / "spec.md").write_text(SPEC, encoding="utf-8")


def _exportable_change() -> ChangeSet:
    """Target fragment matches kebab(title) so export → import round-trips."""
    return make_change_set(
        delta=Delta(
            baseline_revision=SDD_REVISION,
            operations=[
                AddOperation(
                    target="req:pilot:reservation:reservation-timeout", artifact=SDD_ARTIFACT
                )
            ],
        )
    )


def test_parse_spec_sections_splits_kinds() -> None:
    entries = parse_spec_sections(SPEC)
    assert [(kind, name) for kind, name, _body in entries] == [
        ("ADDED", "Reservation timeout"),
        ("ADDED", "Grace period"),
        ("MODIFIED", "Cancellation policy"),
        ("REMOVED", "Legacy quota"),
    ]


def test_import_change_maps_sections_to_operations(tmp_path: Path) -> None:
    adapter, openspec_root = _seed(tmp_path)
    _fake_change(openspec_root)
    change = adapter.import_change("reservation-timeout")
    manifest = change.manifest
    assert manifest.id == "chg:pilot:2026:0001"
    assert manifest.product == SDD_PRODUCT
    assert manifest.title == "reservation-timeout"
    assert manifest.status.value == "proposed"
    assert change.delta is not None
    assert [(op.operation, op.target) for op in change.delta.operations] == [
        (DeltaOperationKind.ADD, "req:pilot:reservation:reservation-timeout"),
        (DeltaOperationKind.ADD, "req:pilot:reservation:grace-period"),
        (DeltaOperationKind.MODIFY, "req:pilot:reservation:cancellation-policy"),
        (DeltaOperationKind.RETIRE, "req:pilot:reservation:legacy-quota"),
    ]
    assert [doc.path for doc in change.documents] == [
        "spec/requirements/reservation/reservation-timeout.md",
        "spec/requirements/reservation/grace-period.md",
        "spec/requirements/reservation/cancellation-policy.md",
    ]
    assert manifest.baseline.revision == current_revision(adapter._native.factory_root)
    requirement = change.documents[0]
    assert requirement.frontmatter is not None
    assert requirement.frontmatter.id == "req:pilot:reservation:reservation-timeout"
    assert requirement.frontmatter.status == "proposed"
    assert requirement.frontmatter.change == manifest.id


def test_import_change_writes_change_yaml_into_the_openspec_dir(tmp_path: Path) -> None:
    adapter, openspec_root = _seed(tmp_path)
    _fake_change(openspec_root)
    change = adapter.import_change("reservation-timeout")
    change_yaml = openspec_root / "changes" / "reservation-timeout" / "change.yaml"
    raw = yaml.safe_load(change_yaml.read_text(encoding="utf-8"))
    assert raw["id"] == change.manifest.id


def test_import_change_without_specs_dir_fails(tmp_path: Path) -> None:
    adapter, _openspec_root = _seed(tmp_path)
    with pytest.raises(MissingArtifactError):
        adapter.import_change("ghost")


def test_export_change_renders_openspec_directory(tmp_path: Path) -> None:
    adapter, openspec_root = _seed(tmp_path)
    change = make_change_set()
    exported = adapter.export_change(change)
    assert exported == openspec_root / "changes" / "reservation-timeout"
    text = (exported / "specs" / "reservation" / "spec.md").read_text(encoding="utf-8")
    assert "## ADDED Requirements" in text
    assert "### Requirement: Reservation timeout" in text
    assert "The reservation must expire after 30 minutes." in text
    change_yaml = exported / "change.yaml"
    raw = yaml.safe_load(change_yaml.read_text(encoding="utf-8"))
    assert raw["id"] == change.manifest.id


def test_export_then_import_round_trips_targets(tmp_path: Path) -> None:
    adapter, _openspec_root = _seed(tmp_path)
    adapter.export_change(_exportable_change())
    imported = adapter.import_change("reservation-timeout")
    assert imported.delta is not None
    assert [(op.operation, op.target) for op in imported.delta.operations] == [
        (DeltaOperationKind.ADD, "req:pilot:reservation:reservation-timeout")
    ]
    requirement = imported.documents[0]
    assert requirement.frontmatter is not None
    assert requirement.frontmatter.title == "Reservation timeout"


def test_port_delegation_create_and_read(tmp_path: Path) -> None:
    adapter, openspec_root = _seed(tmp_path)
    _fake_change(openspec_root)
    change_id = asyncio.run(adapter.create_change(adapter.import_change("reservation-timeout")))
    assert change_id == "chg:pilot:2026:0001"
    snapshot = asyncio.run(adapter.read_requirements(change_id))
    assert [entry.id for entry in snapshot.requirements] == [
        "req:pilot:reservation:reservation-timeout",
        "req:pilot:reservation:grace-period",
        "req:pilot:reservation:cancellation-policy",
        "req:pilot:reservation:legacy-quota",
    ]


def test_port_delegation_apply_delta(tmp_path: Path) -> None:
    adapter, _openspec_root = _seed(tmp_path)
    adapter.export_change(_exportable_change())
    change_id = asyncio.run(adapter.create_change(adapter.import_change("reservation-timeout")))
    revision = current_revision(adapter._native.factory_root)
    new_revision = asyncio.run(adapter.apply_delta(change_id, expected_revision=revision))
    assert new_revision != revision
    requirements_dir = adapter._native.factory_root / "product" / "requirements" / "reservation"
    assert (requirements_dir / "reservation-timeout.md").is_file()
