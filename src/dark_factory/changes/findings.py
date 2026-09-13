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
    """Structured review finding: id, origin, severity, file/line, reviewed SHA, action, status."""

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
    evidence_ids: list[str] = []


class GateResult(BaseModel):
    """Result of one gate, evaluated against the final SHA (ADR-019 p.3)."""

    gate: Gate
    status: GateStatus
    sha: str | None = None
    summary: str | None = None
    evidence_ids: list[str] = []


class Decision(BaseModel):
    """Approval/decision recorded in run records (ADR-006 p.2, ADR-018)."""

    id: str = Field(min_length=1)
    gate: Gate
    outcome: DecisionOutcome
    decided_by: DecisionSource
    role: Role | None = None
    decided_at: datetime
    comment: str | None = None
    evidence_ids: list[str] = []
