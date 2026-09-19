"""Unit tests of the agent profile manifests and registry (T-011, ADR-007)."""

import pytest

from dark_factory.agents import CORE_ROLES, AgentProfile, get_profile, get_skill
from dark_factory.agents.artifacts import ArtifactKind
from dark_factory.changes.enums import Role


def test_core_roles_are_exactly_the_nine_catalog_roles() -> None:
    assert CORE_ROLES == (
        Role.PRODUCT,
        Role.DESIGN,
        Role.ARCHITECT,
        Role.DEVELOP,
        Role.QUALITY,
        Role.SECURITY,
        Role.INFRASTRUCTURE,
        Role.CI_CD,
        Role.OPERATION,
    )
    assert set(CORE_ROLES) == set(Role)


def test_every_role_has_a_profile() -> None:
    for role in Role:
        assert get_profile(role).role == role


@pytest.mark.parametrize("role", CORE_ROLES)
def test_profile_is_a_complete_versioned_manifest(role: Role) -> None:
    profile = get_profile(role)
    assert isinstance(profile, AgentProfile)
    assert profile.schema_version == 1
    assert profile.name
    assert profile.version
    assert profile.description
    assert profile.inputs
    assert profile.outputs
    assert profile.tools
    assert profile.constraints
    assert profile.stop_conditions


@pytest.mark.parametrize("role", CORE_ROLES)
def test_profile_skills_exist_and_belong_to_the_role(role: Role) -> None:
    profile = get_profile(role)
    assert profile.skills
    for skill_id in profile.skills:
        skill = get_skill(skill_id)
        assert skill.role == role


def test_architect_profile_carries_the_architecture_phase_skill() -> None:
    profile = get_profile(Role.ARCHITECT)
    assert profile.skills == ("impact-analysis", "adr-proposal", "solution-design")
    assert profile.version == "1.0.1"
    assert "write_file" in profile.tools
    assert set(get_skill("solution-design").outputs) <= set(profile.outputs)
    assert ArtifactKind.DESIGN_OVERVIEW in profile.outputs


def test_design_profile_carries_the_interface_phase_skill() -> None:
    profile = get_profile(Role.DESIGN)
    assert profile.skills == ("ux-flow", "accessibility-review", "ui-spec")
    assert profile.version == "1.0.1"
    assert "write_file" in profile.tools
    assert set(get_skill("ui-spec").inputs) <= set(profile.inputs)
    assert set(get_skill("ui-spec").outputs) <= set(profile.outputs)


@pytest.mark.parametrize("role", CORE_ROLES)
def test_profile_declares_every_input_and_output_of_its_skills(role: Role) -> None:
    profile = get_profile(role)
    for skill_id in profile.skills:
        skill = get_skill(skill_id)
        assert set(skill.outputs) <= set(profile.outputs), skill_id
