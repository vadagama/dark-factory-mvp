"""Escalation violations of autonomous implementation (T-016, ADR-018 p.5)."""

from pydantic import BaseModel, ConfigDict

from dark_factory.changes.enums import EscalationRule


class EscalationViolation(BaseModel):
    """One triggered escalation condition with human-readable diagnostics.

    Produced by the deterministic checks of
    ``dark_factory.orchestration.policy`` and transported to the flow on
    ``StageResult.escalations``: a declared violation vetoes autonomous
    continuation and stops the flow in ``Blocked``.

    ``manual_assessment`` is deprecated and always ``False``: after T-080 no
    escalation condition is a manual assessment (ADR-023 p.5). The field stays
    in the serialized contract — removing it is a breaking change that needs a
    ``SchemaVersion`` bump (ADR-015 p.3) — and is dropped in the next record
    schema revision.
    """

    model_config = ConfigDict(frozen=True)

    rule: EscalationRule
    reason: str
    manual_assessment: bool = False
