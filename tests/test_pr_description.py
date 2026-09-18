"""Tests of the change-request description renderer (T-094).

The renderer turns the packaged (or operator-provided) Markdown template into
the body a change request is opened with: these tests pin the substitution of
every placeholder, the fallbacks for absent tracker data, the fail-closed
construction and the ``DARK_FACTORY_PR_TEMPLATE`` override.
"""

from pathlib import Path

import pytest

from dark_factory.changes.enums import Role, Route, Stage
from dark_factory.changes.usage import BudgetSnapshot
from dark_factory.orchestration.stages.context import StageContext, build_context
from dark_factory.orchestration.stages.pr_description import (
    PR_TEMPLATE_ENV_VAR,
    PrDescriptionRenderer,
    PrTemplateError,
)
from tests.changes_factories import make_change

RUN_ID = "run-001"
REVISION = "abc123"
BRANCH = "factory/chg-001"
COMMIT = "deadbeef"
DESCRIPTION = "The export crashes on empty workbooks."


def _context(
    description: str | None = DESCRIPTION, external_ref: str | None = "PLANE-42"
) -> StageContext:
    return build_context(
        change=make_change().model_copy(
            update={"description": description, "external_ref": external_ref}
        ),
        stage=Stage.CONSTRUCTION,
        route=Route.STANDARD,
        run_id=RUN_ID,
        input_revision=REVISION,
        budget=BudgetSnapshot(),
    )


def _render(renderer: PrDescriptionRenderer, context: StageContext) -> str:
    return renderer.render(
        context,
        role=Role.DEVELOP,
        source_branch=BRANCH,
        target_branch="main",
        commit_sha=COMMIT,
    )


def test_the_default_renderer_renders_the_packaged_template() -> None:
    body = _render(PrDescriptionRenderer.default(), _context())

    assert "## Summary" in body
    assert "Add export button" in body
    assert DESCRIPTION in body
    assert BRANCH in body
    assert COMMIT in body


def test_every_known_placeholder_is_substituted() -> None:
    body = _render(PrDescriptionRenderer.default(), _context())

    assert "{{" not in body


def test_absent_tracker_data_falls_back() -> None:
    body = _render(PrDescriptionRenderer.default(), _context(description=None, external_ref=""))

    assert "_The tracker did not provide a description._" in body
    assert "| Tracker | n/a |" in body


def test_an_unknown_placeholder_fails_construction() -> None:
    with pytest.raises(PrTemplateError, match="operator_name"):
        PrDescriptionRenderer("## {{operator_name}}")


def test_from_env_treats_a_missing_or_blank_variable_as_an_absence() -> None:
    context = _context()
    default = _render(PrDescriptionRenderer.default(), context)

    assert _render(PrDescriptionRenderer.from_env({}), context) == default
    assert _render(PrDescriptionRenderer.from_env({PR_TEMPLATE_ENV_VAR: "  "}), context) == default


def test_from_env_reads_the_template_file_it_points_at(tmp_path: Path) -> None:
    template = tmp_path / "pr-description.md"
    template.write_text("CUSTOM {{change_id}} @ {{commit_sha}}", encoding="utf-8")

    renderer = PrDescriptionRenderer.from_env({PR_TEMPLATE_ENV_VAR: str(template)})

    assert _render(renderer, _context()) == f"CUSTOM chg-001 @ {COMMIT}"


def test_from_env_fails_closed_on_an_unreadable_template_file(tmp_path: Path) -> None:
    missing = tmp_path / "missing" / "pr-description.md"

    with pytest.raises(PrTemplateError, match=PR_TEMPLATE_ENV_VAR):
        PrDescriptionRenderer.from_env({PR_TEMPLATE_ENV_VAR: str(missing)})


def test_the_packaged_template_does_not_embed_the_change_marker() -> None:
    # The provider adapter appends the change-id marker to the body itself, and
    # change_id_from_body reads the *first* marker: a template carrying the
    # marker prefix would shadow it and break the cold change-request lookup.
    renderer = PrDescriptionRenderer.default()

    assert "<!-- dark-factory:change:" not in renderer._template


def test_render_is_deterministic() -> None:
    context = _context()
    renderer = PrDescriptionRenderer.default()

    assert _render(renderer, context) == _render(renderer, context)
