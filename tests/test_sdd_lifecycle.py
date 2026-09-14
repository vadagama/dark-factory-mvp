"""ChangeSet lifecycle transitions (ADR-020, changeset.md)."""

from itertools import pairwise

import pytest

from dark_factory.context.sdd.lifecycle import (
    CHANGESET_STATUS_TRANSITIONS,
    CHANGESET_TERMINAL_STATUSES,
    ChangeSetStatus,
    InvalidChangeSetStatus,
    validate_change_status_transition,
)
from tests.sdd_factories import make_manifest

CHAIN = [
    ChangeSetStatus.DRAFT,
    ChangeSetStatus.PROPOSED,
    ChangeSetStatus.SPECIFIED,
    ChangeSetStatus.DESIGNED,
    ChangeSetStatus.READY,
    ChangeSetStatus.ACCEPTED,
    ChangeSetStatus.RECONCILED,
    ChangeSetStatus.CLOSED,
]


def test_transition_table_is_the_linear_chain() -> None:
    for current, target in pairwise(CHAIN):
        assert target in CHANGESET_STATUS_TRANSITIONS[current]
    assert {ChangeSetStatus.CLOSED} == CHANGESET_TERMINAL_STATUSES


def test_manifest_walks_the_whole_chain() -> None:
    manifest = make_manifest()
    for status in CHAIN[1:]:
        manifest.apply_status(status)
    assert manifest.status is ChangeSetStatus.CLOSED


def test_manifest_rejects_skipping_stages() -> None:
    manifest = make_manifest()
    with pytest.raises(InvalidChangeSetStatus, match="draft -> designed"):
        manifest.apply_status(ChangeSetStatus.DESIGNED)


def test_closed_is_terminal() -> None:
    manifest = make_manifest(status=ChangeSetStatus.CLOSED)
    with pytest.raises(InvalidChangeSetStatus):
        manifest.apply_status(ChangeSetStatus.DRAFT)


def test_validate_function_mirrors_the_table() -> None:
    validate_change_status_transition(ChangeSetStatus.READY, ChangeSetStatus.ACCEPTED)
    with pytest.raises(InvalidChangeSetStatus):
        validate_change_status_transition(ChangeSetStatus.READY, ChangeSetStatus.CLOSED)
