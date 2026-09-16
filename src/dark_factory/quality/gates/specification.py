"""Deterministic machine checks of the specification gate (T-021, ADR-020).

The gate decides over the five axes of the normalized contract (changeset.md)
as computed by :func:`dark_factory.context.sdd.normalized.normalize`: findings
are data, the gate weighs them. Pure and deterministic — no harness, LLM or
clock; identical inputs yield an identical :class:`GateDecision`.

The risk-class total order and the R2 threshold come from
``dark_factory.changes.risk`` (T-080, ADR-023 p.2): the lowest layer, so
``quality`` neither depends on ``orchestration`` nor keeps a second copy of the
order.
"""

from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from dark_factory.changes.enums import Gate, GateStatus, RiskClass
from dark_factory.changes.implementation_contract import ImplementationContract
from dark_factory.changes.risk import RISK_ORDER, is_r2_or_higher
from dark_factory.context.sdd.models import (
    ChangeManifest,
    DeltaOperationKind,
    RequirementVerification,
)
from dark_factory.context.sdd.normalized import (
    REQ_ID_PREFIX,
    Axis,
    AxisFinding,
    ChangeSet,
    normalize,
)
from dark_factory.context.sdd.strictness import required_artifacts
from dark_factory.quality.gates.decision import (
    SPECIFICATION_GATE_POLICY,
    SPECIFICATION_GATE_POLICY_VERSION,
    GateDecision,
    GateFinding,
)

# Canonical check type of an acceptance criterion (sdd-native-core.md §12).
ACCEPTANCE_SCENARIO_CHECK: Final = "acceptance-scenario"

# Codes the default policy blocks; every other code (including unknown ones)
# is non-blocking. ``risk_class_raised`` is informational and
# ``missing_evidence_for_accepted_change`` belongs to the release gate —
# evidence arrives with acceptance (changeset.md).
DEFAULT_SPEC_GATE_BLOCKING_CODES: Final[frozenset[str]] = frozenset(
    {
        "baseline_revision_mismatch",
        "declared_artifact_missing",
        "duplicate_delta_target",
        "missing_required_artifact",
        "reconciliation_required_for_high_risk",
        "requirement_without_acceptance_scenario",
        "requirement_without_task",
        "requirement_without_verification",
        "required_evidence_unavailable",
        "risk_class_lowered_without_policy",
        "scope_contradiction",
        "self_supersede",
    }
)

# Total order R0 < R1 < R2 < R3 < R4 lives in ``dark_factory.changes.risk``
# (T-080): a single definition, no local copy to drift.


