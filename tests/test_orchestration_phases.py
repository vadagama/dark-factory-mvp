"""The phase projection (T098, ADR-032, ADR-039): rounds of the specification stage."""

from datetime import UTC, datetime

from dark_factory.changes.enums import (
    DecisionOutcome,
    DecisionSource,
    Gate,
    Phase,
    Provider,
    Route,
    RunStatus,
    Stage,
    StageStatus,
)
from dark_factory.changes.findings import Decision
from dark_factory.changes.next_action import WaitForInputAction
from dark_factory.changes.run import ChangeRun, StageRun
from dark_factory.context.design import UiRequirement
from dark_factory.orchestration.phase_gate import (
    SPECIFICATION_PHASES,
    PhaseGate,
    decision_phase,
    specification_phases,
)
from dark_factory.orchestration.phases import (
    PHASE_ORDER,
    PhaseSettlement,
    PhaseState,
    current_phase,
    next_specification_phase,
    phase_of_path,
    phase_settlement,
    project_phases,
    specification_phase,
)

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
ROOT = ".factory/changes/2026/CHG-0001-percent"


def _decision(
    phase: Phase | None,
    sha: str | None,
    outcome: DecisionOutcome = DecisionOutcome.APPROVED,
    *,
    gate: Gate | None = None,
    comment: str | None = None,
) -> Decision:
    resolved_gate = gate if gate is not None else Gate.SPECIFICATION
    if phase is Phase.INTERFACE and gate is None:
        resolved_gate = Gate.UI
    return Decision(
        id=f"dec-{phase.value if phase else 'legacy'}-{outcome.value}-{sha}",
        gate=resolved_gate,
        outcome=outcome,
        decided_by=DecisionSource.HUMAN,
        decided_at=NOW,
        commit_sha=sha,
        comment=comment,
        phase=phase,
    )


def _run(
    stage: Stage = Stage.SPECIFICATION,
    status: StageStatus = StageStatus.WAITING,
    *,
    route: Route = Route.STANDARD,
    run_status: RunStatus = RunStatus.WAITING,
) -> ChangeRun:
    return ChangeRun(
        id="run-1",
        change_id="chg-001",
        route=route,
        provider=Provider.GITHUB,
        status=run_status,
        stages=[StageRun(id="st-1", stage=stage, status=status)],
    )


def test_the_phases_of_the_specification_stage_follow_the_route() -> None:
    assert specification_phases(Route.STANDARD) == SPECIFICATION_PHASES
    assert specification_phases(Route.QUICK) == (Phase.REQUIREMENTS, Phase.ARCHITECTURE)
    assert next_specification_phase(Route.STANDARD, Phase.REQUIREMENTS) is Phase.ARCHITECTURE
    assert next_specification_phase(Route.STANDARD, Phase.INTERFACE) is None
    assert next_specification_phase(Route.QUICK, Phase.ARCHITECTURE) is None
    assert next_specification_phase(Route.QUICK, Phase.PLAN) is None


def test_a_legacy_decision_reads_as_the_phase_of_its_gate() -> None:
    assert decision_phase(_decision(None, "r1")) is Phase.REQUIREMENTS
    assert decision_phase(_decision(None, "r1", gate=Gate.UI)) is Phase.INTERFACE
    assert decision_phase(_decision(Phase.ARCHITECTURE, "r1")) is Phase.ARCHITECTURE


def test_phase_paths_map_onto_phases() -> None:
    assert phase_of_path(f"{ROOT}/spec/requirements/REQ-001.md") is Phase.REQUIREMENTS
    assert phase_of_path(f"{ROOT}/design/overview.md") is Phase.ARCHITECTURE
    assert phase_of_path(f"{ROOT}/design/decisions/ADR-001-x.md") is Phase.ARCHITECTURE
    assert phase_of_path(f"{ROOT}/tasks/graph.yaml") is Phase.PLAN
    assert phase_of_path(f"{ROOT}/evidence/index.yaml") is None


