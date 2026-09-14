"""SpecKitAdapter: read-only bootstrap import of legacy specs/<feature>/ (T-016)."""

import asyncio
from pathlib import Path

from dark_factory.context.sdd.baseline import current_revision
from dark_factory.context.sdd.frontmatter import parse_frontmatter
from dark_factory.context.sdd.models import AddOperation
from dark_factory.context.sdd.native import NativeChangeSetAdapter
from dark_factory.context.sdd.speckit import SpecKitAdapter, parse_tasks_markdown
from tests.sdd_factories import SDD_PRODUCT, seed_baseline

SPEC = "# Feature: Reservation timeouts\n\nReservations must expire.\n"
PLAN = "# Plan: Reservation timeouts\n\nUse a configuration value.\n"
TASKS = (
    "- [ ] T001 Add timeout configuration\n- [x] T002 Publish expiry event\n- [ ] not a task line\n"
)


def _adapter(tmp_path: Path) -> tuple[SpecKitAdapter, Path]:
    root = tmp_path / ".factory"
    seed_baseline(root)
    specs = tmp_path / "specs" / "001-reservation"
    specs.mkdir(parents=True)
    (specs / "spec.md").write_text(SPEC, encoding="utf-8")
    (specs / "plan.md").write_text(PLAN, encoding="utf-8")
    (specs / "tasks.md").write_text(TASKS, encoding="utf-8")
    native = NativeChangeSetAdapter(root)
    return SpecKitAdapter(native, tmp_path / "specs"), specs


def test_parse_tasks_markdown_extracts_checklist_tasks() -> None:
    tasks = parse_tasks_markdown(TASKS)
    assert [(task.id, task.title) for task in tasks] == [
        ("T001", "Add timeout configuration"),
        ("T002", "Publish expiry event"),
    ]


def test_import_feature_builds_a_draft_change_set(tmp_path: Path) -> None:
    adapter, _specs = _adapter(tmp_path)
    change = adapter.import_feature("001-reservation")
    manifest = change.manifest
    assert manifest.product == SDD_PRODUCT
    assert manifest.slug == "001-reservation"
    assert manifest.status.value == "draft"
    assert manifest.baseline.revision == current_revision(adapter._native.factory_root)
    assert manifest.id.startswith("chg:pilot:2026:0001")
    assert change.delta is not None
    first = change.delta.operations[0]
    assert isinstance(first, AddOperation)
    assert first.target == "req:pilot:001-reservation:main"
    assert first.artifact == "requirements/SPEC-001.md"
    paths = [doc.path for doc in change.documents]
    assert paths == ["intent.md", "spec/requirements/SPEC-001.md", "design/overview.md"]
    assert change.tasks is not None
    assert [task.id for task in change.tasks.tasks] == ["T001", "T002"]


def test_import_feature_is_read_only(tmp_path: Path) -> None:
    adapter, specs = _adapter(tmp_path)
    before = sorted(path.name for path in specs.rglob("*"))
    adapter.import_feature("001-reservation")
    after = sorted(path.name for path in specs.rglob("*"))
    assert before == after


def test_import_synthesizes_okf_frontmatter(tmp_path: Path) -> None:
    adapter, _specs = _adapter(tmp_path)
    change = adapter.import_feature("001-reservation")
    requirement = change.documents[1]
    assert requirement.frontmatter is not None
    assert requirement.frontmatter.schema_ == "dark-factory.dev/requirement/v1"
    assert requirement.frontmatter.status == "proposed"
    assert requirement.frontmatter.change == change.manifest.id
    design = change.documents[2]
    assert design.frontmatter is not None
    assert design.frontmatter.schema_ == "dark-factory.dev/design/v1"


def test_imported_feature_flows_through_the_port(tmp_path: Path) -> None:
    adapter, _specs = _adapter(tmp_path)
    change = adapter.import_feature("001-reservation")
    change_id = asyncio.run(adapter.create_change(change))
    snapshot = asyncio.run(adapter.read_requirements(change_id))
    assert [entry.id for entry in snapshot.requirements] == ["req:pilot:001-reservation:main"]
    new_revision = asyncio.run(
        adapter.apply_delta(change_id, expected_revision=change.manifest.baseline.revision)
    )
    assert new_revision != change.manifest.baseline.revision
    baseline_doc = adapter._native.factory_root / "product" / "requirements" / "SPEC-001.md"
    assert baseline_doc.is_file()
    frontmatter = parse_frontmatter(baseline_doc.read_text(encoding="utf-8"))
    assert frontmatter is not None
    assert frontmatter.status == "active"
