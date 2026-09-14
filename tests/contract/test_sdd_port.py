"""Contract tests for SDDPort (Native SDD Core, ADR-020 p.8)."""

import asyncio

import pytest

from dark_factory.ports import BaselineMismatchError, ChangeNotFoundError, SDDPort
from tests.sdd_factories import SDD_CHANGE_ID, SDD_TARGET, make_change_set


def test_adapter_satisfies_protocol(sdd_port: SDDPort) -> None:
    assert isinstance(sdd_port, SDDPort)


def test_create_change_returns_the_change_id(sdd_port: SDDPort) -> None:
    assert asyncio.run(sdd_port.create_change(make_change_set())) == SDD_CHANGE_ID


def test_read_requirements_reports_delta_targets(sdd_port: SDDPort) -> None:
    change_id = asyncio.run(sdd_port.create_change(make_change_set()))
    snapshot = asyncio.run(sdd_port.read_requirements(change_id))
    assert snapshot.change_id == SDD_CHANGE_ID
    assert [entry.id for entry in snapshot.requirements] == [SDD_TARGET]


def test_read_requirements_of_unknown_change_fails(sdd_port: SDDPort) -> None:
    with pytest.raises(ChangeNotFoundError):
        asyncio.run(sdd_port.read_requirements("chg:pilot:2026:9999"))


def test_apply_delta_returns_a_new_revision(sdd_port: SDDPort, sdd_revision: str) -> None:
    change_id = asyncio.run(sdd_port.create_change(make_change_set()))
    new_revision = asyncio.run(sdd_port.apply_delta(change_id, expected_revision=sdd_revision))
    assert new_revision != sdd_revision


def test_apply_delta_with_stale_revision_fails(sdd_port: SDDPort) -> None:
    change_id = asyncio.run(sdd_port.create_change(make_change_set()))
    with pytest.raises(BaselineMismatchError):
        asyncio.run(sdd_port.apply_delta(change_id, expected_revision="deadbeef"))
