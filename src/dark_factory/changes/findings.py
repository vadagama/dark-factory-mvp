"""Findings, gate results and decisions (hld-mvp 8, vision 3.3-3.4)."""

from datetime import datetime

from pydantic import BaseModel, Field

from dark_factory.changes.enums import (
    DecisionOutcome,
    DecisionSource,
    FindingOrigin,
    FindingSeverity,
    FindingStatus,
    Gate,
    GateStatus,
    Role,
)


class Finding(BaseModel):
    """Structured review finding: id, origin, severity, file/line, reviewed SHA, action, status.

    ``confidence`` is the producer's self-assessed certainty (0.0-1.0); agent
    findings carry it, human and CI findings may leave it unset. It documents
    the judgment, it never changes the blocking weight — the gate weighs
    severity and status (FR-006, T-013).
    """

    id: str = Field(min_length=1)
    origin: FindingOrigin
    role: Role | None = None
    severity: FindingSeverity
    category: str | None = None
    file: str | None = None
    line: int | None = Field(default=None, ge=1)
    reviewed_sha: str | None = None
    required_action: str | None = None
    status: FindingStatus
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence_ids: list[str] = []


class GateResult(BaseModel):
    """Result of one gate, evaluated against the final SHA (ADR-019 p.3)."""

    gate: Gate
    status: GateStatus
    sha: str | None = None
    summary: str | None = None
    evidence_ids: list[str] = []


class Decision(BaseModel):
    """Approval/decision recorded in run records (ADR-006 p.2, ADR-018).

    ``commit_sha`` binds the decision to a revision (version-bound approval,
    ADR-009 p.7, FR-011): a decision authorizes exactly the SHA it was
    recorded for — a new head SHA invalidates it. ``None`` means unbound,
    which never authorizes a merge at a specific SHA.
    """

    id: str = Field(min_length=1)
    gate: Gate
    outcome: DecisionOutcome
    decided_by: DecisionSource
    role: Role | None = None
    decided_at: datetime
    commit_sha: str | None = None
    comment: str | None = None
    evidence_ids: list[str] = []
