"""Unit tests of the ``factory outbox`` CLI commands (T028, contract cli.md).

The database-backed pass itself is exercised in the integration suite; these
tests cover the report rendering and the configuration error paths (exit 2,
contract cli.md) without any database or network access.
"""

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.exc import SQLAlchemyError

import dark_factory.cli._common as cli_common
import dark_factory.cli.outbox as outbox_module
from dark_factory.cli.main import (
    EXIT_INVALID_INPUT,
    EXIT_OK,
    OutboxDispatchArgs,
    OutboxReplayArgs,
    OutboxSkipArgs,
    main,
)
from dark_factory.orchestration.events.models import (
    DeliveryOutcome,
    DeliveryOutcomeKind,
    DispatchReport,
)

NOW = datetime(2026, 9, 14, 12, 0, 0, tzinfo=UTC)


def _report() -> DispatchReport:
    return DispatchReport(
        reserved=3,
        outcomes=(
            DeliveryOutcome(
                event_id="evt-1",
                consumer_id="tracker",
                outcome=DeliveryOutcomeKind.DELIVERED,
                attempts=1,
            ),
            DeliveryOutcome(
                event_id="evt-2",
                consumer_id="tracker",
                outcome=DeliveryOutcomeKind.FAILED,
                attempts=1,
                next_attempt_at=NOW,
                error="boom",
            ),
            DeliveryOutcome(
                event_id="evt-3", consumer_id="tracker", outcome=DeliveryOutcomeKind.DEFERRED
            ),
            DeliveryOutcome(event_id="evt-4", outcome=DeliveryOutcomeKind.CLEANED),
        ),
    )


def test_render_text_lists_header_and_entries() -> None:
    text = outbox_module.render_text(_report())
    lines = text.splitlines()

    assert (
        lines[0] == "factory outbox dispatch: reserved=3 delivered=1 failed=1 deferred=1 cleaned=1"
    )
    assert "evt-1/tracker: delivered (attempts=1)" in text
    assert f"evt-2/tracker: failed (attempts=1, next={NOW.isoformat()}, error: boom)" in text
    assert "evt-3/tracker: deferred (attempts=0)" in text
    assert "evt-4: cleaned (attempts=0)" in text


def test_render_json_round_trips_the_report() -> None:
    payload = json.loads(outbox_module.render_json(_report()))

    assert payload == _report().model_dump(mode="json")


def test_dispatch_rejects_a_non_positive_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "not-a-valid-database-url")

    code = outbox_module.run_dispatch_command(
        OutboxDispatchArgs(once=False, json_output=False, limit=0, cleanup=False)
    )

    assert code == EXIT_INVALID_INPUT


def test_invalid_database_url_fails_with_invalid_configuration(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", "not-a-valid-database-url")

    code = outbox_module.run_dispatch_command(
        OutboxDispatchArgs(once=False, json_output=False, limit=None, cleanup=False)
    )

    assert code == EXIT_INVALID_INPUT
    captured = capsys.readouterr()
    # The URL and the exception text may embed credentials — never echoed (ADR-009).
    assert "not-a-valid-database-url" not in captured.err
    assert "not-a-valid-database-url" not in captured.out


def test_unreachable_state_store_fails_with_invalid_configuration(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class _UnreachableEngine:
        def connect(self) -> None:
            raise SQLAlchemyError("connection refused")

        def dispose(self) -> None:
            return None

    def _failing_engine(url: str) -> _UnreachableEngine:
        return _UnreachableEngine()

    monkeypatch.setattr(cli_common, "create_state_engine", _failing_engine)

    code = outbox_module.run_dispatch_command(
        OutboxDispatchArgs(once=False, json_output=True, limit=None, cleanup=False)
    )

    assert code == EXIT_INVALID_INPUT
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "state store is not reachable" in captured.err


def test_main_dispatches_outbox_commands_to_their_handlers(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: dict[str, Any] = {}

    def _fake_dispatch(args: OutboxDispatchArgs, **kwargs: Any) -> int:
        seen["dispatch"] = args
        print(outbox_module.render_json(_report()))
        return EXIT_OK

    def _fake_replay(args: OutboxReplayArgs, **kwargs: Any) -> int:
        seen["replay"] = args
        return EXIT_OK

    def _fake_skip(args: OutboxSkipArgs, **kwargs: Any) -> int:
        seen["skip"] = args
        return EXIT_OK

    monkeypatch.setattr(outbox_module, "run_dispatch_command", _fake_dispatch)
    monkeypatch.setattr(outbox_module, "run_replay_command", _fake_replay)
    monkeypatch.setattr(outbox_module, "run_skip_command", _fake_skip)

    assert main(["outbox", "dispatch", "--json", "--limit", "5", "--cleanup"]) == EXIT_OK
    assert main(["outbox", "replay", "--event-id", "evt-1", "--consumer", "tracker"]) == EXIT_OK
    assert main(["outbox", "skip", "--event-id", "evt-1", "--consumer", "tracker", "--json"]) == (
        EXIT_OK
    )

    dispatch_args = seen["dispatch"]
    assert dispatch_args == OutboxDispatchArgs(once=False, json_output=True, limit=5, cleanup=True)
    replay_args = seen["replay"]
    assert replay_args == OutboxReplayArgs(event_id="evt-1", consumer="tracker", json_output=False)
    skip_args = seen["skip"]
    assert skip_args == OutboxSkipArgs(event_id="evt-1", consumer="tracker", json_output=True)
    assert json.loads(capsys.readouterr().out.splitlines()[0])["reserved"] == 3
