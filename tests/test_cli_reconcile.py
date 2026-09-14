"""Unit tests of the ``factory reconcile`` CLI command (T-063, contract cli.md).

The database-backed pass itself is exercised in the integration suite; these
tests cover the report rendering and the configuration error path (exit 2,
contract cli.md) without any database or network access.
"""

import json
from typing import Any

import pytest
from sqlalchemy.exc import SQLAlchemyError

import dark_factory.cli.reconcile as reconcile_module
from dark_factory.changes.enums import RunStatus
from dark_factory.cli.main import EXIT_INVALID_INPUT, EXIT_OK, ReconcileArgs, main
from dark_factory.orchestration.reconcile.models import (
    AnomalyKind,
    ReconcileAction,
    ReconcileActionKind,
    ReconcileReport,
    RunReconciliation,
)


def _report() -> ReconcileReport:
    return ReconcileReport(
        lease_acquired=True,
        owner_id="owner-1",
        scanned=2,
        entries=(
            RunReconciliation(
                run_id="run-0001",
                change_id="chg-1",
                desired_status=RunStatus.RUNNING,
                desired_revision=2,
                observed_status=RunStatus.RUNNING,
                in_sync=True,
                action=ReconcileAction(
                    kind=ReconcileActionKind.LEASE_TAKEOVER,
                    anomaly=AnomalyKind.EXPIRED_LEASE,
                    run_id="run-0001",
                    reason="lease of 'agent-1' expired",
                ),
                applied=True,
            ),
            RunReconciliation(
                run_id="run-0002",
                change_id="chg-2",
                desired_status=RunStatus.WAITING,
                desired_revision=5,
            ),
        ),
    )


def test_render_text_lists_header_and_entries() -> None:
    text = reconcile_module.render_text(_report())

    assert text.splitlines()[0] == "factory reconcile: owner=owner-1 lease_acquired=True scanned=2"
    assert "run-0001 [running]: lease_takeover (expired_lease) applied=True" in text
    assert "run-0002 [waiting]: no action" in text


def test_render_json_round_trips_the_report() -> None:
    payload = json.loads(reconcile_module.render_json(_report()))

    assert payload == _report().model_dump(mode="json")


def test_invalid_database_url_fails_with_invalid_configuration(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", "not-a-valid-database-url")

    code = reconcile_module.run_reconcile_command(ReconcileArgs(json_output=False))

    assert code == EXIT_INVALID_INPUT
    captured = capsys.readouterr()
    # The URL and the exception text may embed credentials — never echoed (ADR-009).
    assert "not-a-valid-database-url" not in captured.err
    assert "not-a-valid-database-url" not in captured.out


def test_unreachable_state_store_fails_with_invalid_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _UnreachableEngine:
        def connect(self) -> None:
            raise SQLAlchemyError("connection refused")

        def dispose(self) -> None:
            return None

    def _failing_engine(url: str) -> _UnreachableEngine:
        return _UnreachableEngine()

    monkeypatch.setattr(reconcile_module, "create_state_engine", _failing_engine)

    assert (
        reconcile_module.run_reconcile_command(ReconcileArgs(json_output=True))
        == EXIT_INVALID_INPUT
    )


def test_main_dispatches_reconcile_to_the_command_handler(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: dict[str, Any] = {}

    def _fake_command(args: ReconcileArgs, **kwargs: Any) -> int:
        seen["json_output"] = args.json_output
        seen["kwargs"] = kwargs
        print(reconcile_module.render_json(_report()))
        return EXIT_OK

    monkeypatch.setattr(reconcile_module, "run_reconcile_command", _fake_command)

    assert main(["reconcile", "--json"]) == EXIT_OK
    assert seen["json_output"] is True
    payload = json.loads(capsys.readouterr().out)
    assert payload["lease_acquired"] is True
    assert payload["entries"][0]["run_id"] == "run-0001"


def test_default_owner_id_is_unique_per_process(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DARK_FACTORY_RECONCILER_OWNER_ID", raising=False)

    first = reconcile_module._default_owner_id()
    second = reconcile_module._default_owner_id()

    assert first != second
    int(first.rsplit("-", 1)[1], 16)  # the suffix is a uuid hex chunk
    monkeypatch.setenv("DARK_FACTORY_RECONCILER_OWNER_ID", "cronjob-reconciler")
    assert reconcile_module._default_owner_id() == "cronjob-reconciler"
