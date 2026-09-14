"""GateDecision record of a quality gate (ADR-020, sdd-native-core.md §12).

An artifact is an input, not proof of a passed gate (changeset.md): the gate
computes a result over the normalized ChangeSet and records a
:class:`GateDecision` — the policy and its version, the verified ChangeSet
revision, the result, evidence, the explanation, the decider and an optional
override. No timestamps: Git owns history (``context.sdd.models``).
"""

from pathlib import Path
from typing import Final, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from dark_factory.changes.enums import DecisionSource, Gate, GateStatus, RiskClass
from dark_factory.context.sdd.normalized import Axis

GATE_DECISION_SCHEMA: Final = "dark-factory.dev/gate-decision/v1"

# Literal contract type of GATE_DECISION_SCHEMA; keep the pair in sync.
type GateDecisionSchema = Literal["dark-factory.dev/gate-decision/v1"]

SPECIFICATION_GATE_POLICY: Final = "specification-gate"
SPECIFICATION_GATE_POLICY_VERSION: Final = "1.0"

GATES_DIR: Final = "gates"
SPECIFICATION_GATE_ARTIFACT: Final = "specification.yaml"

# Accept both python and wire names on input, always emit the wire name.
_SCHEMA_MODEL_CONFIG: Final = ConfigDict(
    frozen=True, populate_by_name=True, serialize_by_alias=True
)

# A recorded decision is final: pending/skipped are run states, not decisions.
_DECIDED_RESULTS: Final[frozenset[GateStatus]] = frozenset({GateStatus.PASSED, GateStatus.FAILED})


class GateFinding(BaseModel):
    """One weighed finding of a gate evaluation: contract finding plus blocking class."""

    model_config = ConfigDict(frozen=True)

    axis: Axis
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    blocking: bool


class GateDecisionOverride(BaseModel):
    """Human override of a failed gate decision (§12: override, if allowed)."""

    model_config = _SCHEMA_MODEL_CONFIG

    decided_by: DecisionSource
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def _human_only(self) -> Self:
        if self.decided_by is not DecisionSource.HUMAN:
            raise ValueError(
                f"only a human may override a gate decision, got {self.decided_by.value!r}"
            )
        return self


class GateDecision(BaseModel):
    """Recorded decision of one gate (sdd-native-core.md §12, changeset.md).

    ``result`` is only ``passed`` or ``failed``: the decision is final, the
    run states (``pending``, ``skipped``) never enter the record. ``evidence``
    holds the sorted references the decision rests on.
    """

    model_config = _SCHEMA_MODEL_CONFIG

    schema_: GateDecisionSchema = Field(GATE_DECISION_SCHEMA, alias="schema")
    policy: str = Field(default=SPECIFICATION_GATE_POLICY, min_length=1)
    policy_version: str = Field(default=SPECIFICATION_GATE_POLICY_VERSION, min_length=1)
    gate: Gate = Gate.SPECIFICATION
    changeset_id: str = Field(min_length=1)
    changeset_revision: str = Field(min_length=1)
    risk_class: RiskClass
    result: GateStatus
    findings: tuple[GateFinding, ...] = ()
    evidence: tuple[str, ...] = ()
    explanation: str = Field(min_length=1)
    decided_by: DecisionSource = DecisionSource.POLICY
    override: GateDecisionOverride | None = None

    @model_validator(mode="after")
    def _decided_result(self) -> Self:
        if self.result not in _DECIDED_RESULTS:
            raise ValueError(f"a gate decision records passed or failed, got {self.result.value!r}")
        return self


class GateOverrideError(ValueError):
    """An override outside the allowed policy was requested."""


def apply_override(
    decision: GateDecision, *, decided_by: DecisionSource, reason: str
) -> GateDecision:
    """Record a human override of a failed decision as a new :class:`GateDecision` (§12).

    Only a human may override, only a failed decision may be overridden and the
    reason must be non-empty (ADR-011/018: agents never waive gates). The
    result itself stays ``failed`` — the override is recorded, not reverted.
    """
    if decided_by is not DecisionSource.HUMAN:
        raise GateOverrideError(
            f"only a human may override a gate decision, got {decided_by.value!r} (ADR-018)"
        )
    if decision.result is not GateStatus.FAILED:
        raise GateOverrideError(
            f"a {decision.result.value} gate decision is not overridden: "
            "overrides apply to failed decisions only"
        )
    if not reason.strip():
        raise GateOverrideError("a gate override requires a non-empty reason")
    return decision.model_copy(
        update={"override": GateDecisionOverride(decided_by=decided_by, reason=reason)}
    )


def write_gate_decision(change_dir: Path, decision: GateDecision) -> None:
    """Persist ``gates/specification.yaml`` of a ChangeSet (§12, changeset.md)."""
    target = change_dir / GATES_DIR
    target.mkdir(parents=True, exist_ok=True)
    payload = decision.model_dump(mode="json", exclude_none=True)
    (target / SPECIFICATION_GATE_ARTIFACT).write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
