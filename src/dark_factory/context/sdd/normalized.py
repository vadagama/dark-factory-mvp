"""Normalized ChangeSet contract for the specification gate (ADR-020, changeset.md).

The aggregate (:class:`ChangeSet`) bundles the manifest with the structured
artifacts read from a ChangeSet directory. :func:`normalize` evaluates the five
axes of the normalized contract — completeness, consistency, policy compliance,
coverage, evidence — **as data**: it yields findings, not decisions. The gate
itself (T-021) decides over this data and records a GateDecision.
"""

from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from dark_factory.changes.enums import RiskClass
from dark_factory.context.sdd.models import (
    ChangeManifest,
    Delta,
    DeltaOperationKind,
    Document,
    EvidenceIndex,
    ReconciliationPlan,
    TaskGraph,
    VerificationPlan,
)
from dark_factory.context.sdd.strictness import ArtifactSlot, required_artifacts

REQ_ID_PREFIX: Final = "req:"

# Manifest slots whose content lives in the aggregate as structured artifacts.
_SLOT_TO_ARTIFACT: Final[dict[ArtifactSlot, str]] = {
    ArtifactSlot.SPEC: "delta",
    ArtifactSlot.TASKS: "tasks",
    ArtifactSlot.VERIFICATION: "verification",
    ArtifactSlot.EVIDENCE: "evidence",
    ArtifactSlot.RECONCILIATION: "reconciliation",
}

# Manifest slots whose content is a markdown document inside the aggregate.
_DOCUMENT_SLOTS: Final[tuple[ArtifactSlot, ...]] = (ArtifactSlot.INTENT, ArtifactSlot.DESIGN)

# Statuses from which the contract expects recorded evidence (changeset.md:
# "для завершённого изменения есть evidence").
_EVIDENCE_EXPECTED_STATUSES: Final[frozenset[str]] = frozenset({"accepted", "reconciled", "closed"})


class Axis(StrEnum):
    """Axes of the normalized contract (changeset.md)."""

    COMPLETENESS = "completeness"
    CONSISTENCY = "consistency"
    POLICY = "policy"
    COVERAGE = "coverage"
    EVIDENCE = "evidence"


class AxisFinding(BaseModel):
    """One contract finding on an axis; the gate (T-021) weighs these."""

    model_config = ConfigDict(frozen=True)

    axis: Axis
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)


class ChangeSet(BaseModel):
    """Aggregate of a ChangeSet: manifest plus the artifacts read from disk.

    ``documents`` holds the markdown artifacts (intent, requirement, design,
    …) with optional frontmatter; YAML artifacts are the typed fields. What a
    given ChangeSet must contain is decided by strictness (workflow profile x
    risk class), not by this model.
    """

    manifest: ChangeManifest
    delta: Delta | None = None
    documents: list[Document] = []
    tasks: TaskGraph | None = None
    verification: VerificationPlan | None = None
    evidence: EvidenceIndex | None = None
    reconciliation: ReconciliationPlan | None = None


