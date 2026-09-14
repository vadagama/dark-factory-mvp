"""Strictness policy: required artifacts per workflow profile x risk class (T-016)."""

import pytest

from dark_factory.changes.enums import RiskClass
from dark_factory.context.sdd.strictness import (
    ArtifactSlot,
    WorkflowProfile,
    required_artifacts,
)

ALL_PROFILES = list(WorkflowProfile)
NON_BUGFIX = [profile for profile in ALL_PROFILES if profile is not WorkflowProfile.BUGFIX_R0]
LOW_RISK = [RiskClass.R0, RiskClass.R1]
HIGH_RISK = [RiskClass.R2, RiskClass.R3, RiskClass.R4]


def test_bugfix_r0_requires_no_artifacts_at_any_risk() -> None:
    for risk in RiskClass:
        assert required_artifacts(WorkflowProfile.BUGFIX_R0, risk) == frozenset()


def test_default_profile_set_is_fixed_in_code() -> None:
    expected = frozenset(
        {
            ArtifactSlot.INTENT,
            ArtifactSlot.SPEC,
            ArtifactSlot.DESIGN,
            ArtifactSlot.TASKS,
            ArtifactSlot.VERIFICATION,
            ArtifactSlot.EVIDENCE,
        }
    )
    for profile in NON_BUGFIX:
        for risk in LOW_RISK:
            assert required_artifacts(profile, risk) == expected


@pytest.mark.parametrize("risk", HIGH_RISK)
def test_reconciliation_is_mandatory_for_high_risk(risk: RiskClass) -> None:
    required = required_artifacts(WorkflowProfile.PRODUCT_FEATURE, risk)
    assert ArtifactSlot.RECONCILIATION in required


@pytest.mark.parametrize("risk", LOW_RISK)
def test_reconciliation_is_optional_for_low_risk(risk: RiskClass) -> None:
    assert ArtifactSlot.RECONCILIATION not in required_artifacts(
        WorkflowProfile.PRODUCT_FEATURE, risk
    )