class SpecGatePolicy(BaseModel):
    """Classification policy of the specification gate: which finding codes block (T-021)."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(default=SPECIFICATION_GATE_POLICY, min_length=1)
    version: str = Field(default=SPECIFICATION_GATE_POLICY_VERSION, min_length=1)
    blocking_codes: frozenset[str] = DEFAULT_SPEC_GATE_BLOCKING_CODES


def _missing_artifact_findings(
    manifest: ChangeManifest, risk_class: RiskClass
) -> list[AxisFinding]:
    """Completeness findings for ``risk_class`` (mirrors normalized._completeness_findings)."""
    required = required_artifacts(manifest.workflow.profile, risk_class)
    missing = required - manifest.artifacts.keys()
    return [
        AxisFinding(
            axis=Axis.COMPLETENESS,
            code="missing_required_artifact",
            message=f"required artifact slot {slot.value!r} is not declared in change.yaml",
        )
        for slot in sorted(missing, key=lambda slot: slot.value)
    ]


def _reconciliation_findings(changeset: ChangeSet, risk_class: RiskClass) -> list[AxisFinding]:
    """Policy finding on the R2+ reconciliation requirement, by the effective risk."""
    if is_r2_or_higher(risk_class) and changeset.reconciliation is None:
        return [
            AxisFinding(
                axis=Axis.POLICY,
                code="reconciliation_required_for_high_risk",
                message=f"risk class {risk_class.value} requires a reconciliation plan",
            )
        ]
    return []


def _risk_transition_finding(manifest_risk: RiskClass, effective: RiskClass) -> AxisFinding:
    """Lowering the risk class is a policy decision, raising it is informational (ADR-011 p.5)."""
    if RISK_ORDER[effective] < RISK_ORDER[manifest_risk]:
        return AxisFinding(
            axis=Axis.POLICY,
            code="risk_class_lowered_without_policy",
            message=(
                f"the implementation contract lowers the risk class {manifest_risk.value} -> "
                f"{effective.value}; only the formal policy or a human may lower it"
            ),
        )
    return AxisFinding(
        axis=Axis.POLICY,
        code="risk_class_raised",
        message=(
            f"the implementation contract raises the risk class {manifest_risk.value} -> "
            f"{effective.value}"
        ),
    )


def _scope_contradiction_findings(contract: ImplementationContract) -> list[AxisFinding]:
    """Items present (case- and whitespace-insensitively) in both scope directions."""
    in_scope = {item.strip().casefold() for item in contract.scope.in_scope}
    out_of_scope = {item.strip().casefold() for item in contract.scope.out_of_scope}
    return [
        AxisFinding(
            axis=Axis.CONSISTENCY,
            code="scope_contradiction",
            message=f"scope item {item!r} is approved both in scope and out of scope",
        )
        for item in sorted(in_scope & out_of_scope)
    ]


def _requirement_targets(changeset: ChangeSet) -> list[str]:
    """Requirements the delta touches with add/modify (normalized._coverage_findings)."""
    delta = changeset.delta
    if delta is None:
        return []
    return [
        operation.target
        for operation in delta.operations
        if operation.operation in (DeltaOperationKind.ADD, DeltaOperationKind.MODIFY)
        and operation.target.startswith(REQ_ID_PREFIX)
    ]


def _acceptance_scenario_findings(changeset: ChangeSet) -> list[AxisFinding]:
    """Every touched requirement needs checks, and one of them an acceptance scenario."""
    entries: dict[str, RequirementVerification] = {}
    if changeset.verification is not None:
        entries = {entry.requirement: entry for entry in changeset.verification.requirements}
    findings: list[AxisFinding] = []
    for target in _requirement_targets(changeset):
        entry = entries.get(target)
        if entry is None or not any(
            check.type == ACCEPTANCE_SCENARIO_CHECK for check in entry.checks
        ):
            findings.append(
                AxisFinding(
                    axis=Axis.COMPLETENESS,
                    code="requirement_without_acceptance_scenario",
                    message=(
                        f"requirement {target!r} has no {ACCEPTANCE_SCENARIO_CHECK!r} check "
                        "in the verification plan"
                    ),
                )
            )
    return findings


def _evidence_references(changeset: ChangeSet) -> tuple[str, ...]:
    """Sorted references the decision rests on: declared artifact paths + evidence ids."""
    references = set(changeset.manifest.artifacts.values())
    if changeset.evidence is not None:
        references.update(entry.id for entry in changeset.evidence.evidence)
    return tuple(sorted(references))


def _explanation(findings: tuple[GateFinding, ...]) -> str:
    """Deterministic summary of the decision (T-021)."""
    blocking = [finding for finding in findings if finding.blocking]
    if blocking:
        codes = sorted({finding.code for finding in blocking})
        return f"specification gate failed: {len(blocking)} blocking findings: {', '.join(codes)}"
    if findings:
        return f"specification gate passed: {len(findings)} non-blocking findings"
    return "specification gate passed: no findings"


def evaluate_specification_gate(
    changeset: ChangeSet,
    contract: ImplementationContract | None = None,
    *,
    policy: SpecGatePolicy | None = None,
) -> GateDecision:
    """Decide the specification gate over a ChangeSet (T-021, changeset.md).

    Deterministic: identical inputs yield an identical decision, recorded by
    :class:`GateDecision` with ``decided_by=policy`` — agents and humans enter
    through :func:`apply_override`. ``contract`` supplies the effective risk
    class and the approved scope; ``policy`` supplies the blocking-code set.
    """
    gate_policy = policy if policy is not None else SpecGatePolicy()
    normalized = normalize(changeset)
    manifest = changeset.manifest
    manifest_risk = manifest.risk_class
    effective = contract.risk_class if contract is not None else manifest_risk

    completeness = list(normalized.completeness)
    consistency = list(normalized.consistency)
    policy_axis = list(normalized.policy)
    if contract is not None and effective != manifest_risk:
        # The effective risk class decides the required artifact set and the
        # reconciliation requirement (changeset.md): recompute both axes
        # against it instead of the manifest-only findings of normalize().
        completeness = _missing_artifact_findings(manifest, effective)
        policy_axis = _reconciliation_findings(changeset, effective)
        policy_axis.append(_risk_transition_finding(manifest_risk, effective))
    completeness.extend(_acceptance_scenario_findings(changeset))
    if contract is not None:
        consistency.extend(_scope_contradiction_findings(contract))

    ordered = (
        *completeness,
        *consistency,
        *policy_axis,
        *normalized.coverage,
        *normalized.evidence,
    )
    findings = tuple(
        GateFinding(
            axis=finding.axis,
            code=finding.code,
            message=finding.message,
            blocking=finding.code in gate_policy.blocking_codes,
        )
        for finding in ordered
    )
    result = (
        GateStatus.FAILED if any(finding.blocking for finding in findings) else GateStatus.PASSED
    )
    return GateDecision(
        policy=gate_policy.name,
        policy_version=gate_policy.version,
        gate=Gate.SPECIFICATION,
        changeset_id=manifest.id,
        changeset_revision=manifest.baseline.revision,
        risk_class=effective,
        result=result,
        findings=findings,
        evidence=_evidence_references(changeset),
        explanation=_explanation(findings),
    )