class RequirementEntry(BaseModel):
    """One requirement as seen through the spec delta of a ChangeSet."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    operation: DeltaOperationKind
    artifact: str | None = None
    superseded_by: str | None = None


class RequirementsSnapshot(BaseModel):
    """What ``SDDPort.read_requirements`` returns for a ChangeSet."""

    model_config = ConfigDict(frozen=True)

    change_id: str = Field(min_length=1)
    baseline_revision: str = Field(min_length=1)
    requirements: tuple[RequirementEntry, ...] = ()


class NormalizedChangeSet(BaseModel):
    """ChangeSet plus the per-axis findings of the normalized contract."""

    model_config = ConfigDict(frozen=True)

    changeset: ChangeSet
    completeness: tuple[AxisFinding, ...] = ()
    consistency: tuple[AxisFinding, ...] = ()
    policy: tuple[AxisFinding, ...] = ()
    coverage: tuple[AxisFinding, ...] = ()
    evidence: tuple[AxisFinding, ...] = ()


def _completeness_findings(changeset: ChangeSet) -> list[AxisFinding]:
    manifest = changeset.manifest
    required = required_artifacts(manifest.workflow.profile, manifest.risk_class)
    missing = required - manifest.artifacts.keys()
    return [
        AxisFinding(
            axis=Axis.COMPLETENESS,
            code="missing_required_artifact",
            message=f"required artifact slot {slot.value!r} is not declared in change.yaml",
        )
        for slot in sorted(missing, key=lambda slot: slot.value)
    ]


def _consistency_findings(changeset: ChangeSet) -> list[AxisFinding]:
    findings: list[AxisFinding] = []
    manifest = changeset.manifest
    delta = changeset.delta
    if delta is not None and delta.baseline_revision != manifest.baseline.revision:
        findings.append(
            AxisFinding(
                axis=Axis.CONSISTENCY,
                code="baseline_revision_mismatch",
                message=(
                    f"delta targets baseline {delta.baseline_revision!r} but the manifest "
                    f"pins {manifest.baseline.revision!r}"
                ),
            )
        )
    if delta is not None:
        seen: set[str] = set()
        for op in delta.operations:
            if op.target in seen:
                findings.append(
                    AxisFinding(
                        axis=Axis.CONSISTENCY,
                        code="duplicate_delta_target",
                        message=f"target {op.target!r} appears in more than one operation",
                    )
                )
            seen.add(op.target)
            if op.operation is DeltaOperationKind.SUPERSEDE and op.superseded_by == op.target:
                findings.append(
                    AxisFinding(
                        axis=Axis.CONSISTENCY,
                        code="self_supersede",
                        message=f"operation supersedes {op.target!r} by itself",
                    )
                )
    for slot in _DOCUMENT_SLOTS:
        path = manifest.artifacts.get(slot)
        if path is not None and not any(doc.path == path for doc in changeset.documents):
            findings.append(
                AxisFinding(
                    axis=Axis.CONSISTENCY,
                    code="declared_artifact_missing",
                    message=f"artifact {path!r} of slot {slot.value!r} is declared but absent",
                )
            )
    for slot, attribute in _SLOT_TO_ARTIFACT.items():
        if manifest.artifacts.get(slot) is not None and getattr(changeset, attribute) is None:
            findings.append(
                AxisFinding(
                    axis=Axis.CONSISTENCY,
                    code="declared_artifact_missing",
                    message=f"slot {slot.value!r} is declared but its artifact is not present",
                )
            )
    return findings


def _policy_findings(changeset: ChangeSet) -> list[AxisFinding]:
    manifest = changeset.manifest
    findings: list[AxisFinding] = []
    if (
        manifest.risk_class in (RiskClass.R2, RiskClass.R3, RiskClass.R4)
        and changeset.reconciliation is None
    ):
        findings.append(
            AxisFinding(
                axis=Axis.POLICY,
                code="reconciliation_required_for_high_risk",
                message=(f"risk class {manifest.risk_class.value} requires a reconciliation plan"),
            )
        )
    return findings


def _coverage_findings(changeset: ChangeSet) -> list[AxisFinding]:
    delta = changeset.delta
    if delta is None:
        return []
    requirement_targets = [
        op.target
        for op in delta.operations
        if op.operation in (DeltaOperationKind.ADD, DeltaOperationKind.MODIFY)
        and op.target.startswith(REQ_ID_PREFIX)
    ]
    findings: list[AxisFinding] = []
    satisfied: set[str] = set()
    if changeset.tasks is not None:
        for task in changeset.tasks.tasks:
            satisfied.update(task.satisfies)
    verified: set[str] = set()
    if changeset.verification is not None:
        verified.update(entry.requirement for entry in changeset.verification.requirements)
    for target in requirement_targets:
        if target not in satisfied:
            findings.append(
                AxisFinding(
                    axis=Axis.COVERAGE,
                    code="requirement_without_task",
                    message=f"requirement {target!r} is not satisfied by any task",
                )
            )
        if target not in verified:
            findings.append(
                AxisFinding(
                    axis=Axis.COVERAGE,
                    code="requirement_without_verification",
                    message=f"requirement {target!r} has no checks in the verification plan",
                )
            )
    return findings


def _evidence_findings(changeset: ChangeSet) -> list[AxisFinding]:
    findings: list[AxisFinding] = []
    evidence = changeset.evidence
    if evidence is not None:
        for entry in evidence.evidence:
            if entry.required and not entry.available:
                findings.append(
                    AxisFinding(
                        axis=Axis.EVIDENCE,
                        code="required_evidence_unavailable",
                        message=f"required evidence {entry.id!r} is unavailable",
                    )
                )
    if (
        changeset.evidence is None or not changeset.evidence.evidence
    ) and changeset.manifest.status.value in _EVIDENCE_EXPECTED_STATUSES:
        findings.append(
            AxisFinding(
                axis=Axis.EVIDENCE,
                code="missing_evidence_for_accepted_change",
                message=(f"change in status {changeset.manifest.status.value!r} has no evidence"),
            )
        )
    return findings


def normalize(changeset: ChangeSet) -> NormalizedChangeSet:
    """Evaluate all five axes of the normalized contract as data."""
    return NormalizedChangeSet(
        changeset=changeset,
        completeness=tuple(_completeness_findings(changeset)),
        consistency=tuple(_consistency_findings(changeset)),
        policy=tuple(_policy_findings(changeset)),
        coverage=tuple(_coverage_findings(changeset)),
        evidence=tuple(_evidence_findings(changeset)),
    )
