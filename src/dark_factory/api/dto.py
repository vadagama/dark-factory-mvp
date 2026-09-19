"""Request/response DTOs of the factory API (T035, contract api.md).

Domain Pydantic models (``Change``, ``StageResult``, ``Decision``, ``Finding``,
``GateResult``, ``Evidence``) are reused directly as request/response schemas
so the API wire format equals the versioned run-record contracts (ADR-015).
"""

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from dark_factory.changes.conversations import ArtifactAnchor, Comment
from dark_factory.changes.enums import AnchorState, AnswerKind, Gate, Phase
from dark_factory.changes.findings import GateResult
from dark_factory.changes.product import Product
from dark_factory.changes.refs import ArtifactRef, RepositoryRef
from dark_factory.changes.run import Change
from dark_factory.context.artifacts import ArtifactDocument, ArtifactTree
from dark_factory.orchestration.ci import CiStage, CiStageGroup, CiStageWeight
from dark_factory.ports import BaselineBootstrapResult, RepositoryValidation


class ErrorBody(BaseModel):
    """RFC 7807-like error body of every failed response (contract api.md)."""

    type: str = "about:blank"
    title: str
    status: int
    detail: str


class ApprovalRequest(BaseModel):
    """Body of ``POST /changes/{change_id}/approvals`` (contract api.md).

    ``subject_revision`` is required: the decision is version-bound to it
    (FR-003, ADR-009 p.7). ``expected_state_revision`` turns the write into an
    optimistic-concurrency check (ADR-006 p.4).
    """

    gate: Gate
    outcome: Literal["approved", "rejected", "waived"]
    subject_revision: str | None = Field(default=None, min_length=1)
    """The revision the decision binds to. Required for ``approved`` and ``rejected``;
    a ``waived`` phase may have no artifacts at all (a UI-free change, T097), so a
    waiver binds to the revision when one is given and stays unbound otherwise —
    a waiver is about the phase, not about a document (ADR-032 p.5)."""
    comment: str | None = None
    expected_state_revision: int | None = None
    phase: Phase | None = None
    """The phase the decision approves (M3, ADR-039); default — the phase of ``gate``.

    Required in practice for ``architecture``, which shares the ``specification``
    gate with ``requirements``; must agree with ``gate`` (422 otherwise).
    """

    @model_validator(mode="after")
    def _revision_unless_waived(self) -> "ApprovalRequest":
        if self.outcome != "waived" and self.subject_revision is None:
            raise ValueError("subject_revision is required for an approved or rejected decision")
        return self


class RunSummary(BaseModel):
    """Item of ``GET /runs``."""

    run_id: str
    change_id: str
    route: str
    provider: str
    status: str
    state_revision: int
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None


class StageSummary(BaseModel):
    """Logical stage operation of a run (the ``stage`` table)."""

    stage: str
    status: str
    input_revision: str | None
    attempt_count: int


class UsageAggregate(BaseModel):
    """Usage sums over the run's attempts (FR-018/FR-024, SC-003)."""

    prompt_tokens: int
    completion_tokens: int
    cost: Decimal | None
    manual_interventions: int


class RunCard(BaseModel):
    """Response of ``GET /runs/{run_id}``: state, stages, gates, blockers, cost."""

    run_id: str
    change_id: str
    route: str
    provider: str
    status: str
    state_revision: int
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None
    stages: list[StageSummary]
    usage: UsageAggregate
    gates: list[GateResult]
    open_blockers: int


class TraceStage(BaseModel):
    """One stage link of the SC-007 trace chain."""

    stage: str
    status: str
    input_revision: str | None
    attempt_number: int
    produced_at: datetime
    artifacts: list[ArtifactRef]


class RunTrace(BaseModel):
    """Response of ``GET /runs/{run_id}/trace`` (SC-007)."""

    change_id: str
    run_id: str
    status: str
    chain: list[TraceStage]


class ChangeRunRef(BaseModel):
    """One run of a change on the change card."""

    run_id: str
    status: str


class ChangeCard(Change):
    """Response of ``GET /changes/{change_id}``: the Change document plus links."""

    runs: list[ChangeRunRef] = Field(default_factory=list)
    decisions_count: int = 0


