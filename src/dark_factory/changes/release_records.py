"""Release-evidence models of the versioned contracts (T034, ADR-011 p.6).

``SmokeProbeEvidence`` and ``ReleaseEvidence`` are the persisted release
sections of the versioned contracts: ``RunRecord.release`` (T034) and the
additive ``StageResult.release`` (T-092 S4). They live in this leaf module —
it imports only ``enums`` — so both contract owners (``run.py`` and
``run_records.py``) can reference the models without an import cycle;
``run_records`` re-exports them, keeping the T034 import path valid.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from dark_factory.changes.enums import ReleaseStatus


class SmokeProbeEvidence(BaseModel):
    """Result of one smoke probe inside the release evidence (T034, FR-013).

    ``detail`` is a short diagnostic (HTTP status, exception class name); it
    never carries the probe URL, response bodies or raw exception text
    (ADR-009).
    """

    name: str = Field(min_length=1)
    passed: bool
    detail: str | None = None


class ReleaseEvidence(BaseModel):
    """Release section of a run record: digest, Argo status, smoke, decision (T034, ADR-011 p.6).

    Added as an optional section (``RunRecord.release`` in T034, then
    ``StageResult.release`` in T-092 S4): records written before each
    extension parse unchanged (the field defaults to ``None``) and older
    readers ignore the new key, so ``schema_version`` stays ``1`` — the
    extension is additive and optional. ``argo_sync_status``/
    ``argo_health_status`` store the raw observed values verbatim; the
    canonical statuses live in ``quality.release`` and only feed the decision.
    """

    verified_at: datetime
    decision: ReleaseStatus
    reason: str | None = None
    expected_digest: str | None = None
    observed_digest: str | None = None
    argo_sync_status: str | None = None
    argo_health_status: str | None = None
    application: str | None = None
    smoke: tuple[SmokeProbeEvidence, ...] = ()
    rollback_signal: str | None = None
