"""Pack ``packs/product-baseline``: templates are valid and round-trip (T-022).

The pack is data, not code: its templates are loaded through the same strict
schemas, gate and adapter the factory uses (ADR-020), so a drifted template
fails CI instead of a product repository.
"""

import asyncio
import re
import shutil
from pathlib import Path

import pytest
import yaml

from dark_factory.changes.enums import GateStatus, RiskClass
from dark_factory.context.sdd.baseline import current_revision, read_factory_manifest
from dark_factory.context.sdd.errors import BaselineMismatchError
from dark_factory.context.sdd.frontmatter import parse_frontmatter
from dark_factory.context.sdd.lifecycle import ChangeSetStatus
from dark_factory.context.sdd.models import (
    CHANGE_ID_PATTERN,
    ChangeManifest,
    Delta,
    DeltaOperationKind,
    EvidenceIndex,
    TaskGraph,
    VerificationPlan,
)
from dark_factory.context.sdd.native import NativeChangeSetAdapter, change_dir_name
from dark_factory.context.sdd.strictness import ArtifactSlot, WorkflowProfile
from dark_factory.quality.gates import evaluate_specification_gate

PACK_ROOT = Path(__file__).resolve().parents[1] / "packs" / "product-baseline"
CHANGESET = PACK_ROOT / "changeset"
REVISION_PLACEHOLDER = "0" * 64
CHANGE_ID = "chg:example-product:2026:0001"
CHECKOUT_TIMEOUT = "req:example-product:checkout:timeout"

# Only these two template files pin the baseline revision; everything else is
# copied as-is.
_REVISED_FILES = frozenset({"change.yaml", "spec/delta.yaml"})


def _read_manifest() -> ChangeManifest:
    return ChangeManifest.model_validate(
        yaml.safe_load((CHANGESET / "change.yaml").read_text(encoding="utf-8"))
    )


def test_baseline_template_is_valid() -> None:
    manifest = read_factory_manifest(PACK_ROOT / "baseline")
    assert manifest.product == "example-product"
    for path in sorted((PACK_ROOT / "baseline").rglob("*.md")):
        document = parse_frontmatter(path.read_text(encoding="utf-8"))
        assert document is not None, path
        assert document.product == "example-product", path
        assert document.status == "active", path
    assert current_revision(PACK_ROOT / "baseline")


def test_changeset_template_matches_strict_schemas() -> None:
    manifest = _read_manifest()
    assert re.match(CHANGE_ID_PATTERN, manifest.id)
    assert manifest.id == CHANGE_ID
    assert manifest.workflow.profile is WorkflowProfile.PRODUCT_FEATURE
    assert manifest.risk_class is RiskClass.R1
    assert manifest.status is ChangeSetStatus.DRAFT
    assert set(manifest.artifacts) == set(ArtifactSlot) - {ArtifactSlot.RECONCILIATION}

    delta = Delta.model_validate(
        yaml.safe_load((CHANGESET / "spec" / "delta.yaml").read_text(encoding="utf-8"))
    )
    assert delta.baseline_revision == manifest.baseline.revision == REVISION_PLACEHOLDER
    assert [operation.target for operation in delta.operations] == [CHECKOUT_TIMEOUT]

    tasks = TaskGraph.model_validate(
        yaml.safe_load((CHANGESET / "tasks" / "graph.yaml").read_text(encoding="utf-8"))
    )
    assert [(task.id, task.satisfies) for task in tasks.tasks] == [("TASK-001", [CHECKOUT_TIMEOUT])]

    verification = VerificationPlan.model_validate(
        yaml.safe_load((CHANGESET / "verification" / "plan.yaml").read_text(encoding="utf-8"))
    )
    assert verification.requirements[0].requirement == CHECKOUT_TIMEOUT
    assert any(check.type == "acceptance-scenario" for check in verification.requirements[0].checks)

    evidence = EvidenceIndex.model_validate(
        yaml.safe_load((CHANGESET / "evidence" / "index.yaml").read_text(encoding="utf-8"))
    )
    assert evidence.evidence == []


def test_changeset_template_round_trips_through_adapter(tmp_path: Path) -> None:
    factory_root = tmp_path / ".factory"
    shutil.copytree(PACK_ROOT / "baseline", factory_root)
    actual = current_revision(factory_root)

    manifest = _read_manifest()
    change_dir = factory_root / "changes" / manifest.id.split(":")[2] / change_dir_name(manifest)
    for path in sorted(CHANGESET.rglob("*")):
        if not path.is_file():
            continue
        target = change_dir / path.relative_to(CHANGESET)
        target.parent.mkdir(parents=True, exist_ok=True)
        text = path.read_text(encoding="utf-8")
        if path.relative_to(CHANGESET).as_posix() in _REVISED_FILES:
            text = text.replace(REVISION_PLACEHOLDER, actual)
        target.write_text(text, encoding="utf-8")

    adapter = NativeChangeSetAdapter(factory_root)
    decision = evaluate_specification_gate(adapter.read_change(CHANGE_ID))
    assert decision.result is GateStatus.PASSED

    snapshot = asyncio.run(adapter.read_requirements(CHANGE_ID))
    assert [(entry.id, entry.operation) for entry in snapshot.requirements] == [
        (CHECKOUT_TIMEOUT, DeltaOperationKind.ADD)
    ]

    new_revision = asyncio.run(adapter.apply_delta(CHANGE_ID, expected_revision=actual))
    assert new_revision != actual
    baseline_doc = factory_root / "product" / "requirements" / "REQ-001-checkout-timeout.md"
    assert baseline_doc.is_file()
    document = parse_frontmatter(baseline_doc.read_text(encoding="utf-8"))
    assert document is not None and document.status == "active"
    assert (change_dir / "reconciliation" / "result.yaml").is_file()

    # The revision moved: replaying the same delta is a parallel change and
    # must be rejected.
    with pytest.raises(BaselineMismatchError):
        asyncio.run(adapter.apply_delta(CHANGE_ID, expected_revision=actual))
