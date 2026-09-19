"""``factory change phases`` (T098, ADR-039): parsing and the text rendering."""

from datetime import UTC, datetime

from dark_factory.changes.enums import Gate, Phase, Stage
from dark_factory.cli.changes import render_phases_text
from dark_factory.cli.main import ChangePhasesArgs, parse_command
from dark_factory.orchestration.phases import PhasesProjection, PhaseState, PhaseView

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


def test_change_phases_parses_its_options() -> None:
    assert parse_command(["change", "phases", "--id", "chg-1"]) == ChangePhasesArgs(
        change_id="chg-1", json_output=False
    )
    assert parse_command(["change", "phases", "--id", "chg-1", "--json"]) == ChangePhasesArgs(
        change_id="chg-1", json_output=True
    )


def test_render_phases_text_marks_the_current_phase_and_shows_no_percentage() -> None:
    projection = PhasesProjection(
        change_id="chg-1",
        current=Phase.ARCHITECTURE,
        phases=(
            PhaseView(
                phase=Phase.REQUIREMENTS,
                index=1,
                label="Требования",
                stage=Stage.SPECIFICATION,
                gate=Gate.SPECIFICATION,
                state=PhaseState.APPROVED,
                state_reason="Согласовано на ревизии abc123def456789",
                revision="abc123def456789",
                approved_revision="abc123def456789",
            ),
            PhaseView(
                phase=Phase.ARCHITECTURE,
                index=2,
                label="Архитектура",
                stage=Stage.SPECIFICATION,
                gate=Gate.SPECIFICATION,
                state=PhaseState.NEEDS_DECISION,
                open_questions=2,
                blocking_questions=1,
                open_comments=3,
                iteration=1,
            ),
        ),
    )
    text = render_phases_text(projection)
    lines = text.splitlines()
    assert lines[0] == "phases of chg-1: current=architecture"
    assert lines[1].startswith("  F1 Требования") and "approved" in lines[1]
    assert "rev=abc123def456" in lines[1]
    assert lines[2].startswith("* F2 Архитектура") and "needs_decision" in lines[2]
    assert "q=2(!1) c=3 iter=1" in lines[2]
    assert "%" not in text
