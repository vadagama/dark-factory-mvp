"""Pure gates builder of the wait-resolution protocol (T-092 S3, ADR-006 p.8).

A stage parked in ``waiting`` — the durable external-wait checkpoint of
ADR-006 p.8 — is resumed by the driver
(:func:`~dark_factory.orchestration.runner.advance_run`) once the facts it
waits for resolve. This module turns the observed facts
(:class:`GateObservation`: pipeline verdict, merge state, version-bound human
approvals) into the :class:`StageResult` of that same attempt: the stage's
machine gates are mapped onto the observed pipeline outcome, an observed merge
completes the review stage through the merge policy (ADR-011 p.2), and a
failing pipeline feeds the bounded rework loop (T-014).

Deterministic and pure — no I/O, no provider calls, no clock beyond the
explicit ``now`` parameter (the same discipline as the neighboring stage
checks). FR-009 rules every mapping: a gate is ``passed`` only on an observed
pipeline success bound to the observed head SHA, never fabricated — an
observation that cannot be attributed to a SHA cannot honestly complete the
stage, and the attempt ends ``blocked`` instead.

The resolution result belongs to the waiting attempt: same stage, same attempt
number, same pinned input revision — the builder receives them and never
invents an identity. What the *flow* then decides with the result (advance,
merge, rework, stop, or a further wait) is its business: the builder returns a
succeeded merge result even when the policy will park the run for the human
merge — the flow owns that transition (ADR-011 p.2), and synthesizing a wait
here would duplicate it.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from dark_factory.changes.enums import (
    ChangeRequestStatus,
    FindingOrigin,
    FindingSeverity,
    FindingStatus,
    Gate,
    GateStatus,
    Route,
    Stage,
    StageStatus,
    StopOutcome,
)
from dark_factory.changes.findings import Decision, Finding, GateResult
from dark_factory.changes.next_action import (
    ExecuteStageAction,
    MergeAction,
    ReworkAction,
    StopAction,
    WaitForCIAction,
)
from dark_factory.changes.refs import ChangeRequestRef
from dark_factory.changes.run import Change, ChangeRun, StageResult
from dark_factory.flows.routes import route_profile
from dark_factory.orchestration.policy.merge import MergeRequestContext
from dark_factory.orchestration.policy.risk import effective_change_risk_class
from dark_factory.orchestration.rework import ReviewPass, finding_signature, plan_rework
from dark_factory.rules.gates import HUMAN_GATES, required_gates, unsatisfied_gates

_PIPELINE_SUCCESS: Final[str] = "success"
"""The pipeline verdict a gate passes on (mirrors the provider ports' values)."""

_PIPELINE_FAILURES: Final[frozenset[str]] = frozenset({"failure", "canceled"})
"""The pipeline verdicts that fail every machine gate of the stage."""


@dataclass(frozen=True, slots=True)
class GateObservation:
    """Provider facts observed for one waiting stage attempt (T-092 S3).

    A value-level observation — the driver stays port-free (ADR-024 p.5), the
    composition root derives it from ``CIPort``/``MergeRequestPort`` in slice
    S3. ``pipeline_status`` is one of ``queued``, ``in_progress``, ``success``,
    ``failure``, ``canceled`` (the provider ports' pipeline values, spelled out
    here rather than imported) or ``None`` when no pipeline ran.
    ``approvals`` are the human decisions observed bound to a SHA; the merge
    policy weighs them (version-bound approval, ADR-009 p.7), this module only
    carries them.
    """

    head_sha: str | None
    merged: bool
    pipeline_status: str | None
    approvals: Sequence[Decision] = ()


@dataclass(frozen=True, slots=True)
class GateResolution:
    """The resolution result of a waiting attempt, with its optional merge facts.

    ``result`` is the :class:`StageResult` of the waiting attempt — same stage,
    same attempt number, same pinned input revision.
    ``merge_context`` is set only for a merge-carrying result (the review
    stage): the flow anchors route, stage, the effective risk class and the
    gate results itself; the context contributes the observed SHAs and the
    observed approvals (T-026).
    """

    result: StageResult
    merge_context: MergeRequestContext | None


def gate_resolved(observation: GateObservation | None, *, stage: Stage) -> bool:
    """Whether the observation resolves the external wait of ``stage`` (T-092 S3).

    Construction-style stages resolve on a terminal pipeline verdict; the
    review/verification stage resolves only on an observed merge — it
    completes through the merge (``flow.FLOW_TRANSITIONS``), so a green
    pipeline there is not a resolution: waking the attempt would build a merge
    result the policy can only park again. ``None`` (nothing observed) is
    never a resolution.
    """
    if observation is None:
        return False
    # Release resolves only through release facts, never pipeline observations.
    if stage is Stage.RELEASE:
        return False
    if observation.merged:
        return stage is Stage.REVIEW_VERIFICATION
    if observation.pipeline_status == _PIPELINE_SUCCESS:
        return stage is not Stage.REVIEW_VERIFICATION
    return observation.pipeline_status in _PIPELINE_FAILURES


def build_gate_resolution(
    *,
    run: ChangeRun,
    stage: Stage,
    change: Change,
    checkpoint: StageResult,
    observation: GateObservation,
    input_revision: str,
    attempt_number: int,
    history: Sequence[StageResult] = (),
    now: datetime,
) -> GateResolution:
    """Turn a resolved observation into the final result of the waiting attempt.

    The result carries the waiting attempt's identity verbatim: ``stage``,
    ``attempt_number`` and ``input_revision`` as given, so the durable store
    supersedes the waiting checkpoint of the same attempt (ADR-006 p.8) and
    the flow matches the result to the parked stage run.

    On a construction-style stage the machine gates (``rules.gates`` minus the
    base human set) are mapped onto the pipeline verdict: a pass at the head
    SHA advances to the next stage of the route, a failure is a bounded rework
    round (the flow enforces the limit, FR-008), and a success that cannot be
    attributed to a SHA blocks honestly (FR-009). On the review stage an
    observed merge builds the merge result with its merge context (ADR-011
    p.2) — the flow then advances or parks for the human merge per policy —
    while a failing pipeline runs the rework-loop policy (T-014) over the
    review passes of the run's earlier review attempts.

    Raises ``ValueError`` when the observation does not resolve the wait of
    ``stage`` (the driver replays the checkpoint instead) and when a merged
    observation names no change request (neither the checkpoint's
    ``wait_for_ci`` action nor the change snapshot carries one).
    """
    if not gate_resolved(observation, stage=stage):
        raise ValueError(
            f"observation of stage {stage.value} does not resolve its external wait; "
            "the driver replays the waiting checkpoint instead"
        )
    if stage is Stage.REVIEW_VERIFICATION:
        return _resolve_review(
            run=run,
            change=change,
            checkpoint=checkpoint,
            observation=observation,
            input_revision=input_revision,
            attempt_number=attempt_number,
            history=history,
            now=now,
        )
    return _resolve_machine_gated(
        run=run,
        stage=stage,
        observation=observation,
        input_revision=input_revision,
        attempt_number=attempt_number,
        now=now,
    )


def _resolve_machine_gated(
    *,
    run: ChangeRun,
    stage: Stage,
    observation: GateObservation,
    input_revision: str,
    attempt_number: int,
    now: datetime,
) -> GateResolution:
    """Resolve a stage whose completion is decided by the pipeline verdict alone."""
    machine_results = _machine_gate_results(run.route, stage, observation)
    unsatisfied = unsatisfied_gates(run.route, stage, machine_results)
    if not unsatisfied:
        successor = route_profile(run.route).next_stage(stage)
        if successor is None:  # pragma: no cover - construction always has a successor
            raise ValueError(f"stage {stage.value} on route {run.route.value} has no successor")
        result = StageResult(
            stage=stage,
            run_id=run.id,
            change_id=run.change_id,
            attempt_number=attempt_number,
            input_revision=input_revision,
            status=StageStatus.SUCCEEDED,
            next_action=ExecuteStageAction(
                next_stage=successor,
                reason=f"pipeline {_PIPELINE_SUCCESS} at {observation.head_sha}",
            ),
            gate_results=machine_results,
            produced_at=now,
        )
        return GateResolution(result=result, merge_context=None)
    failed = _failed_gates(machine_results)
    if failed:
        # The pipeline produced a verdict, but not the passing one: the failed
        # machine gates are a rework round for the implementation, bounded by
        # the flow's rework limit (FR-008); the round itself is declared here
        # and spent by the flow.
        budget = run.budget
        result = StageResult(
            stage=stage,
            run_id=run.id,
            change_id=run.change_id,
            attempt_number=attempt_number,
            input_revision=input_revision,
            status=StageStatus.FAILED,
            next_action=ReworkAction(
                round=budget.used_rework_rounds + 1,
                max_rounds=budget.max_rework_rounds,
                reason=_pipeline_failure_reason(observation, failed),
            ),
            gate_results=machine_results,
            findings=_pipeline_findings(failed, observation),
            produced_at=now,
        )
        return GateResolution(result=result, merge_context=None)
    # A success-shaped observation with no evaluable head SHA (FR-009): no gate
    # result may be fabricated, so the attempt blocks honestly instead.
    result = StageResult(
        stage=stage,
        run_id=run.id,
        change_id=run.change_id,
        attempt_number=attempt_number,
        input_revision=input_revision,
        status=StageStatus.BLOCKED,
        next_action=StopAction(
            outcome=StopOutcome.BLOCKED,
            reason=(
                "pipeline success cannot be attributed to a head SHA; a gate may not be "
                "passed without an observed success at the final SHA (FR-009)"
            ),
        ),
        gate_results=machine_results,
        produced_at=now,
    )
    return GateResolution(result=result, merge_context=None)


def _resolve_review(
    *,
    run: ChangeRun,
    change: Change,
    checkpoint: StageResult,
    observation: GateObservation,
    input_revision: str,
    attempt_number: int,
    history: Sequence[StageResult],
    now: datetime,
) -> GateResolution:
    """Resolve the review stage: merge (ADR-011 p.2) or the bounded rework loop."""
    if observation.merged:
        # The observed human merge outranks a pipeline verdict: it is the
        # review pass (ADR-011 p.2); the pipeline still feeds verification.
        ref = _change_request_ref(change, checkpoint, observation)
        gate_results: list[GateResult] = []
        if observation.head_sha is not None:
            gate_results.append(
                GateResult(
                    gate=Gate.REVIEW,
                    status=GateStatus.PASSED,
                    sha=observation.head_sha,
                    summary=f"change request #{ref.number} merged",
                )
            )
        gate_results.extend(
            _machine_gate_results(run.route, Stage.REVIEW_VERIFICATION, observation)
        )
        result = StageResult(
            stage=Stage.REVIEW_VERIFICATION,
            run_id=run.id,
            change_id=run.change_id,
            attempt_number=attempt_number,
            input_revision=input_revision,
            status=StageStatus.SUCCEEDED,
            next_action=MergeAction(
                change_request=ref,
                reason=f"change request #{ref.number} observed merged at {observation.head_sha}",
            ),
            gate_results=gate_results,
            produced_at=now,
        )
        return GateResolution(
            result=result,
            merge_context=MergeRequestContext(
                executor="human",
                route=run.route,
                stage=Stage.REVIEW_VERIFICATION,
                risk_class=effective_change_risk_class(run.implementation_contract, run.route),
                expected_sha=observation.head_sha,
                head_sha=observation.head_sha,
                human_approvals=observation.approvals,
            ),
        )
    machine_results = _machine_gate_results(run.route, Stage.REVIEW_VERIFICATION, observation)
    failed = _failed_gates(machine_results)
    findings = _pipeline_findings(failed, observation)
    prior = _review_passes(history)
    passes = [
        *prior,
        ReviewPass(
            round=len(prior) + 1,
            sha=observation.head_sha or input_revision,
            blocking=tuple(finding_signature(finding) for finding in findings),
        ),
    ]
    decision = plan_rework(budget=run.budget, passes=passes)
    if decision.outcome == "blocked":
        result = StageResult(
            stage=Stage.REVIEW_VERIFICATION,
            run_id=run.id,
            change_id=run.change_id,
            attempt_number=attempt_number,
            input_revision=input_revision,
            status=StageStatus.BLOCKED,
            next_action=StopAction(outcome=StopOutcome.BLOCKED, reason=decision.reason),
            gate_results=machine_results,
            findings=findings,
            produced_at=now,
        )
    else:
        # ``plan_rework`` returns a round exactly for the "rework" outcome.
        next_round = (
            decision.round if decision.round is not None else run.budget.used_rework_rounds + 1
        )
        result = StageResult(
            stage=Stage.REVIEW_VERIFICATION,
            run_id=run.id,
            change_id=run.change_id,
            attempt_number=attempt_number,
            input_revision=input_revision,
            status=StageStatus.FAILED,
            next_action=ReworkAction(
                round=next_round,
                max_rounds=decision.max_rounds,
                reason=decision.reason,
            ),
            gate_results=machine_results,
            findings=findings,
            produced_at=now,
        )
    return GateResolution(result=result, merge_context=None)


def _machine_gate_results(
    route: Route, stage: Stage, observation: GateObservation
) -> list[GateResult]:
    """The stage's machine gates mapped onto the observed pipeline verdict.

    Machine gates are the required set minus the base human gates
    (``rules.gates``): a green pipeline at the head SHA passes them bound to
    that SHA, a failed or canceled pipeline fails them (bound to the SHA when
    one was observed), and anything else — including a success without a SHA —
    produces no result at all, so no gate is satisfied on missing facts
    (FR-009).
    """
    required = required_gates(route, stage) - HUMAN_GATES
    if not required:
        return []
    if observation.pipeline_status == _PIPELINE_SUCCESS and observation.head_sha is not None:
        return [
            GateResult(
                gate=gate,
                status=GateStatus.PASSED,
                sha=observation.head_sha,
                summary=f"pipeline {_PIPELINE_SUCCESS} at {observation.head_sha}",
            )
            for gate in sorted(required, key=lambda item: item.value)
        ]
    if observation.pipeline_status in _PIPELINE_FAILURES:
        return [
            GateResult(
                gate=gate,
                status=GateStatus.FAILED,
                sha=observation.head_sha,
                summary=f"pipeline {observation.pipeline_status} at "
                f"{observation.head_sha or 'an unknown SHA'}",
            )
            for gate in sorted(required, key=lambda item: item.value)
        ]
    return []


def _failed_gates(machine_results: Sequence[GateResult]) -> list[Gate]:
    """Machine gates the pipeline failed, in stable gate order."""
    return sorted(
        (item.gate for item in machine_results if item.status is GateStatus.FAILED),
        key=lambda gate: gate.value,
    )


def _pipeline_findings(failed: Sequence[Gate], observation: GateObservation) -> list[Finding]:
    """Open blocker findings for the machine gates the pipeline failed.

    Deterministic ids (``ci-pipeline:<gate>``) so a repeated failure of the
    same gate deduplicates in the run view instead of inflating the blocker
    count; ``origin`` is ``ci`` — the verdict of a machine check, not of an
    agent or a human.
    """
    return [
        Finding(
            id=f"ci-pipeline:{gate.value}",
            origin=FindingOrigin.CI,
            severity=FindingSeverity.BLOCKER,
            category=gate.value,
            reviewed_sha=observation.head_sha,
            required_action=(
                f"gate {gate.value!r} failed: pipeline {observation.pipeline_status} at "
                f"{observation.head_sha or 'an unknown SHA'}; make the gate pass and re-run CI"
            ),
            status=FindingStatus.OPEN,
        )
        for gate in failed
    ]


def _pipeline_failure_reason(observation: GateObservation, failed: Sequence[Gate]) -> str:
    """Human-readable rework reason naming the failed gates and the verdict."""
    names = ", ".join(gate.value for gate in failed)
    return (
        f"pipeline {observation.pipeline_status} at {observation.head_sha or 'an unknown SHA'}: "
        f"machine gate(s) {names} failed"
    )


def _review_passes(history: Sequence[StageResult]) -> list[ReviewPass]:
    """Completed review passes of the run's earlier review attempts (T-014).

    The append-only pass history is derived from the committed results: every
    earlier *failed* review attempt of the run is one pass — its round is its
    position in the produced order, its blocking set the signatures of its
    blocker findings, its SHA the revision it was evaluated at. Results
    without a revision are skipped (``ReviewPass`` requires one); escalations
    ride along as a stop condition of their own.
    """
    passes: list[ReviewPass] = []
    for result in history:
        if result.stage is not Stage.REVIEW_VERIFICATION:
            continue
        if result.status is not StageStatus.FAILED or not result.input_revision:
            continue
        passes.append(
            ReviewPass(
                round=len(passes) + 1,
                sha=result.input_revision,
                blocking=tuple(
                    finding_signature(finding)
                    for finding in result.findings
                    if finding.severity is FindingSeverity.BLOCKER
                ),
                escalations=list(result.escalations),
            )
        )
    return passes


def _change_request_ref(
    change: Change, checkpoint: StageResult, observation: GateObservation
) -> ChangeRequestRef:
    """The change request the merge result carries (merge, ADR-011).

    The waiting checkpoint's ``wait_for_ci`` action names the request it waits
    on; the change snapshot's own reference is the fallback. An observed merge
    is reflected in the status: the flow and the merge policy must not read
    the stale ``open``/``draft`` of the checkpoint.
    """
    action = checkpoint.next_action
    ref = action.change_request if isinstance(action, WaitForCIAction) else None
    if ref is None:
        ref = change.change_request
    if ref is None:
        raise ValueError(
            "the merged observation names no change request: neither the waiting "
            "checkpoint nor the change snapshot carries one (merge, ADR-011)"
        )
    if observation.merged and ref.status is not ChangeRequestStatus.MERGED:
        return ref.model_copy(update={"status": ChangeRequestStatus.MERGED})
    return ref
