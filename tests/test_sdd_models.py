"""Round-trips and wire shapes of the Native SDD Core artifact schemas (T-016)."""

import pytest
from pydantic import ValidationError

from dark_factory.changes.enums import RiskClass
from dark_factory.changes.run_records import from_yaml, to_yaml
from dark_factory.context.sdd.models import (
    ChangeManifest,
    Delta,
    DeltaOperationKind,
    Document,
    EvidenceEntry,
    EvidenceIndex,
    EvidenceResult,
    ReconciliationPlan,
    ReconciliationResult,
    RetireOperation,
    SupersedeOperation,
    TaskGraph,
    VerificationPlan,
)
from tests.sdd_factories import (
    SDD_ARTIFACT,
    SDD_REVISION,
    SDD_TARGET,
    make_change_set,
    make_delta,
    make_frontmatter,
    make_manifest,
    make_reconciliation_plan,
)

OPERATIONS = [
    {"operation": "add", "target": SDD_TARGET, "artifact": SDD_ARTIFACT},
    {"operation": "modify", "target": SDD_TARGET, "artifact": SDD_ARTIFACT},
    {
        "operation": "supersede",
        "target": "req:pilot:reservation:legacy",
        "superseded_by": SDD_TARGET,
    },
    {"operation": "retire", "target": "req:pilot:reservation:obsolete"},
]


def test_manifest_round_trips_through_yaml_with_schema_alias() -> None:
    manifest = make_manifest()
    text = to_yaml(manifest)
    assert "schema: dark-factory.dev/change/v1" in text
    assert "schema_" not in text
    assert from_yaml(ChangeManifest, text) == manifest


def test_manifest_accepts_wire_input() -> None:
    manifest = make_manifest()
    payload = manifest.model_dump(mode="json")
    assert payload["schema"] == "dark-factory.dev/change/v1"
    assert ChangeManifest.model_validate(payload) == manifest


def test_manifest_rejects_malformed_id() -> None:
    with pytest.raises(ValidationError):
        make_manifest(id="chg:pilot:26:1")
    with pytest.raises(ValidationError):
        make_manifest(id="CHG:pilot:2026:0002")


def test_manifest_artifacts_use_wire_slot_names() -> None:
    text = to_yaml(make_manifest())
    assert "intent: intent.md" in text
    assert "spec: spec/delta.yaml" in text
    assert "tasks: tasks/graph.yaml" in text


def test_manifest_risk_class_round_trips_as_wire_value() -> None:
    manifest = make_manifest(risk_class=RiskClass.R3)
    text = to_yaml(manifest)
    assert "risk_class: R3" in text
    assert from_yaml(ChangeManifest, text) == manifest


@pytest.mark.parametrize("payload", OPERATIONS, ids=["add", "modify", "supersede", "retire"])
def test_delta_operation_round_trips(payload: dict[str, str]) -> None:
    delta = Delta.model_validate({"baseline_revision": SDD_REVISION, "operations": [payload]})
    restored = from_yaml(Delta, to_yaml(delta))
    assert restored == delta
    assert restored.operations[0].operation.value == payload["operation"]


def test_delta_rejects_unknown_operation() -> None:
    with pytest.raises(ValidationError):
        Delta.model_validate(
            {
                "baseline_revision": SDD_REVISION,
                "operations": [{"operation": "teleport", "target": "x"}],
            }
        )


def test_supersede_operation_carries_successor() -> None:
    delta = make_delta(
        operations=[
            SupersedeOperation(target="req:pilot:reservation:legacy", superseded_by=SDD_TARGET)
        ]
    )
    restored = from_yaml(Delta, to_yaml(delta))
    assert restored.operations[0].superseded_by == SDD_TARGET  # type: ignore[union-attr]
    assert restored.operations[0].operation is DeltaOperationKind.SUPERSEDE


def test_retire_operation_has_no_artifact() -> None:
    delta = make_delta(operations=[RetireOperation(target="req:pilot:reservation:obsolete")])
    text = to_yaml(delta)
    assert "retire" in text
    assert "artifact" not in text


def test_task_graph_round_trips() -> None:
    changeset = make_change_set()
    assert changeset.tasks is not None
    restored = from_yaml(TaskGraph, to_yaml(changeset.tasks))
    assert restored == changeset.tasks
    assert "satisfies:" in to_yaml(changeset.tasks)


def test_verification_plan_round_trips() -> None:
    changeset = make_change_set()
    assert changeset.verification is not None
    restored = from_yaml(VerificationPlan, to_yaml(changeset.verification))
    assert restored == changeset.verification


def test_evidence_index_round_trips_with_wire_result() -> None:
    changeset = make_change_set()
    assert changeset.evidence is not None
    text = to_yaml(changeset.evidence)
    assert "result: passed" in text
    assert from_yaml(EvidenceIndex, text) == changeset.evidence


def test_evidence_entry_defaults_to_optional_and_available() -> None:
    entry = EvidenceEntry(
        id="EVD-001", type="test-report", uri="s3://x/report.xml", result=EvidenceResult.PASSED
    )
    assert entry.required is False
    assert entry.available is True


def test_reconciliation_plan_and_result_round_trip() -> None:
    changeset = make_change_set(reconciliation=make_reconciliation_plan())
    assert changeset.reconciliation is not None
    plan = from_yaml(ReconciliationPlan, to_yaml(changeset.reconciliation))
    assert plan == changeset.reconciliation
    result = ReconciliationResult.model_validate(
        {
            "change_id": "chg:pilot:2026:0002",
            "original_revision": "aaa",
            "new_revision": "bbb",
            "applied_operations": 1,
        }
    )
    restored = from_yaml(ReconciliationResult, to_yaml(result))
    assert restored == result
    assert "schema: dark-factory.dev/reconciliation-result/v1" in to_yaml(result)


def test_frontmatter_preserves_extra_fields() -> None:
    frontmatter = make_frontmatter(realizes=["req:pilot:reservation:timeout"])
    assert frontmatter.realizes == ["req:pilot:reservation:timeout"]  # type: ignore[attr-defined]
    restored = from_yaml(type(frontmatter), to_yaml(frontmatter))
    assert restored == frontmatter


def test_document_round_trips_without_frontmatter() -> None:
    document = Document(path="intent.md", body="# Why")
    restored = from_yaml(Document, to_yaml(document))
    assert restored == document
    assert restored.frontmatter is None
