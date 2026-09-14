"""Escalation violations of autonomous implementation (T-016, ADR-018 p.5)."""

from pydantic import BaseModel, ConfigDict

from dark_factory.changes.enums import EscalationRule


class EscalationViolation(BaseModel):
    """One triggered escalation condition with human-readable diagnostics.

    Produced by the deterministic checks of
    ``dark_factory.orchestration.policy`` and transported to the flow on
    ``StageResult.escalations``: a declared violation vetoes autonomous
    continuation and stops the flow in ``Blocked``. ``manual_assessment``
    marks conditions the machine cannot fully verify yet — the raise of the
    risk class to R2+ stays manual until T-080 (ADR-018 p.5).
    """

    model_config = ConfigDict(frozen=True)

    rule: EscalationRule
    reason: str
    manual_assessment: bool = False
