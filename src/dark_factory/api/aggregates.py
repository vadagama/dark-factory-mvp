"""Read-side aggregation over stage results for the API (T035, contract api.md).

Stage results are immutable per attempt; read models apply the "latest wins"
rule deterministically: results are consumed in the order produced by
``StageResultRepository.list_for_run`` (produced_at, stage, attempt_number), so
a later attempt supersedes earlier gates, findings and evidence with the same
id — mirroring the gate semantics of ``rules.gates`` (ADR-009 p.7) — and the
trace chain follows the canonical stage order (FR-004..FR-008, SC-007).
"""

from collections.abc import Sequence
from typing import Final

from sqlalchemy.orm import Session

from dark_factory.api.dto import RunTrace, TraceStage
from dark_factory.changes.enums import (
    FindingSeverity,
    FindingStatus,
    Gate,
    Stage,
)
from dark_factory.changes.findings import Finding, GateResult
from dark_factory.changes.refs import Evidence
from dark_factory.changes.run import StageResult
from dark_factory.orchestration.state.models import Execution
from dark_factory.orchestration.state.stage_results import StageResultRepository

CANONICAL_STAGE_ORDER: Final[tuple[Stage, ...]] = (
    Stage.SPECIFICATION,
    Stage.PLANNING,
    Stage.CONSTRUCTION,
    Stage.REVIEW_VERIFICATION,
    Stage.RELEASE,
)


def latest_gate_results(results: Sequence[StageResult]) -> list[GateResult]:
    """The last GateResult per gate, in stable gate order (later attempts win)."""
    latest: dict[Gate, GateResult] = {}
    for result in results:
        for gate_result in result.gate_results:
            latest[gate_result.gate] = gate_result
    return [latest[gate] for gate in sorted(latest, key=lambda gate: gate.value)]


def findings_by_id(results: Sequence[StageResult]) -> dict[str, Finding]:
    """Findings deduplicated by id (later attempts win), insertion-ordered."""
    findings: dict[str, Finding] = {}
    for result in results:
        for finding in result.findings:
            findings[finding.id] = finding
    return findings


def evidence_by_id(results: Sequence[StageResult]) -> dict[str, Evidence]:
    """Evidence deduplicated by id (later attempts win), insertion-ordered."""
    evidence: dict[str, Evidence] = {}
    for result in results:
        for item in result.evidence:
            evidence[item.id] = item
    return evidence


def open_blocker_count(results: Sequence[StageResult]) -> int:
    """Number of distinct open blocker findings across the run (ADR-009 p.9)."""
    return sum(
        1
        for finding in findings_by_id(results).values()
        if finding.severity is FindingSeverity.BLOCKER and finding.status is FindingStatus.OPEN
    )


def trace_chain(results: Sequence[StageResult]) -> list[TraceStage]:
    """The SC-007 chain in canonical stage order; later attempts win per stage."""
    latest: dict[Stage, TraceStage] = {}
    for result in results:
        latest[result.stage] = TraceStage(
            stage=result.stage.value,
            status=result.status.value,
            input_revision=result.input_revision,
            attempt_number=result.attempt_number,
            produced_at=result.produced_at,
            artifacts=list(result.artifacts),
        )
    return [latest[stage] for stage in CANONICAL_STAGE_ORDER if stage in latest]


def build_run_trace(session: Session, execution: Execution) -> RunTrace:
    """The SC-007 trace of one run from its durable stage results."""
    results = StageResultRepository(session).list_for_run(execution.id)
    return RunTrace(
        change_id=execution.change_id,
        run_id=execution.id,
        status=execution.status,
        chain=trace_chain(results),
    )
