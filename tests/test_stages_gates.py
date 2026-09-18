"""Pure gates builder of the wait-resolution protocol (T-092 S3, ADR-006 p.8)."""

from collections.abc import Sequence

import pytest

from dark_factory.changes.enums import (
    ChangeRequestStatus,
    FindingOrigin,
    FindingSeverity,
    FindingStatus,
    Gate,
    GateStatus,
    RiskClass,
    Route,
    Stage,
    StageStatus,
    StopOutcome,
)
from dark_factory.changes.findings import Finding, GateResult
from dark_factory.changes.next_action import (
    ExecuteStageAction,
    MergeAction,
    ReworkAction,
    StopAction,
    WaitForCIAction,
)
from dark_factory.changes.run import Change, ChangeRun, StageResult
from dark_factory.changes.usage import BudgetSnapshot
from dark_factory.flows.routes import route_profile
from dark_factory.orchestration.flow import expected_result_status
from dark_factory.orchestration.policy.merge import MergeRequestContext
from dark_factory.orchestration.policy.risk import effective_change_risk_class
from dark_factory.orchestration.stages.gates import (
    GateObservation,
    GateResolution,
    build_gate_resolution,
    gate_resolved,
)
from tests.changes_factories import (
    NOW,
    make_change,
    make_change_request,
    make_contract,
    make_merge_approval,
    make_run,
)

HEAD = "9f2c7ab1"
REVISION = "a1b2c3d"


def _checkpoint(stage: Stage, run: ChangeRun, change: Change) -> StageResult:
    """The waiting checkpoint the resolution supersedes; its wait names the CR."""
    return StageResult(
        stage=stage,
        run_id=run.id,
        change_id=change.id,
        attempt_number=1,
        input_revision=REVISION,
        status=StageStatus.WAITING,
        next_action=WaitForCIAction(
            reason="waiting for the pipeline",
            change_request=make_change_request() if stage is Stage.REVIEW_VERIFICATION else None,
        ),
        produced_at=NOW,
    )


def _build(
    stage: Stage,
    observation: GateObservation,
    *,
    run: ChangeRun | None = None,
    change: Change | None = None,
    history: Sequence[StageResult] = (),
    attempt_number: int = 1,
) -> GateResolution:
    resolved_run = run if run is not None else make_run()
    resolved_change = change if change is not None else make_change()
    return build_gate_resolution(
        run=resolved_run,
        stage=stage,
        change=resolved_change,
        checkpoint=_checkpoint(stage, resolved_run, resolved_change),
        observation=observation,
        input_revision=REVISION,
        attempt_number=attempt_number,
        history=history,
        now=NOW,
    )


def test_nothing_observed_never_resolves_the_wait() -> None:
    for stage in (Stage.CONSTRUCTION, Stage.REVIEW_VERIFICATION):
        assert (
            gate_resolved(None, stage=stage, route=Route.STANDARD, risk_class=RiskClass.R1) is False
        )


@pytest.mark.parametrize(
    ("pipeline_status", "merged", "construction", "review"),
    [
        ("queued", False, False, False),
        ("in_progress", False, False, False),
        (None, False, False, False),
        ("success", False, True, False),
        ("failure", False, True, True),
        ("canceled", False, True, True),
        ("success", True, False, True),
        (None, True, False, True),
    ],
)
def test_gate_resolved_truth_table(
    pipeline_status: str | None, merged: bool, construction: bool, review: bool
) -> None:
    observation = GateObservation(head_sha=HEAD, merged=merged, pipeline_status=pipeline_status)

    # A green pipeline completes a machine-gated stage; the review stage
    # completes only through the merge (flow.FLOW_TRANSITIONS).
    assert (
        gate_resolved(
            observation, stage=Stage.CONSTRUCTION, route=Route.STANDARD, risk_class=RiskClass.R1
        )
        is construction
    )
    assert (
        gate_resolved(
            observation,
            stage=Stage.REVIEW_VERIFICATION,
            route=Route.STANDARD,
            risk_class=RiskClass.R1,
        )
        is review
    )


@pytest.mark.parametrize(
    ("pipeline_status", "merged"),
    [
        ("success", False),
        ("failure", False),
        (None, True),
    ],
)
def test_the_release_wait_never_resolves_from_pipeline_observations(
    pipeline_status: str | None, merged: bool
) -> None:
    # Release resolves only through release facts, never pipeline observations.
    observation = GateObservation(head_sha=HEAD, merged=merged, pipeline_status=pipeline_status)

    assert (
        gate_resolved(
            observation, stage=Stage.RELEASE, route=Route.STANDARD, risk_class=RiskClass.R1
        )
        is False
    )


