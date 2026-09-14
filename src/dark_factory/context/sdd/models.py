"""Versioned Pydantic schemas of Native SDD Core artifacts (ADR-020).

Wire shapes follow the canonical examples in ``docs/sdd-native-core.md`` §6-12
and ``specs/001-dark-factory-mvp/contracts/changeset.md``: ``change.yaml``,
``spec/delta.yaml``, ``tasks/graph.yaml``, ``verification/plan.yaml``,
``evidence/index.yaml``, ``reconciliation/{plan,result}.yaml`` and the minimal
YAML frontmatter of OKF nodes (§8).

``schema`` is a reserved word in Python: the field is ``schema_`` with the wire
alias ``schema`` (``populate_by_name`` + ``serialize_by_alias``), so YAML
round-trips keep the canonical key. No timestamps: Git owns history and
baseline revisions are content hashes (``baseline.py``).
"""

from enum import StrEnum
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from dark_factory.changes.enums import RiskClass
from dark_factory.context.sdd.lifecycle import (
    ChangeSetStatus,
    validate_change_status_transition,
)
from dark_factory.context.sdd.strictness import ArtifactSlot, WorkflowProfile

CHANGE_SCHEMA: Final = "dark-factory.dev/change/v1"
TASK_GRAPH_SCHEMA: Final = "dark-factory.dev/task-graph/v1"
DESIGN_SCHEMA: Final = "dark-factory.dev/design/v1"
VERIFICATION_PLAN_SCHEMA: Final = "dark-factory.dev/verification-plan/v1"
EVIDENCE_INDEX_SCHEMA: Final = "dark-factory.dev/evidence-index/v1"
RECONCILIATION_PLAN_SCHEMA: Final = "dark-factory.dev/reconciliation-plan/v1"
RECONCILIATION_RESULT_SCHEMA: Final = "dark-factory.dev/reconciliation-result/v1"

# Literal contract types of the schema constants (mypy cannot parameterize
# Literal with a Final variable, cf. ContextSchemaVersion in bundle.py); keep
# each pair in sync.
type ChangeSchema = Literal["dark-factory.dev/change/v1"]
type TaskGraphSchema = Literal["dark-factory.dev/task-graph/v1"]
type VerificationPlanSchema = Literal["dark-factory.dev/verification-plan/v1"]
type EvidenceIndexSchema = Literal["dark-factory.dev/evidence-index/v1"]
type ReconciliationPlanSchema = Literal["dark-factory.dev/reconciliation-plan/v1"]
type ReconciliationResultSchema = Literal["dark-factory.dev/reconciliation-result/v1"]

# chg:<product>:<year>:<NNNN> — stable, globally addressable ChangeSet id
# (changeset.md); product is lowercase kebab, NNNN is zero-padded.
CHANGE_ID_PATTERN: Final = r"^chg:[a-z0-9][a-z0-9-]*:\d{4}:\d{4}$"

# Config for every model carrying the ``schema`` alias: accept both python and
# wire names on input, always emit the wire name.
_SCHEMA_MODEL_CONFIG: Final = ConfigDict(populate_by_name=True, serialize_by_alias=True)


class BaselineRef(BaseModel):
    """Baseline revision a ChangeSet was created against (``change.yaml``)."""

    model_config = ConfigDict(frozen=True)

    revision: str = Field(min_length=1)


class WorkflowRef(BaseModel):
    """Strictness profile of the change (changeset.md)."""

    model_config = ConfigDict(frozen=True)

    profile: WorkflowProfile
    version: str = Field(default="1.0", min_length=1)


class Owners(BaseModel):
    """Product and technical owners (``change.yaml``)."""

    model_config = ConfigDict(frozen=True)

    product: str | None = None
    technical: str | None = None


class TargetRef(BaseModel):
    """One target repository of the change and its role there (multi-repo, ADR-020)."""

    model_config = ConfigDict(frozen=True)

    repository: str = Field(min_length=1)
    role: str = Field(min_length=1)


class ChangeManifest(BaseModel):
    """Manifest and entry point of a ChangeSet (``change.yaml``, §6).

    Mutable like the run-domain entities: status moves through
    :meth:`apply_status` (single validation point, lifecycle.py).
    """

    model_config = _SCHEMA_MODEL_CONFIG

    schema_: ChangeSchema = Field(CHANGE_SCHEMA, alias="schema")
    id: str = Field(pattern=CHANGE_ID_PATTERN)
    title: str = Field(min_length=1)
    slug: str = Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")
    product: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    risk_class: RiskClass
    status: ChangeSetStatus = ChangeSetStatus.DRAFT
    baseline: BaselineRef
    workflow: WorkflowRef
    owners: Owners | None = None
    targets: list[TargetRef] = []
    artifacts: dict[ArtifactSlot, str] = {}

    def apply_status(self, target: ChangeSetStatus) -> None:
        """Move to ``target``; raises InvalidChangeSetStatus outside the table."""
        validate_change_status_transition(self.status, target)
        self.status = target


class DeltaOperationKind(StrEnum):
    """Delta operation over the baseline (§9, changeset.md)."""

    ADD = "add"
    MODIFY = "modify"
    SUPERSEDE = "supersede"
    RETIRE = "retire"


class AddOperation(BaseModel):
    """Introduce a new OKF node; ``artifact`` is relative to ``spec/``."""

    model_config = ConfigDict(frozen=True)

    operation: Literal[DeltaOperationKind.ADD] = DeltaOperationKind.ADD
    target: str = Field(min_length=1)
    artifact: str = Field(min_length=1)


