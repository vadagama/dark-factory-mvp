"""Human participation modes per phase and their stage projection (T-016, ADR-018 p.1)."""

from dark_factory.changes.enums import Gate, HumanParticipation, Route, Stage
from dark_factory.flows.routes import HUMAN_GATES, route_profile
from dark_factory.orchestration.policy.participation import (
    PHASE_PARTICIPATION,
    STAGE_PARTICIPATION,
    stage_participation,
)

# The ADR-018 p.1 table, transcribed verbatim (T-016 part 4).
ADR_018_PHASES: dict[str, HumanParticipation] = {
    "problem_and_requirements": HumanParticipation.IN_THE_LOOP,
    "ux_ui": HumanParticipation.IN_THE_LOOP,
    "architecture": HumanParticipation.IN_THE_LOOP,
    "implementation_planning": HumanParticipation.ON_THE_LOOP,
    "coding": HumanParticipation.OFF_THE_LOOP,
    "quality_and_security_checks": HumanParticipation.ON_THE_LOOP,
    "merge": HumanParticipation.IN_THE_LOOP,
    "deploy_to_dev": HumanParticipation.OFF_THE_LOOP,
    "prod": HumanParticipation.IN_THE_LOOP,
}


def test_phase_table_matches_adr_018_p1() -> None:
    table = dict(PHASE_PARTICIPATION)
    assert table == ADR_018_PHASES


def test_stage_projection_covers_every_stage() -> None:
    assert set(STAGE_PARTICIPATION) == set(Stage)


def test_stage_projection_semantics() -> None:
    assert stage_participation(Stage.SPECIFICATION) is HumanParticipation.IN_THE_LOOP
    # Planning keeps architecture in-the-loop dominance over on-the-loop scheduling.
    assert stage_participation(Stage.PLANNING) is HumanParticipation.IN_THE_LOOP
    # Coding is autonomous (ADR-018 p.4): implementation runs off-the-loop.
    assert stage_participation(Stage.CONSTRUCTION) is HumanParticipation.OFF_THE_LOOP
    # Review carries the human-confirmed merge (ADR-011 p.2).
    assert stage_participation(Stage.REVIEW_VERIFICATION) is HumanParticipation.IN_THE_LOOP
    # Deploy to dev after merge is human-off-the-loop.
    assert stage_participation(Stage.RELEASE) is HumanParticipation.OFF_THE_LOOP


def test_human_gates_are_route_independent() -> None:
    # ``ui`` joined the base set when it moved to the design stage (ADR-029 p.2).
    assert frozenset({Gate.SPECIFICATION, Gate.UI, Gate.REVIEW}) == HUMAN_GATES
    for route in Route:
        assert route_profile(route).human_gates == HUMAN_GATES


def test_human_gates_sit_on_in_the_loop_stages() -> None:
    # The flow-level human gates cover the stages whose projected mode is
    # in-the-loop: specification approval covers discovery (requirements, UX,
    # architecture) and carries the UI gate, review carries the merge. Planning
    # stays in-the-loop via escalation conditions (e.g. a new ADR proposal), not
    # via a flow gate.
    in_the_loop = {
        stage
        for stage, mode in STAGE_PARTICIPATION.items()
        if mode is HumanParticipation.IN_THE_LOOP
    }
    assert in_the_loop == {Stage.SPECIFICATION, Stage.PLANNING, Stage.REVIEW_VERIFICATION}
    assert frozenset({Gate.SPECIFICATION, Gate.UI, Gate.REVIEW}) == HUMAN_GATES