def test_the_current_phase_is_the_first_unsettled_one() -> None:
    revisions = {Phase.REQUIREMENTS: "s1", Phase.ARCHITECTURE: None, Phase.INTERFACE: None}
    assert specification_phase(Route.STANDARD, [], revisions) is Phase.REQUIREMENTS
    approved = [_decision(Phase.REQUIREMENTS, "s1")]
    assert specification_phase(Route.STANDARD, approved, revisions) is Phase.ARCHITECTURE
    # The architect committed design files: the requirements approval stays current,
    # because it binds to the revision of the requirements' own artifacts.
    revisions = {Phase.REQUIREMENTS: "s1", Phase.ARCHITECTURE: "d1", Phase.INTERFACE: None}
    assert specification_phase(Route.STANDARD, approved, revisions) is Phase.ARCHITECTURE
    both = [*approved, _decision(Phase.ARCHITECTURE, "d1")]
    assert specification_phase(Route.STANDARD, both, revisions) is Phase.INTERFACE
    # A UI-free change: the operator waives the interface; the stage is complete.
    waived = [*both, _decision(Phase.INTERFACE, None, DecisionOutcome.WAIVED, comment="api only")]
    assert specification_phase(Route.STANDARD, waived, revisions) is Phase.INTERFACE
    assert phase_settlement(waived, Phase.INTERFACE, None)[0] is PhaseSettlement.WAIVED


def test_an_edit_after_the_approval_makes_the_phase_current_again() -> None:
    decisions = [_decision(Phase.REQUIREMENTS, "s1"), _decision(Phase.ARCHITECTURE, "d1")]
    revisions = {Phase.REQUIREMENTS: "s2", Phase.ARCHITECTURE: "d1", Phase.INTERFACE: None}
    assert specification_phase(Route.STANDARD, decisions, revisions) is Phase.REQUIREMENTS
    state, settled_by = phase_settlement(decisions, Phase.REQUIREMENTS, "s2")
    assert state is PhaseSettlement.OPEN and settled_by is None
    # A rejection settles nothing; the newest approval wins.
    rejected = [*decisions, _decision(Phase.REQUIREMENTS, "s2", DecisionOutcome.REJECTED)]
    assert phase_settlement(rejected, Phase.REQUIREMENTS, "s2")[0] is PhaseSettlement.OPEN
    re_approved = [*rejected, _decision(Phase.REQUIREMENTS, "s2")]
    assert phase_settlement(re_approved, Phase.REQUIREMENTS, "s2")[0] is PhaseSettlement.APPROVED


def test_current_phase_of_a_change() -> None:
    assert current_phase(None) is Phase.INITIATIVE
    done = _run(run_status=RunStatus.SUCCEEDED, status=StageStatus.SUCCEEDED)
    assert current_phase(done) is Phase.DONE
    assert current_phase(_run(Stage.PLANNING)) is Phase.PLAN
    assert current_phase(_run(Stage.CONSTRUCTION)) is Phase.EXECUTION
    assert current_phase(_run()) is Phase.REQUIREMENTS
    run = _run()
    decisions = [_decision(Phase.REQUIREMENTS, "s1")]
    assert (
        current_phase(run, decisions=decisions, revisions={Phase.REQUIREMENTS: "s1"})
        is Phase.ARCHITECTURE
    )


def _gate(phase: Phase, **overrides: object) -> PhaseGate:
    fields: dict[str, object] = {
        "change_id": "chg-001",
        "phase": phase,
        "gate": Gate.SPECIFICATION if phase is not Phase.INTERFACE else Gate.UI,
        "available": True,
        "current_revision": "s1",
    }
    fields.update(overrides)
    return PhaseGate.model_validate(fields)