def test_a_purely_human_gated_stage_never_resolves_on_the_pipeline() -> None:
    # Specification requires ``specification`` and (non-quick) ``ui`` — both
    # human: a green pipeline must not complete the human decision for the flow
    # (T-043 increment 1 finding, ADR-028 p.2).
    observation = GateObservation(head_sha=HEAD, merged=False, pipeline_status="success")

    assert (
        gate_resolved(
            observation, stage=Stage.SPECIFICATION, route=Route.STANDARD, risk_class=RiskClass.R1
        )
        is False
    )


def test_a_risk_widened_human_gate_is_never_resolved_by_the_pipeline() -> None:
    """A R2 ``planning`` gate is human, so a green pipeline parks the stage (ADR-028 p.2).

    This is the leak the pilot found: the pipeline verdict satisfied a
    risk-widened human gate instead of waiting for the human decision.
    """
    run = make_run()
    run.implementation_contract = make_contract().model_copy(update={"risk_class": RiskClass.R2})
    observation = GateObservation(head_sha=HEAD, merged=False, pipeline_status="success")

    assert (
        gate_resolved(
            observation, stage=Stage.PLANNING, route=Route.STANDARD, risk_class=RiskClass.R2
        )
        is False
    )
    approval = make_merge_approval(sha=HEAD).model_copy(update={"gate": Gate.PLANNING})
    approved = GateObservation(
        head_sha=HEAD, merged=False, pipeline_status="success", approvals=(approval,)
    )
    assert (
        gate_resolved(approved, stage=Stage.PLANNING, route=Route.STANDARD, risk_class=RiskClass.R2)
        is True
    )
    # The same observation on R1 leaves ``planning`` a machine gate: it resolves
    # on the pipeline, exactly as before ADR-028.
    assert (
        gate_resolved(
            observation, stage=Stage.PLANNING, route=Route.STANDARD, risk_class=RiskClass.R1
        )
        is True
    )


def test_an_observed_human_approval_resolves_the_human_gated_stage() -> None:
    approval = make_merge_approval(sha=HEAD).model_copy(update={"gate": Gate.SPECIFICATION})
    observation = GateObservation(
        head_sha=HEAD,
        merged=False,
        pipeline_status="success",
        approvals=(approval,),
    )

    assert (
        gate_resolved(
            observation, stage=Stage.SPECIFICATION, route=Route.STANDARD, risk_class=RiskClass.R1
        )
        is True
    )


def test_an_observed_merge_resolves_the_human_gated_stage() -> None:
    """The merge of the stage's change request is the human decision on the stage."""
    observation = GateObservation(head_sha=HEAD, merged=True, pipeline_status="success")

    assert (
        gate_resolved(
            observation, stage=Stage.SPECIFICATION, route=Route.STANDARD, risk_class=RiskClass.R1
        )
        is True
    )


def test_human_gated_resolution_builds_the_success_result_from_an_approval() -> None:
    """The resolved human gate passes version-bound to the approved SHA (ADR-009 p.7)."""
    approval = make_merge_approval(sha=HEAD).model_copy(update={"gate": Gate.SPECIFICATION})
    resolution = _build(
        Stage.SPECIFICATION,
        GateObservation(
            head_sha=HEAD, merged=False, pipeline_status="success", approvals=(approval,)
        ),
    )

    result = resolution.result
    assert result.status is StageStatus.SUCCEEDED
    assert result.status is expected_result_status(result.next_action)
    assert isinstance(result.next_action, ExecuteStageAction)
    assert result.next_action.next_stage is route_profile(Route.STANDARD).next_stage(
        Stage.SPECIFICATION
    )
    assert resolution.merge_context is None
    assert [(item.gate, item.status, item.sha) for item in result.gate_results] == [
        (Gate.SPECIFICATION, GateStatus.PASSED, HEAD),
        (Gate.UI, GateStatus.PASSED, HEAD),
    ]
    assert result.attempt_number == 1
    assert result.input_revision == REVISION


def test_human_gated_resolution_builds_the_success_result_from_a_merge() -> None:
    """The observed merge of the spec request completes the human gate at its head."""
    resolution = _build(
        Stage.SPECIFICATION,
        GateObservation(head_sha=HEAD, merged=True, pipeline_status="success"),
    )

    result = resolution.result
    assert result.status is StageStatus.SUCCEEDED
    assert isinstance(result.next_action, ExecuteStageAction)
    assert [(item.gate, item.status, item.sha) for item in result.gate_results] == [
        (Gate.SPECIFICATION, GateStatus.PASSED, HEAD),
        (Gate.UI, GateStatus.PASSED, HEAD),
    ]


