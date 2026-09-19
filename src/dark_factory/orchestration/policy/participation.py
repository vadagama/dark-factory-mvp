"""Human participation modes per lifecycle phase (T-016, ADR-018 p.1).

The phase table is a direct transcription of ADR-018 p.1; the stage mapping
projects it onto the five flow stages. Human-in-the-loop phases surface as
human gates of the flow profiles (``dark_factory.orchestration.routes.HUMAN_GATES``);
human-on-the-loop phases stay autonomous with escalation on exceptions
(ADR-018 p.5). Prod is a manual post-MVP phase (T-091) and has no stage.
"""

from typing import Final, Literal

from dark_factory.changes.enums import HumanParticipation, Stage

type PhaseName = Literal[
    "problem_and_requirements",
    "ux_ui",
    "architecture",
    "implementation_planning",
    "coding",
    "quality_and_security_checks",
    "merge",
    "deploy_to_dev",
    "prod",
]
"""Lifecycle phases of ADR-018 p.1 (``prod`` is post-MVP, T-091)."""

PHASE_PARTICIPATION: Final[tuple[tuple[PhaseName, HumanParticipation], ...]] = (
    ("problem_and_requirements", HumanParticipation.IN_THE_LOOP),
    ("ux_ui", HumanParticipation.IN_THE_LOOP),
    ("architecture", HumanParticipation.IN_THE_LOOP),
    ("implementation_planning", HumanParticipation.ON_THE_LOOP),
    ("coding", HumanParticipation.OFF_THE_LOOP),
    ("quality_and_security_checks", HumanParticipation.ON_THE_LOOP),
    ("merge", HumanParticipation.IN_THE_LOOP),
    ("deploy_to_dev", HumanParticipation.OFF_THE_LOOP),
    ("prod", HumanParticipation.IN_THE_LOOP),
)
"""The ADR-018 p.1 table, verbatim (T-016 part 4)."""

STAGE_PARTICIPATION: Final[dict[Stage, HumanParticipation]] = {
    Stage.SPECIFICATION: HumanParticipation.IN_THE_LOOP,
    Stage.PLANNING: HumanParticipation.IN_THE_LOOP,
    Stage.CONSTRUCTION: HumanParticipation.OFF_THE_LOOP,
    Stage.REVIEW_VERIFICATION: HumanParticipation.IN_THE_LOOP,
    Stage.RELEASE: HumanParticipation.OFF_THE_LOOP,
}
"""Participation mode of each flow stage, projected from the phase table.

Specification folds problem/requirements (in) with UX/UI (in); planning keeps
the architecture in-the-loop dominance over on-the-loop scheduling; review
carries the human-confirmed merge (ADR-011 p.2); release is the automatic
deploy to dev after merge (ADR-011 p.2, ADR-018 p.1).
"""


def stage_participation(stage: Stage) -> HumanParticipation:
    """Participation mode of one flow stage (read-only view of the mapping)."""
    return STAGE_PARTICIPATION[stage]
