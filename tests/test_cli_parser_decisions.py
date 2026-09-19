"""Parsing of ``factory change decisions|alternative|ui`` (T093/T094)."""

import pytest

from dark_factory.cli.main import (
    EXIT_INVALID_INPUT,
    ChangeAlternativeArgs,
    ChangeDecisionsArgs,
    ChangeUiArgs,
    UiSection,
    parse_command,
)


def test_change_decisions_alternative_and_ui_parse_options() -> None:
    assert parse_command(["change", "decisions", "--id", "chg_1", "--json"]) == ChangeDecisionsArgs(
        change_id="chg_1", json_output=True
    )
    assert parse_command(
        [
            "change",
            "alternative",
            "--id",
            "chg_1",
            "--decision",
            "adr:calc:0001",
            "--instruction",
            "consider a client-side timeout",
            "--comment",
            "cmt_1",
            "--comment",
            "cmt_2",
        ]
    ) == ChangeAlternativeArgs(
        change_id="chg_1",
        decision_id="adr:calc:0001",
        instruction="consider a client-side timeout",
        comment_ids=("cmt_1", "cmt_2"),
        json_output=False,
    )
    assert parse_command(["change", "ui", "--id", "chg_1"]) == ChangeUiArgs(
        change_id="chg_1", section=None, json_output=False
    )
    assert parse_command(
        ["change", "ui", "--id", "chg_1", "--section", "screens", "--json"]
    ) == ChangeUiArgs(change_id="chg_1", section=UiSection.SCREENS, json_output=True)


@pytest.mark.parametrize(
    "argv",
    [
        ["change", "decisions"],
        ["change", "alternative", "--id", "chg_1", "--decision", "adr:x"],
        ["change", "alternative", "--id", "chg_1", "--instruction", "x"],
        ["change", "ui", "--id", "chg_1", "--section", "components"],
    ],
)
def test_m3_commands_reject_invalid_input(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        parse_command(argv)
    assert excinfo.value.code == EXIT_INVALID_INPUT