class ChangeTrace(BaseModel):
    """Response of ``GET /changes/{change_id}/trace``: the full SC-007 chain."""

    change_id: str
    runs: list[RunTrace]


class CiStageView(BaseModel):
    """One CI stage of ``GET /ci/stages`` (T059, ADR-027).

    ``enabled`` is ``True`` when the stage runs, ``False`` when its toggle
    variable switches it off, and ``None`` when this contour cannot reach the
    repository (``available=false``): an unknown state is reported as unknown
    rather than guessed (fail-closed).
    """

    job: str
    title: str
    group: CiStageGroup
    summary: str
    local_command: str
    weight: CiStageWeight
    variable: str
    enabled: bool | None = None

    @classmethod
    def of(cls, stage: CiStage, enabled: bool | None) -> "CiStageView":
        """View of one catalog entry with its current (or unknown) state."""
        return cls(
            job=stage.job,
            title=stage.title,
            group=stage.group,
            summary=stage.summary,
            local_command=stage.local_command,
            weight=stage.weight,
            variable=stage.variable,
            enabled=enabled,
        )


class CiStagesView(BaseModel):
    """Response of ``GET /ci/stages``: the catalog plus the current toggle state."""

    schema_version: int = 1
    repository: str | None = None
    available: bool
    reason: str | None = None
    stages: list[CiStageView]


class CiStageToggleRequest(BaseModel):
    """Body of ``PUT /ci/stages/{job}``: the wanted state, not an event (idempotent).

    ``strict=True``: a toggle is a boolean, and pydantic must not coerce
    ``"yes"`` or ``1`` into it — the state of a pipeline gate is not a place for
    a lenient parse.
    """

    enabled: bool = Field(strict=True)


class ProductCreateRequest(BaseModel):
    """Body of ``POST /products`` (T066, ADR-030 p.1, contract api.md).

    ``id`` is client-chosen like the change id, so registration replays by it and
    a retry cannot create a second product (FR-017). Readiness is observed, never
    submitted: ``status``, ``state_revision`` and ``created_at`` belong to the
    server.
    """

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    repository: RepositoryRef
    description: str | None = None
    repository_url: str | None = None
    baseline_ref: str | None = None
    dev_env_ref: str | None = None


class BriefFormulateRequest(BaseModel):
    """Body of ``POST /briefs/formulate`` (T072): the operator's free text."""

    source_text: str = Field(min_length=1)


class ProductValidateRequest(BaseModel):
    """Body of ``POST /products/{product_id}/validate`` (T066, contract api.md).

    ``expected_state_revision`` turns the transition into an optimistic-concurrency
    check (ADR-006 p.4); absent means "validate whatever is current".
    """

    expected_state_revision: int | None = None


class ProductValidationView(Product):
    """Response of ``POST /products/{product_id}/validate``: the product plus the observation.

    ``validation`` is the raw provisioning result (ADR-031 p.4): the status is the
    coarse ``ready``/``error`` outcome, while this field keeps the observed state
    (empty repository, baseline absent/current/stale) and the revision.
    """

    validation: RepositoryValidation | None = None


# --- conversations (T086, ADR-034) --------------------------------------------------


class QuestionCreateRequest(BaseModel):
    """Body of ``POST /changes/{change_id}/questions``: a question as the agent states it.

    ``phase`` defaults to the phase of the change's latest run; ``anchor`` is
    ``{artifact, anchor_id, revision}`` (ADR-034 p.1).
    """

    text: str = Field(min_length=1)
    kind: AnswerKind = AnswerKind.TEXT
    options: list[str] = Field(default_factory=list)
    anchor: ArtifactAnchor | None = None
    blocking: bool = True
    phase: Phase | None = None
    run_id: str | None = None


class AnswerRequest(BaseModel):
    """Body of ``POST /changes/{change_id}/questions/{question_id}/answer``."""

    value: str = Field(min_length=1)
    comment: str | None = None


class CommentCreateRequest(BaseModel):
    """Body of ``POST /changes/{change_id}/comments``: a remark anchored to a fragment.

    ``revision`` defaults to the current head of the change branch when a
    repository is bound, so the anchor is version-bound (ADR-034 p.1).
    """

    artifact: str = Field(min_length=1)
    anchor_id: str | None = None
    revision: str | None = None
    body: str = Field(min_length=1)
    phase: Phase | None = None


