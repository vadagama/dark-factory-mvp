"""Fixture ``fixtures/chg_smoke.yaml`` drives the US1 walking skeleton (T013).

The fixture is the fixed input of the local smoke run (quickstart §2) and of
the CI parity job (US1 scenario 2, ``factory-us1-parity``): loading it and
running the deterministic construction stage must yield the same ``waiting``
StageResult everywhere. The test encodes those CI expectations: exit code 10,
exactly one StageResult JSON document on stdout, and the input revision
derived from the fixture file bytes.
"""

import hashlib
import json
from pathlib import Path
from typing import Final

import pytest

from dark_factory.cli.main import EXIT_WAITING, main
from dark_factory.cli.stage import load_change_snapshot

FIXTURE_PATH: Final[Path] = Path(__file__).resolve().parent.parent / "fixtures" / "chg_smoke.yaml"


def test_fixture_loads_as_a_valid_change() -> None:
    snapshot = load_change_snapshot(str(FIXTURE_PATH))
    assert snapshot.change.id == "chg_smoke"
    assert snapshot.change.title == "US1 walking skeleton smoke change"
    assert snapshot.change.source.value == "cli"
    assert snapshot.change.product.provider.value == "github"
    assert snapshot.change.product.slug == "vadagama/dark-factory-mvp"
    assert snapshot.change.risk_class.value == "R0"


def test_fixture_runs_construction_to_waiting(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        main(
            [
                "stage",
                "run",
                "--change",
                str(FIXTURE_PATH),
                "--stage",
                "construction",
                "--json",
                "--non-interactive",
            ]
        )
        == EXIT_WAITING
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["schema_version"] == 1
    assert payload["status"] == "waiting"
    assert payload["change_id"] == "chg_smoke"
    assert payload["next_action"]["type"] == "wait_for_input"
    assert payload["input_revision"] == hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest()
