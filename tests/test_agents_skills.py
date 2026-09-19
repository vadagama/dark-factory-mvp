"""Unit tests of the skill manifests and registry (T-011, T-046, T-047, T092/T094)."""

import re

import pytest

from dark_factory.agents import SkillManifest, SkillNotFoundError, get_profile, get_skill
from dark_factory.agents.artifacts import ArtifactKind
from dark_factory.changes.enums import Role

SKILL_IDS = (
    "intake",
    "requirements-refinement",
    "spec-authoring",
    "ux-flow",
    "accessibility-review",
    "ui-spec",
    "impact-analysis",
    "adr-proposal",
    "solution-design",
    "implementation",
    "implementation-rework",
    "code-review",
    "acceptance-verification",
    "change-request",
    "threat-model",
    "security-analysis",
    "infra-design",
    "infra-change",
    "pipeline-delivery",
    "task-git-cycle",
    "smoke-verification",
    "rollback-analysis",
)

KEBAB_CASE = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")


def test_all_registered_skills_are_registered_and_unique() -> None:
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


# --- M3 phase skills (T092/T094, ADR-032/ADR-035) ---------------------------


def test_solution_design_produces_the_architecture_phase_artifacts() -> None:
    skill = get_skill("solution-design")
    assert skill.role is Role.ARCHITECT
    assert skill.version == "1.0.0"
    assert set(skill.inputs) == {ArtifactKind.SPEC, ArtifactKind.REQUIREMENTS, ArtifactKind.CONTEXT}
    assert set(skill.outputs) == {ArtifactKind.DESIGN_OVERVIEW, ArtifactKind.ADR_PROPOSAL}
    text = skill.instruction
    # File layout of the contract (m3-contracts.md §1).
    assert ".factory/changes/<year>/CHG-<slug>/" in text
    assert "design/overview.md" in text
    assert "design/decisions/ADR-NNN-<slug>.md" in text
    assert "Never edit spec/**" in text
    # Overview frontmatter and body.
    for key in ("dark-factory.dev/design/v1", "ui: required | not_required", "ui_reason"):
        assert key in text, key
    for heading in ("## Обзор", "```mermaid```", "## Компоненты", "## Решения"):
        assert heading in text, heading
    # ADR frontmatter and sections; the agent never writes an acceptance.
    for key in ("dark-factory.dev/adr/v1", "adr:<product>:NNNN", "impact:", "status: proposed"):
        assert key in text, key
    for heading in (
        "## Контекст",
        "## Решение",
        "## Обоснование",
        "## Альтернативы",
        "## Последствия",
    ):
        assert heading in text, heading
    assert "| Вариант | Плюсы | Минусы | Почему не выбран |" in text
    assert "operator's call" in text
    # Backend-only changes, stable ids and the alternative request.
    assert "not_required with a concrete ui_reason" in text
    assert "never renumber or rename" in text
    assert "names an ADR id" in text
    assert "`questions`" in text and "`rework-summary`" in text
    stops = " ".join(skill.stop_conditions)
    assert "budget" in stops and "risk" in stops
    assert "not approved or not available" in stops


def test_ui_spec_produces_the_interface_phase_artifacts() -> None:
    skill = get_skill("ui-spec")
    assert skill.role is Role.DESIGN
    assert skill.version == "1.0.0"
    assert set(skill.inputs) == {ArtifactKind.SPEC, ArtifactKind.ADR_PROPOSAL, ArtifactKind.CONTEXT}
    assert skill.outputs == (ArtifactKind.UX_SPEC,)
    text = skill.instruction
    assert "design/ui/scenarios/SCN-NNN-<slug>.md" in text
    assert "design/ui/screens/SCR-NNN-<slug>.md" in text
    assert "Never edit spec/**" in text
    # Scenario frontmatter.
    assert "dark-factory.dev/ui-scenario/v1" in text
    assert "steps:" in text and "{id: S<n>, text, screen: SCR-NNN" in text
    assert "primary user flow" in text
    # Screen frontmatter: route, preview_url, the five states, elements, transitions.
    assert "dark-factory.dev/ui-screen/v1" in text
    for key in ("route", "preview_url", "states:", "elements:", "transitions:"):
        assert key in text, key
    assert "loading, empty, error, success, access" in text
    assert "all five" in text
    assert "EL-<slug>" in text and "component: <UI kit component>" in text
    # UI kit only, stable anchors.
    assert "packs/ui" in text
    assert "never" in text and "invent a component" in text
    assert "stable comment anchors" in text and "never rename or renumber" in text
    stops = " ".join(skill.stop_conditions)
    assert "ui: not_required" in stops
    assert "UI-kit pattern that does not exist" in stops


def test_design_overview_is_a_stable_wire_value() -> None:
    assert ArtifactKind.DESIGN_OVERVIEW.value == "design_overview"
    assert ArtifactKind.ADR_PROPOSAL.value == "adr_proposal"
    assert ArtifactKind.UX_SPEC.value == "ux_spec"
