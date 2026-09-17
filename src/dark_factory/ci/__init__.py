"""CI stage catalog of the factory pipeline (T058/T059, ADR-026/ADR-027)."""

from dark_factory.ci.stages import (
    CI_STAGES,
    SKIP_VALUE,
    TOGGLE_VARIABLE_PREFIX,
    CiStage,
    CiStageGroup,
    CiStageWeight,
    is_skipped_value,
    stage_by_job,
    variable_for_job,
)

__all__ = [
    "CI_STAGES",
    "SKIP_VALUE",
    "TOGGLE_VARIABLE_PREFIX",
    "CiStage",
    "CiStageGroup",
    "CiStageWeight",
    "is_skipped_value",
    "stage_by_job",
    "variable_for_job",
]