def test_projection_lists_eight_phases_in_order_with_the_current_one() -> None:
    run = _run()
    decisions = [_decision(Phase.REQUIREMENTS, "s1")]
    revisions = {Phase.REQUIREMENTS: "s1", Phase.ARCHITECTURE: "d1", Phase.INTERFACE: None}
    projection = project_phases(
        change_id="chg-001",
        run=run,
        decisions=decisions,
        revisions=revisions,
        gates={
            Phase.ARCHITECTURE: _gate(Phase.ARCHITECTURE, open_questions=2, blocking_questions=1)
        },
        waiting_on=WaitForInputAction(reason="design produced"),
    )
    assert projection.current is Phase.ARCHITECTURE
    assert [view.phase for view in projection.phases] == list(PHASE_ORDER)
    assert [view.index for view in projection.phases] == list(range(8))
    by_phase = {view.phase: view for view in projection.phases}
    assert by_phase[Phase.INITIATIVE].state is PhaseState.DONE
    assert by_phase[Phase.REQUIREMENTS].state is PhaseState.APPROVED
    assert by_phase[Phase.REQUIREMENTS].approved_revision == "s1"
    assert by_phase[Phase.ARCHITECTURE].state is PhaseState.NEEDS_DECISION
    assert by_phase[Phase.ARCHITECTURE].open_questions == 2
    assert by_phase[Phase.ARCHITECTURE].blocking_questions == 1
    assert by_phase[Phase.ARCHITECTURE].revision == "d1"
    assert by_phase[Phase.INTERFACE].state is PhaseState.PENDING
    assert by_phase[Phase.PLAN].state is PhaseState.PENDING
    assert by_phase[Phase.DELIVERY].gate is Gate.RELEASE
    assert all(view.label for view in projection.phases)
    assert not any("%" in (view.state_reason or "") for view in projection.phases)


def test_projection_marks_a_stale_approval_and_a_route_free_interface() -> None:
    run = _run(route=Route.QUICK)
    decisions = [_decision(Phase.REQUIREMENTS, "s1")]
    revisions = {Phase.REQUIREMENTS: "s2", Phase.ARCHITECTURE: "d1", Phase.INTERFACE: None}
    projection = project_phases(
        change_id="chg-001",
        run=run,
        decisions=decisions,
        revisions=revisions,
        gates={
            Phase.REQUIREMENTS: _gate(
                Phase.REQUIREMENTS,
                current_revision="s2",
                approvals=[
                    {
                        "decision_id": "dec-1",
                        "outcome": "approved",
                        "revision": "s1",
                        "state": "stale",
                    }
                ],
            )
        },
        waiting_on=WaitForInputAction(reason="waiting"),
    )
    by_phase = {view.phase: view for view in projection.phases}
    assert projection.current is Phase.REQUIREMENTS
    assert by_phase[Phase.REQUIREMENTS].state is PhaseState.STALE
    assert "неактуально" in (by_phase[Phase.REQUIREMENTS].state_reason or "")
    assert by_phase[Phase.INTERFACE].state is PhaseState.NOT_REQUIRED
    assert by_phase[Phase.ARCHITECTURE].state is PhaseState.PENDING


def test_projection_offers_the_ui_waiver_and_shows_waived_and_blocked_states() -> None:
    decisions = [_decision(Phase.REQUIREMENTS, "s1"), _decision(Phase.ARCHITECTURE, "d1")]
    revisions = {Phase.REQUIREMENTS: "s1", Phase.ARCHITECTURE: "d1", Phase.INTERFACE: None}
    proposal = UiRequirement(required=False, source="agent", reason="backend-only")
    projection = project_phases(
        change_id="chg-001",
        run=_run(),
        decisions=decisions,
        revisions=revisions,
        waiting_on=WaitForInputAction(reason="design produced"),
        ui_requirement=proposal,
    )
    interface = next(view for view in projection.phases if view.phase is Phase.INTERFACE)
    assert interface.state is PhaseState.NEEDS_DECISION
    assert "backend-only" in (interface.state_reason or "")
    waived = [
        *decisions,
        _decision(Phase.INTERFACE, None, DecisionOutcome.WAIVED, comment="backend-only"),
    ]
    after = project_phases(
        change_id="chg-001", run=_run(Stage.PLANNING), decisions=waived, revisions=revisions
    )
    states = {view.phase: view.state for view in after.phases}
    assert states[Phase.INTERFACE] is PhaseState.WAIVED
    assert states[Phase.PLAN] is PhaseState.ACTIVE
    assert after.current is Phase.PLAN
    blocked = project_phases(
        change_id="chg-001",
        run=_run(status=StageStatus.BLOCKED, run_status=RunStatus.BLOCKED),
    )
    assert next(v for v in blocked.phases if v.phase is Phase.REQUIREMENTS).state is (
        PhaseState.BLOCKED
    )


