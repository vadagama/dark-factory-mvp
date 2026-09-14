"""Shared builders for the Native SDD Core tests (T-016)."""

from pathlib import Path

from dark_factory.changes.enums import RiskClass
from dark_factory.context.sdd.baseline import init_baseline
from dark_factory.context.sdd.lifecycle import ChangeSetStatus
from dark_factory.context.sdd.models import (
    AddOperation,
    BaselineRef,
    ChangeManifest,
    CheckSpec,
    Delta,
    Document,
    EvidenceEntry,
    EvidenceIndex,
    EvidenceResult,
    Frontmatter,
    ModifyOperation,
    Owners,
    ReconciliationPlan,
    RequirementVerification,
    RetireOperation,
    SupersedeOperation,
    TargetRef,
    TaskDef,
    TaskGraph,
    VerificationPlan,
    WorkflowRef,
)
from dark_factory.context.sdd.normalized import ChangeSet
from dark_factory.context.sdd.strictness import ArtifactSlot, WorkflowProfile

SDD_PRODUCT = "pilot"
SDD_CHANGE_ID = "chg:pilot:2026:0002"
SDD_TARGET = "req:pilot:reservation:timeout"
SDD_ARTIFACT = "requirements/REQ-001-timeout.md"
SDD_REVISION = "8f3a2c1"


def make_frontmatter(**overrides: object) -> Frontmatter:
    values: dict[str, object] = {
        "schema_": "dark-factory.dev/requirement/v1",
        "id": SDD_TARGET,
        "type": "requirement",
        "title": "Reservation timeout",
        "product": SDD_PRODUCT,
        "status": "proposed",
        "change": SDD_CHANGE_ID,
    }
    values.update(overrides)
    return Frontmatter.model_validate(values)


def make_manifest(**overrides: object) -> ChangeManifest:
    values: dict[str, object] = {
        "id": SDD_CHANGE_ID,
        "title": "Reservation timeout",
        "slug": "reservation-timeout",
        "product": SDD_PRODUCT,
        "kind": WorkflowProfile.PRODUCT_FEATURE.value,
        "risk_class": RiskClass.R1,
        "status": ChangeSetStatus.DRAFT,
        "baseline": BaselineRef(revision=SDD_REVISION),
        "workflow": WorkflowRef(profile=WorkflowProfile.PRODUCT_FEATURE),
        "owners": Owners(product="team-pilot", technical="team-core"),
        "targets": [TargetRef(repository="pilot-backend", role="implementation")],
        "artifacts": {
            ArtifactSlot.INTENT: "intent.md",
            ArtifactSlot.SPEC: "spec/delta.yaml",
            ArtifactSlot.DESIGN: "design/overview.md",
            ArtifactSlot.TASKS: "tasks/graph.yaml",
            ArtifactSlot.VERIFICATION: "verification/plan.yaml",
            ArtifactSlot.EVIDENCE: "evidence/index.yaml",
        },
    }
    values.update(overrides)
    return ChangeManifest.model_validate(values)


def make_delta(**overrides: object) -> Delta:
    values: dict[str, object] = {
        "baseline_revision": SDD_REVISION,
        "operations": [AddOperation(target=SDD_TARGET, artifact=SDD_ARTIFACT)],
    }
    values.update(overrides)
    return Delta.model_validate(values)


def make_documents(**overrides: object) -> list[Document]:
    return [
        Document(path="intent.md", body="# Why\n\nMake reservations expire."),
        Document(
            path="design/overview.md",
            frontmatter=make_frontmatter(
                schema_="dark-factory.dev/design/v1",
                id="design:pilot:reservation",
                type="design",
                title="Reservation design",
            ),
            body="# Design\n\nTimeout configuration.",
        ),
        Document(
            path=f"spec/{SDD_ARTIFACT}",
            frontmatter=make_frontmatter(),
            body="The reservation must expire after 30 minutes.",
        ),
    ]


def make_change_set(**overrides: object) -> ChangeSet:
    values: dict[str, object] = {
        "manifest": make_manifest(),
        "delta": make_delta(),
        "documents": make_documents(),
        "tasks": TaskGraph(
            tasks=[
                TaskDef(
                    id="TASK-001",
                    title="Implement the timeout",
                    repository="pilot-backend",
                    type="implementation",
                    satisfies=[SDD_TARGET],
                    depends_on=[],
                )
            ]
        ),
        "verification": VerificationPlan(
            requirements=[
                RequirementVerification(
                    requirement=SDD_TARGET, checks=[CheckSpec(type="unit-test")]
                )
            ]
        ),
        "evidence": EvidenceIndex(
            evidence=[
                EvidenceEntry(
                    id="EVD-001",
                    type="test-report",
                    verifies=[SDD_TARGET],
                    uri="s3://factory-evidence/report.xml",
                    digest="sha256:abc123",
                    result=EvidenceResult.PASSED,
                )
            ]
        ),
    }
    values.update(overrides)
    return ChangeSet.model_validate(values)


def make_supersede_delta(
    target: str = "req:pilot:reservation:legacy", superseded_by: str = SDD_TARGET
) -> Delta:
    return Delta(
        baseline_revision=SDD_REVISION,
        operations=[
            AddOperation(target=SDD_TARGET, artifact=SDD_ARTIFACT),
            SupersedeOperation(target=target, superseded_by=superseded_by),
            RetireOperation(target="req:pilot:reservation:obsolete"),
            ModifyOperation(
                target="req:pilot:reservation:limits", artifact="requirements/REQ-002-limits.md"
            ),
        ],
    )


def make_reconciliation_plan(revision: str = SDD_REVISION) -> ReconciliationPlan:
    return ReconciliationPlan(
        baseline_revision=revision,
        operations=[AddOperation(target=SDD_TARGET, artifact=SDD_ARTIFACT)],
    )


def seed_baseline(factory_root: Path, *, change: str = "chg:pilot:2026:0001") -> None:
    init_baseline(factory_root, product=SDD_PRODUCT, title="Pilot", change=change)
