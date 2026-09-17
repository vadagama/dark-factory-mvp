"""Unit tests of the agent profile manifests and registry (T-011, ADR-007)."""

import pytest

from dark_factory.agents import CORE_ROLES, AgentProfile, get_profile, get_skill
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
