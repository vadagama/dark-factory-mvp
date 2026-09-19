"""Product Baseline: content-hash revisions and reconciliation (T-016)."""

import asyncio
from pathlib import Path

import pytest

from dark_factory.changes.enums import GateStatus
from dark_factory.changes.run_records import from_yaml
from dark_factory.context.sdd.baseline import (
    BaselineFile,
    apply_delta_operations,
    compute_revision,
    current_revision,
    read_factory_manifest,
    scan_baseline,
    write_reconciliation_result,
)
from dark_factory.context.sdd.errors import BaselineMismatchError, MissingArtifactError
from dark_factory.context.sdd.frontmatter import parse_frontmatter, render_document
from dark_factory.context.sdd.models import (
    AddOperation,
    ChangeManifest,
    Frontmatter,
    ReconciliationResult,
    RetireOperation,
    SupersedeOperation,
)
from dark_factory.context.sdd.native import NativeChangeSetAdapter
from dark_factory.quality.gates.specification import evaluate_specification_gate
from tests.sdd_factories import (
    SDD_ARTIFACT,
    SDD_CHANGE_ID,
    SDD_PRODUCT,
    SDD_TARGET,
    make_change_set,
    make_delta,
    make_manifest,
    seed_baseline,
)

REPO_FACTORY_ROOT = Path(__file__).resolve().parents[1] / ".factory"


def _created_change(factory_root: Path) -> None:
    """Create the sample ChangeSet (CHG-0002) on disk with its revision pinned."""
    revision = current_revision(factory_root)
    change = make_change_set(
        manifest=make_manifest(baseline={"revision": revision}),
        delta=make_delta(baseline_revision=revision),
    )
    asyncio.run(NativeChangeSetAdapter(factory_root).create_change(change))


def test_revision_is_order_and_content_sensitive() -> None:
    files = [
        BaselineFile(path="product/a.md", content_hash="h1"),
        BaselineFile(path="product/b.md", content_hash="h2"),
    ]
    shuffled = [files[1], files[0]]
    assert compute_revision(files) == compute_revision(shuffled)
    changed = [
        BaselineFile(path="product/a.md", content_hash="h1!"),
        BaselineFile(path="product/b.md", content_hash="h2"),
    ]
    assert compute_revision(changed) != compute_revision(files)


def test_init_baseline_is_minimally_valid_and_deterministic(tmp_path: Path) -> None:
    root = tmp_path / ".factory"
    seed_baseline(root)
    manifest = read_factory_manifest(root)
    assert manifest.product == SDD_PRODUCT
    files = scan_baseline(root)
    assert [item.path for item in files] == ["product/product.md"]
    assert current_revision(root) == current_revision(root)


def test_repo_baseline_is_valid() -> None:
    manifest = read_factory_manifest(REPO_FACTORY_ROOT)
    assert manifest.product == "dark-factory"
    product_md = REPO_FACTORY_ROOT / "product" / "product.md"
    frontmatter = parse_frontmatter(product_md.read_text(encoding="utf-8"))
    assert frontmatter is not None
    assert frontmatter.status == "active"
    assert current_revision(REPO_FACTORY_ROOT) != ""


def test_repo_changesets_load_and_pass_spec_gate() -> None:
    """Every ChangeSet committed under ``.factory/changes/`` loads and passes the gate.

    Locks the canonical artifacts of this repository in CI (ADR-020): the same
    strict schemas and the same specification gate the factory applies to product
    repositories. ``specs/`` stays bootstrap evidence and is not read here.
    """
    change_files = sorted((REPO_FACTORY_ROOT / "changes").glob("*/*/change.yaml"))
    assert change_files, "no ChangeSet committed under .factory/changes/"
    adapter = NativeChangeSetAdapter(REPO_FACTORY_ROOT)
    for path in change_files:
        manifest = from_yaml(ChangeManifest, path.read_text(encoding="utf-8"))
        decision = evaluate_specification_gate(adapter.read_change(manifest.id))
        assert decision.result is GateStatus.PASSED, f"{manifest.id}: {decision.explanation}"


