"""Strictness policy: required ChangeSet artifacts per workflow profile x risk class.

Defaults fixed in code per the ChangeSet contract
(``specs/001-dark-factory-mvp/contracts/changeset.md``): the canonical artifact
set applies to every non-bugfix profile, ``bugfix-r0`` runs the quick route with
no SDD artifacts, and reconciliation is mandatory for R2+ regardless of profile
(extra artifacts beyond the required set are always allowed).
"""

from enum import StrEnum
from typing import Final

from dark_factory.changes.enums import RiskClass


class WorkflowProfile(StrEnum):
    """Strictness profiles of a ChangeSet (changeset.md, sdd-native-core.md §13)."""

    BUGFIX_R0 = "bugfix-r0"
    PRODUCT_FEATURE = "product-feature"
    UI_RESEARCH = "ui-research"
    ARCHITECTURE_CHANGE = "architecture-change"
    REPOSITORY_REBUILD = "repository-rebuild"
    PLATFORM_CHANGE = "platform-change"


class ArtifactSlot(StrEnum):
    """Named artifact slots of a ChangeSet (``change.yaml`` ``artifacts`` map)."""

    INTENT = "intent"
    SPEC = "spec"
    DESIGN = "design"
    TASKS = "tasks"
    VERIFICATION = "verification"
    EVIDENCE = "evidence"
    RECONCILIATION = "reconciliation"


_DEFAULT_REQUIRED: Final[frozenset[ArtifactSlot]] = frozenset(
    {
        ArtifactSlot.INTENT,
        ArtifactSlot.SPEC,
        ArtifactSlot.DESIGN,
        ArtifactSlot.TASKS,
        ArtifactSlot.VERIFICATION,
        ArtifactSlot.EVIDENCE,
    }
)

_HIGH_RISK: Final[frozenset[RiskClass]] = frozenset({RiskClass.R2, RiskClass.R3, RiskClass.R4})


def required_artifacts(profile: WorkflowProfile, risk_class: RiskClass) -> frozenset[ArtifactSlot]:
    """Artifacts the profile x risk combination must have; empty set means no SDD artifacts."""
    if profile is WorkflowProfile.BUGFIX_R0:
        return frozenset()
    required = set(_DEFAULT_REQUIRED)
    if risk_class in _HIGH_RISK:
        required.add(ArtifactSlot.RECONCILIATION)
    return frozenset(required)