class ModifyOperation(BaseModel):
    """Change an existing OKF node in place."""

    model_config = ConfigDict(frozen=True)

    operation: Literal[DeltaOperationKind.MODIFY] = DeltaOperationKind.MODIFY
    target: str = Field(min_length=1)
    artifact: str = Field(min_length=1)


class SupersedeOperation(BaseModel):
    """Replace a node with its successor; both stay in the baseline history."""

    model_config = ConfigDict(frozen=True)

    operation: Literal[DeltaOperationKind.SUPERSEDE] = DeltaOperationKind.SUPERSEDE
    target: str = Field(min_length=1)
    superseded_by: str = Field(min_length=1)


class RetireOperation(BaseModel):
    """Withdraw a node; it stays in the baseline with ``retired`` status (§4)."""

    model_config = ConfigDict(frozen=True)

    operation: Literal[DeltaOperationKind.RETIRE] = DeltaOperationKind.RETIRE
    target: str = Field(min_length=1)


DeltaOperation = Annotated[
    AddOperation | ModifyOperation | SupersedeOperation | RetireOperation,
    Field(discriminator="operation"),
]


class Delta(BaseModel):
    """Spec delta over the baseline (``spec/delta.yaml``, §9) — not a full spec copy."""

    model_config = ConfigDict(frozen=True)

    baseline_revision: str = Field(min_length=1)
    operations: list[DeltaOperation] = []


class TaskDef(BaseModel):
    """One task of the canonical TaskGraph with traceability (§11)."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    repository: str | None = None
    type: str | None = None
    satisfies: list[str] = []
    depends_on: list[str] = []


class TaskGraph(BaseModel):
    """Canonical decomposition (``tasks/graph.yaml``); ``tasks.md`` is a generated view."""

    model_config = _SCHEMA_MODEL_CONFIG

    schema_: TaskGraphSchema = Field(TASK_GRAPH_SCHEMA, alias="schema")
    tasks: list[TaskDef] = []


class Frontmatter(BaseModel):
    """Minimal YAML frontmatter of a self-addressable OKF node (§7-8).

    ``status`` follows the containing artifact's lifecycle: ChangeSet documents
    carry change statuses (proposed, …), baseline documents carry the accepted
    states active/superseded/retired. Additional fields (``owner``, ``risk``,
    ``realizes``, ``relations``, …) are allowed and preserved as extras.
    """

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True, extra="allow")

    schema_: str = Field(alias="schema", min_length=1)
    id: str = Field(min_length=1)
    type: str = Field(min_length=1)
    title: str = Field(min_length=1)
    product: str = Field(min_length=1)
    status: str = Field(min_length=1)
    change: str = Field(min_length=1)


class Document(BaseModel):
    """Markdown artifact of a ChangeSet: optional frontmatter + body.

    Self-addressable artifacts carry frontmatter; generated views may omit it
    (§7). ``path`` is relative to the ChangeSet directory.
    """

    model_config = ConfigDict(frozen=True)

    path: str = Field(min_length=1)
    frontmatter: Frontmatter | None = None
    body: str = ""


class CheckSpec(BaseModel):
    """One planned check of a requirement (§12)."""

    model_config = ConfigDict(frozen=True)

    type: str = Field(min_length=1)


class RequirementVerification(BaseModel):
    """Checks defined for one requirement before implementation starts (§12)."""

    model_config = ConfigDict(frozen=True)

    requirement: str = Field(min_length=1)
    checks: list[CheckSpec] = []


class VerificationPlan(BaseModel):
    """Verification defined up front (``verification/plan.yaml``, §12)."""

    model_config = _SCHEMA_MODEL_CONFIG

    schema_: VerificationPlanSchema = Field(VERIFICATION_PLAN_SCHEMA, alias="schema")
    requirements: list[RequirementVerification] = []


class EvidenceResult(StrEnum):
    """Outcome recorded by an evidence entry."""

    PASSED = "passed"
    FAILED = "failed"


class EvidenceEntry(BaseModel):
    """Link, digest and provenance of one evidence item (§12).

    ``required``/``available`` back the gate axis "required && !available
    forbids success" (changeset.md); heavy payloads live in CI artifacts or
    Object Storage, Git keeps the reference.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    type: str = Field(min_length=1)
    verifies: list[str] = []
    uri: str = Field(min_length=1)
    digest: str | None = None
    result: EvidenceResult
    required: bool = False
    available: bool = True


class EvidenceIndex(BaseModel):
    """Evidence index of a ChangeSet (``evidence/index.yaml``, §12)."""

    model_config = _SCHEMA_MODEL_CONFIG

    schema_: EvidenceIndexSchema = Field(EVIDENCE_INDEX_SCHEMA, alias="schema")
    evidence: list[EvidenceEntry] = []


class ReconciliationPlan(BaseModel):
    """Plan of moving accepted artifacts into the baseline (§14)."""

    model_config = _SCHEMA_MODEL_CONFIG

    schema_: ReconciliationPlanSchema = Field(RECONCILIATION_PLAN_SCHEMA, alias="schema")
    baseline_revision: str = Field(min_length=1)
    operations: list[DeltaOperation] = []


class ReconciliationResult(BaseModel):
    """Outcome of a reconciliation run (``reconciliation/result.yaml``, §14)."""

    model_config = _SCHEMA_MODEL_CONFIG

    schema_: ReconciliationResultSchema = Field(RECONCILIATION_RESULT_SCHEMA, alias="schema")
    change_id: str = Field(min_length=1)
    original_revision: str = Field(min_length=1)
    new_revision: str = Field(min_length=1)
    applied_operations: int = Field(default=0, ge=0)
    conflicts: list[str] = []