class CommentNoteRequest(BaseModel):
    """Body of ``POST .../comments/{comment_id}/addressed``: the agent's optional note."""

    note: str | None = None


class CommentView(Comment):
    """A comment plus the read-model state of its anchor at the current revision."""

    anchor_state: AnchorState = AnchorState.ATTACHED


class ReworkOrderRequest(BaseModel):
    """Body of ``POST /changes/{change_id}/rework-orders``: the send-back (ADR-034 p.2/p.3).

    The order carries the comments and the answered questions it names plus a
    free-text instruction; at least one of them is required. ``expected_revision``
    turns the send-back into an optimistic check against the current head.
    """

    phase: Phase | None = None
    comment_ids: list[str] = Field(default_factory=list)
    question_ids: list[str] = Field(default_factory=list)
    instruction: str | None = None
    expected_revision: str | None = None


class DecisionAlternativeRequest(BaseModel):
    """Body of ``POST /changes/{change_id}/decisions/{decision_id}/alternative`` (T093).

    «Запросить альтернативу»: a rework order of the architecture phase about one
    ADR. ``instruction`` is required and non-blank — the agent must know what to
    reconsider; ``comment_ids`` carries the operator's remarks along.
    """

    instruction: str = Field(min_length=1)
    comment_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _instruction_is_not_blank(self) -> "DecisionAlternativeRequest":
        if not self.instruction.strip():
            raise ValueError("instruction must not be blank")
        return self


# --- artifacts (T082-T085, ADR-035) ----------------------------------------------------


class ArtifactDraftView(BaseModel):
    """The autosave draft of one artifact and whether its base is behind the head."""

    change_id: str
    artifact: str
    content: str
    base_revision: str | None
    saved_by: str
    updated_at: datetime
    stale: bool = False
    """``True`` when the branch head moved away from ``base_revision`` (ADR-035 p.4)."""


class ArtifactTreeView(ArtifactTree):
    """Response of ``GET /changes/{change_id}/artifacts``: the tree plus the draft paths."""

    drafts: list[str] = Field(default_factory=list)


class ArtifactDocumentView(ArtifactDocument):
    """Response of ``GET /changes/{change_id}/artifacts/{path}``: the document and its context."""

    draft: ArtifactDraftView | None = None
    viewed: bool = False
    """The operator viewed *this* revision («просмотрено» ≠ «согласовано», ADR-034 p.2)."""
    open_comments: int = 0
    open_questions: int = 0


class ArtifactEditRequest(BaseModel):
    """Body of ``PUT /changes/{change_id}/artifacts/{path}``: the whole new content.

    ``base_revision`` is the revision the operator edited from; a head that
    changed the file since is a 409 (ADR-035 p.3). ``properties`` optionally
    replaces frontmatter values on top of ``content`` — protected keys are
    refused with 422 (ADR-035 p.5).
    """

    content: str
    base_revision: str | None = None
    message: str | None = None
    properties: dict[str, Any] | None = None


class ArtifactWriteView(BaseModel):
    """Response of ``PUT .../artifacts/{path}``: the new revision and the staleness effects."""

    path: str
    revision: str
    previous_revision: str | None
    created_commit: bool
    stale_questions: list[str] = Field(default_factory=list)
    detached_comments: list[str] = Field(default_factory=list)


class ArtifactDraftRequest(BaseModel):
    """Body of ``PUT /changes/{change_id}/artifact-drafts/{path}`` (autosave, ADR-035 p.4)."""

    content: str
    base_revision: str | None = None


class ArtifactViewRequest(BaseModel):
    """Body of ``POST /changes/{change_id}/artifact-views/{path}``: the revision viewed."""

    revision: str = Field(min_length=1)


class ProductBootstrapRequest(BaseModel):
    """Body of ``POST /products/{product_id}/bootstrap``: the baseline packs to apply (T069)."""

    packs: list[str] = Field(default_factory=lambda: ["product-baseline"])


class ProductBootstrapView(BaseModel):
    """Response of ``POST /products/{product_id}/bootstrap``: the product and the evidence."""

    product: Product
    result: BaselineBootstrapResult