def test_projection_before_the_first_run_is_the_intake() -> None:
    projection = project_phases(change_id="chg-001", run=None, brief_complete=True)
    assert projection.current is Phase.INITIATIVE
    assert projection.phases[0].state is PhaseState.NEEDS_DECISION
    assert all(view.state is PhaseState.PENDING for view in projection.phases[1:])
    draft = project_phases(change_id="chg-001", run=None)
    assert draft.phases[0].state is PhaseState.ACTIVE


def test_projection_shows_a_settled_round_waiting_for_its_advance() -> None:
    """The requirements checkpoint is parked while its approval is current (M3 live run):
    the architecture phase is *active* — its round has not started — not waiting for a decision."""
    run = _run()
    decisions = [_decision(Phase.REQUIREMENTS, "s1")]
    revisions = {Phase.REQUIREMENTS: "s1", Phase.ARCHITECTURE: None, Phase.INTERFACE: None}
    projection = project_phases(
        change_id="chg-001",
        run=run,
        decisions=decisions,
        revisions=revisions,
        waiting_on=WaitForInputAction(reason="spec produced"),
        waiting_phase=Phase.REQUIREMENTS,
    )
    by_phase = {view.phase: view for view in projection.phases}
    assert projection.current is Phase.ARCHITECTURE
    assert by_phase[Phase.REQUIREMENTS].state is PhaseState.APPROVED
    assert by_phase[Phase.ARCHITECTURE].state is PhaseState.ACTIVE
    assert "запустите раунд" in (by_phase[Phase.ARCHITECTURE].state_reason or "")

    same_round = project_phases(
        change_id="chg-001",
        run=run,
        decisions=[],
        revisions={Phase.REQUIREMENTS: "s1"},
        waiting_on=WaitForInputAction(reason="spec produced"),
        waiting_phase=Phase.REQUIREMENTS,
    )
    assert same_round.current is Phase.REQUIREMENTS
    requirements = next(v for v in same_round.phases if v.phase is Phase.REQUIREMENTS)
    assert requirements.state is PhaseState.NEEDS_DECISION


def test_iteration_counts_the_rounds_of_the_phase_not_the_run_budget() -> None:
    """Two rounds spent on the architecture phase show as its iteration only."""
    run = _run()
    run.budget.used_rework_rounds = 2
    gates = {
        Phase.ARCHITECTURE: _gate(Phase.ARCHITECTURE, rework_rounds_used=2, rework_orders_spent=2),
        Phase.REQUIREMENTS: _gate(Phase.REQUIREMENTS, rework_rounds_used=2),
    }
    projection = project_phases(change_id="chg-001", run=run, gates=gates)
    by_phase = {view.phase: view for view in projection.phases}
    assert by_phase[Phase.ARCHITECTURE].iteration == 2
    assert by_phase[Phase.REQUIREMENTS].iteration == 0
    assert by_phase[Phase.INTERFACE].iteration == 0


def test_an_edit_after_the_stage_completed_marks_that_phase_stale() -> None:
    """The run is in planning; an ADR edited after the approval leaves architecture *stale*,
    the other design phases approved — and the projection does not crash on a later phase."""
    run = _run(stage=Stage.PLANNING, status=StageStatus.PENDING, run_status=RunStatus.RUNNING)
    decisions = [
        _decision(Phase.REQUIREMENTS, "s1"),
        _decision(Phase.ARCHITECTURE, "d1"),
        _decision(Phase.INTERFACE, "u1"),
    ]
    revisions = {Phase.REQUIREMENTS: "s1", Phase.ARCHITECTURE: "d2", Phase.INTERFACE: "u1"}
    projection = project_phases(
        change_id="chg-001", run=run, decisions=decisions, revisions=revisions
    )
    by_phase = {view.phase: view for view in projection.phases}
    assert projection.current is Phase.PLAN
    assert by_phase[Phase.REQUIREMENTS].state is PhaseState.APPROVED
    assert by_phase[Phase.ARCHITECTURE].state is PhaseState.STALE
    assert by_phase[Phase.INTERFACE].state is PhaseState.APPROVED
    assert by_phase[Phase.PLAN].state is PhaseState.ACTIVE