def test_construction_resolution_passes_the_machine_gates_at_the_head_sha() -> None:
    """A green pipeline at the head SHA passes every machine gate of the stage (FR-009)."""
    resolution = _build(
        Stage.CONSTRUCTION, GateObservation(head_sha=HEAD, merged=False, pipeline_status="success")
    )

    result = resolution.result
    assert result.status is StageStatus.SUCCEEDED
    # The (status, action) pairing the flow engine enforces holds for the result.
    assert result.status is expected_result_status(result.next_action)
    assert isinstance(result.next_action, ExecuteStageAction)
    assert result.next_action.next_stage is route_profile(Route.STANDARD).next_stage(
        Stage.CONSTRUCTION
    )
    assert resolution.merge_context is None
    # ``ui`` lives on the specification stage since ADR-028 p.1, so the pipeline
    # maps construction's machine gate ``code`` alone — never the human UI gate.
    assert [(item.gate, item.status, item.sha) for item in result.gate_results] == [
        (Gate.CODE, GateStatus.PASSED, HEAD),
    ]
    assert all(item.gate is not Gate.UI for item in result.gate_results)
    # The result carries the waiting attempt's identity verbatim (ADR-006 p.8).
    assert result.attempt_number == 1
    assert result.input_revision == REVISION


def test_construction_resolution_turns_a_failed_pipeline_into_a_rework_round() -> None:
    resolution = _build(
        Stage.CONSTRUCTION, GateObservation(head_sha=HEAD, merged=False, pipeline_status="failure")
    )

    result = resolution.result
    assert result.status is StageStatus.FAILED
    assert result.status is expected_result_status(result.next_action)
    rework = result.next_action
    assert isinstance(rework, ReworkAction)
    assert rework.round == 1
    assert rework.max_rounds == 3
    assert "failure" in rework.reason
    assert [(item.id, item.origin, item.severity, item.category) for item in result.findings] == [
        ("ci-pipeline:code", FindingOrigin.CI, FindingSeverity.BLOCKER, "code"),
    ]
    assert all(
        item.status is GateStatus.FAILED and item.sha == HEAD for item in result.gate_results
    )


def test_construction_resolution_blocks_on_a_success_without_a_head_sha() -> None:
    """FR-009: a gate is never passed on a success that cannot be attributed to a SHA."""
    resolution = _build(
        Stage.CONSTRUCTION, GateObservation(head_sha=None, merged=False, pipeline_status="success")
    )

    result = resolution.result
    assert result.status is StageStatus.BLOCKED
    assert result.status is expected_result_status(result.next_action)
    assert isinstance(result.next_action, StopAction)
    assert result.next_action.outcome is StopOutcome.BLOCKED
    assert "FR-009" in result.next_action.reason
    assert result.gate_results == []


def test_review_resolution_builds_the_merge_result_with_its_context() -> None:
    run = make_run()
    approval = make_merge_approval(HEAD)
    resolution = _build(
        Stage.REVIEW_VERIFICATION,
        GateObservation(
            head_sha=HEAD, merged=True, pipeline_status="success", approvals=(approval,)
        ),
        run=run,
    )

    result = resolution.result
    assert result.status is StageStatus.SUCCEEDED
    assert result.status is expected_result_status(result.next_action)
    merge = result.next_action
    assert isinstance(merge, MergeAction)
    # The ref comes from the checkpoint's wait_for_ci action, and the observed
    # merge is reflected in it: the stale open status must not leak (ADR-011).
    assert merge.change_request.number == 12
    assert merge.change_request.status is ChangeRequestStatus.MERGED
    assert [(item.gate, item.status, item.sha) for item in result.gate_results] == [
        (Gate.REVIEW, GateStatus.PASSED, HEAD),
        (Gate.VERIFICATION, GateStatus.PASSED, HEAD),
    ]
    context = resolution.merge_context
    assert isinstance(context, MergeRequestContext)
    assert context.executor == "human"
    assert context.route is Route.STANDARD
    assert context.stage is Stage.REVIEW_VERIFICATION
    assert context.expected_sha == HEAD
    assert context.head_sha == HEAD
    assert context.human_approvals == (approval,)
    assert context.risk_class is effective_change_risk_class(run.implementation_contract, run.route)