def test_add_operation_copies_artifact_into_baseline(tmp_path: Path) -> None:
    root = tmp_path / ".factory"
    seed_baseline(root)
    _created_change(root)
    change_dir = root / "changes" / "2026" / "CHG-0002-reservation-timeout"
    revision = current_revision(root)
    result = apply_delta_operations(
        root,
        change_dir,
        [AddOperation(target=SDD_TARGET, artifact=SDD_ARTIFACT)],
        change_id=SDD_CHANGE_ID,
        expected_revision=revision,
    )
    baseline_doc = root / "product" / SDD_ARTIFACT
    assert baseline_doc.is_file()
    frontmatter = parse_frontmatter(baseline_doc.read_text(encoding="utf-8"))
    assert frontmatter is not None
    assert frontmatter.status == "active"
    assert result.original_revision == revision
    assert result.new_revision != revision
    assert result.applied_operations == 1
    write_reconciliation_result(root, change_dir, result)
    assert (change_dir / "reconciliation" / "result.yaml").is_file()


def test_stale_revision_aborts_before_mutation(tmp_path: Path) -> None:
    root = tmp_path / ".factory"
    seed_baseline(root)
    _created_change(root)
    change_dir = root / "changes" / "2026" / "CHG-0002-reservation-timeout"
    with pytest.raises(BaselineMismatchError):
        apply_delta_operations(
            root,
            change_dir,
            [AddOperation(target=SDD_TARGET, artifact=SDD_ARTIFACT)],
            change_id=SDD_CHANGE_ID,
            expected_revision="stale",
        )
    assert not (root / "product" / SDD_ARTIFACT).exists()


def test_supersede_and_retire_flip_baseline_status(tmp_path: Path) -> None:
    root = tmp_path / ".factory"
    seed_baseline(root)
    legacy = Frontmatter(
        schema_="dark-factory.dev/requirement/v1",
        id="req:pilot:reservation:legacy",
        type="requirement",
        title="Legacy timeout",
        product=SDD_PRODUCT,
        status="active",
        change="chg:pilot:2026:0001",
    )
    (root / "product" / "requirements").mkdir(parents=True)
    legacy_path = root / "product" / "requirements" / "legacy.md"
    legacy_path.write_text(render_document(legacy, "Legacy rule."), encoding="utf-8")

    apply_delta_operations(
        root,
        tmp_path / "empty-change",
        [SupersedeOperation(target="req:pilot:reservation:legacy", superseded_by=SDD_TARGET)],
        change_id=SDD_CHANGE_ID,
        expected_revision=current_revision(root),
    )
    superseded = parse_frontmatter(legacy_path.read_text(encoding="utf-8"))
    assert superseded is not None
    assert superseded.status == "superseded"

    apply_delta_operations(
        root,
        tmp_path / "empty-change",
        [RetireOperation(target="req:pilot:reservation:legacy")],
        change_id=SDD_CHANGE_ID,
        expected_revision=current_revision(root),
    )
    retired = parse_frontmatter(legacy_path.read_text(encoding="utf-8"))
    assert retired is not None
    assert retired.status == "retired"


def test_missing_targets_and_artifacts_are_errors(tmp_path: Path) -> None:
    root = tmp_path / ".factory"
    seed_baseline(root)
    revision = current_revision(root)
    with pytest.raises(MissingArtifactError, match="not found under product/"):
        apply_delta_operations(
            root,
            tmp_path / "empty-change",
            [RetireOperation(target="req:pilot:reservation:ghost")],
            change_id=SDD_CHANGE_ID,
            expected_revision=revision,
        )
    with pytest.raises(MissingArtifactError, match="not found in"):
        apply_delta_operations(
            root,
            tmp_path / "empty-change",
            [AddOperation(target=SDD_TARGET, artifact=SDD_ARTIFACT)],
            change_id=SDD_CHANGE_ID,
            expected_revision=revision,
        )


def test_reconciliation_result_round_trip(tmp_path: Path) -> None:
    result = ReconciliationResult(
        change_id=SDD_CHANGE_ID, original_revision="aaa", new_revision="bbb", applied_operations=2
    )
    write_reconciliation_result(tmp_path, tmp_path, result)
    restored = from_yaml(
        ReconciliationResult,
        (tmp_path / "reconciliation" / "result.yaml").read_text(encoding="utf-8"),
    )
    assert restored == result
