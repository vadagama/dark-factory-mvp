"""Unit tests of the skill manifests and registry (T-011)."""

import re

import pytest

from dark_factory.agents import SkillManifest, SkillNotFoundError, get_profile, get_skill
from dark_factory.changes.enums import Role

SKILL_IDS = (
    "intake",
    "requirements-refinement",
    "spec-authoring",
    "implementation",
    "implementation-rework",
    "code-review",
    "acceptance-verification",
    "change-request",
)

KEBAB_CASE = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")


def test_all_first_slice_skills_are_registered_and_unique() -> None:
    manifests = [get_skill(skill_id) for skill_id in SKILL_IDS]
    assert len(manifests) == len(SKILL_IDS)
    assert {manifest.id for manifest in manifests} == set(SKILL_IDS)


def test_skill_ids_are_kebab_case() -> None:
    for skill_id in SKILL_IDS:
        assert KEBAB_CASE.fullmatch(skill_id)


@pytest.mark.parametrize("skill_id", SKILL_IDS)
def test_skill_manifest_is_complete(skill_id: str) -> None:
    skill = get_skill(skill_id)
    assert isinstance(skill, SkillManifest)
    assert skill.schema_version == 1
    assert skill.version
    assert skill.role in Role
    assert skill.purpose
    assert skill.instruction
    assert skill.inputs
    assert skill.outputs
    assert skill.stop_conditions


@pytest.mark.parametrize("skill_id", SKILL_IDS)
def test_every_skill_is_bound_to_its_role_profile(skill_id: str) -> None:
    skill = get_skill(skill_id)
    assert skill.id in get_profile(skill.role).skills


def test_unknown_skill_id_is_an_error() -> None:
    with pytest.raises(SkillNotFoundError, match="'nonexistent-skill'"):
        get_skill("nonexistent-skill")