def test_review_merge_without_a_pipeline_passes_only_the_observed_review_gate() -> None:
    """No machine gate is fabricated without a pipeline verdict (FR-009): exact behaviour."""
    resolution = _build(
        Stage.REVIEW_VERIFICATION, GateObservation(head_sha=HEAD, merged=True, pipeline_status=None)
    )

    result = resolution.result
    assert result.status is StageStatus.SUCCEEDED
    assert isinstance(result.next_action, MergeAction)
    assert result.gate_results == [
        GateResult(
            gate=Gate.REVIEW,
            status=GateStatus.PASSED,
            sha=HEAD,
            summary="change request #12 merged",
        )
    ]
    context = resolution.merge_context
    assert context is not None
    assert context.human_approvals == ()


def test_review_merge_without_a_head_sha_passes_no_gate_at_all() -> None:
    """FR-009 binds every passed gate to a SHA — the review gate included."""
    resolution = _build(
        Stage.REVIEW_VERIFICATION, GateObservation(head_sha=None, merged=True, pipeline_status=None)
    )

    result = resolution.result
    assert result.status is StageStatus.SUCCEEDED
    assert result.gate_results == []
    context = resolution.merge_context
    assert context is not None
    assert context.expected_sha is None
    assert context.head_sha is None


def test_review_resolution_enters_the_bounded_rework_loop_on_a_failure() -> None:
    resolution = _build(
        Stage.REVIEW_VERIFICATION,
        GateObservation(head_sha=HEAD, merged=False, pipeline_status="failure"),
    )

    result = resolution.result
    assert result.status is StageStatus.FAILED
    assert result.status is expected_result_status(result.next_action)
    rework = result.next_action
    assert isinstance(rework, ReworkAction)
    assert rework.round == 1
    assert rework.max_rounds == 3
    assert [item.id for item in result.findings] == ["ci-pipeline:verification"]
    assert [(item.gate, item.status, item.sha) for item in result.gate_results] == [
        (Gate.VERIFICATION, GateStatus.FAILED, HEAD)
    ]


def test_review_resolution_stops_when_the_same_blockers_repeat() -> None:
    """T-014: a rework round that changed nothing stops the loop, blocked."""
    prior = StageResult(
        stage=Stage.REVIEW_VERIFICATION,
        run_id="run-001",
        change_id="chg-001",
        attempt_number=1,
        input_revision="old-sha",
        status=StageStatus.FAILED,
        next_action=ReworkAction(round=1, max_rounds=3, reason="prior round"),
        findings=[
            Finding(
                id="f-prior",
                origin=FindingOrigin.CI,
                severity=FindingSeverity.BLOCKER,
                category="verification",
                reviewed_sha="old-sha",
                required_action="make verification pass",
                status=FindingStatus.OPEN,
            )
        ],
        produced_at=NOW,
    )

    resolution = _build(
        Stage.REVIEW_VERIFICATION,
        GateObservation(head_sha=HEAD, merged=False, pipeline_status="failure"),
        history=(prior,),
    )

    result = resolution.result
    assert result.status is StageStatus.BLOCKED
    assert result.status is expected_result_status(result.next_action)
    stop = result.next_action
    assert isinstance(stop, StopAction)
    assert stop.outcome is StopOutcome.BLOCKED
    assert "did not change the set of blocking findings" in stop.reason


def test_review_resolution_blocks_when_the_rework_limit_is_spent() -> None:
    run = make_run()
    run.budget = BudgetSnapshot(max_rework_rounds=3, used_rework_rounds=3)

    resolution = _build(
        Stage.REVIEW_VERIFICATION,
        GateObservation(head_sha=HEAD, merged=False, pipeline_status="failure"),
        run=run,
    )

    result = resolution.result
    assert result.status is StageStatus.BLOCKED
    assert isinstance(result.next_action, StopAction)
    assert "rework limit" in result.next_action.reason
    assert result.next_action.outcome is StopOutcome.BLOCKED


def test_an_unresolved_observation_is_refused_by_the_builder() -> None:
    with pytest.raises(ValueError):
        _build(
            Stage.CONSTRUCTION,
            GateObservation(head_sha=HEAD, merged=False, pipeline_status="in_progress"),
        )


def test_a_merged_observation_without_a_change_request_is_refused() -> None:
    run = make_run()
    change = make_change()  # no change request attached to the snapshot

    with pytest.raises(ValueError):
        build_gate_resolution(
            run=run,
            stage=Stage.REVIEW_VERIFICATION,
            change=change,
            checkpoint=_checkpoint(Stage.REVIEW_VERIFICATION, run, change).model_copy(
                update={
                    "next_action": WaitForCIAction(reason="waiting for the pipeline"),
                }
            ),
            observation=GateObservation(head_sha=HEAD, merged=True, pipeline_status=None),
            input_revision=REVISION,
            attempt_number=1,
            now=NOW,
        )
