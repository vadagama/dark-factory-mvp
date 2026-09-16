"""Unit tests of the durable run store that need no database (T-092, ADR-006 p.3).

The row-level behaviour of ``RunStore`` (load, create, advance, persist) is
covered by the PostgreSQL integration suite; only the pure parts live here: the
pinned input revision of a change snapshot and the deterministic run id they
key a run by.
"""

import hashlib

from dark_factory.changes.enums import RiskClass
from dark_factory.changes.run_records import to_json
from dark_factory.orchestration.state.run_store import RunStore, generated_run_id
from tests.changes_factories import make_change


def test_stage_input_revision_is_deterministic_for_one_snapshot() -> None:
    change = make_change()

    first = RunStore.stage_input_revision(change)
    second = RunStore.stage_input_revision(change)

    assert first == second
    assert len(first) == 64
    assert set(first) <= set("0123456789abcdef")


def test_stage_input_revision_is_the_digest_of_the_canonical_snapshot_json() -> None:
    change = make_change()

    expected = hashlib.sha256(to_json(change).encode("utf-8")).hexdigest()

    assert RunStore.stage_input_revision(change) == expected


def test_stage_input_revision_changes_with_the_snapshot() -> None:
    change = make_change()
    retitled = change.model_copy(update={"title": "Add export dialog"})
    rerisked = change.model_copy(update={"risk_class": RiskClass.R3})

    assert RunStore.stage_input_revision(retitled) != RunStore.stage_input_revision(change)
    assert RunStore.stage_input_revision(rerisked) != RunStore.stage_input_revision(change)


def test_generated_run_id_is_scoped_to_the_change_snapshot() -> None:
    first = generated_run_id("chg-001", "rev-1")

    assert first == generated_run_id("chg-001", "rev-1")
    assert first != generated_run_id("chg-001", "rev-2")
    assert first != generated_run_id("chg-002", "rev-1")
    assert first.startswith("run_")
    assert len(first) == 36
