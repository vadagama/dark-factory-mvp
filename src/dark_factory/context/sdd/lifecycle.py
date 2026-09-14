"""ChangeSet semantic lifecycle (ADR-020, changeset.md).

The chain is linear — ``draft → proposed → specified → designed → ready →
accepted → reconciled → closed`` — and lives in Git (``change.yaml``), while
runtime state stays in PostgreSQL (ADR-004). The transition table is the single
validation point, mirroring the ``apply_status`` pattern of the run domain
(``dark_factory.changes.run``).
"""

from enum import StrEnum
from typing import Final


class ChangeSetStatus(StrEnum):
    """Semantic status of a ChangeSet; stored in ``change.yaml``."""

    DRAFT = "draft"
    PROPOSED = "proposed"
    SPECIFIED = "specified"
    DESIGNED = "designed"
    READY = "ready"
    ACCEPTED = "accepted"
    RECONCILED = "reconciled"
    CLOSED = "closed"


CHANGESET_STATUS_TRANSITIONS: Final[dict[ChangeSetStatus, frozenset[ChangeSetStatus]]] = {
    ChangeSetStatus.DRAFT: frozenset({ChangeSetStatus.PROPOSED}),
    ChangeSetStatus.PROPOSED: frozenset({ChangeSetStatus.SPECIFIED}),
    ChangeSetStatus.SPECIFIED: frozenset({ChangeSetStatus.DESIGNED}),
    ChangeSetStatus.DESIGNED: frozenset({ChangeSetStatus.READY}),
    ChangeSetStatus.READY: frozenset({ChangeSetStatus.ACCEPTED}),
    ChangeSetStatus.ACCEPTED: frozenset({ChangeSetStatus.RECONCILED}),
    ChangeSetStatus.RECONCILED: frozenset({ChangeSetStatus.CLOSED}),
    ChangeSetStatus.CLOSED: frozenset(),
}

CHANGESET_TERMINAL_STATUSES: Final[frozenset[ChangeSetStatus]] = frozenset(
    status for status, targets in CHANGESET_STATUS_TRANSITIONS.items() if not targets
)


class InvalidChangeSetStatus(ValueError):
    """A ChangeSet status pair outside the transition table was requested."""


def validate_change_status_transition(current: ChangeSetStatus, target: ChangeSetStatus) -> None:
    """Raise :class:`InvalidChangeSetStatus` unless ``current → target`` is allowed."""
    if target not in CHANGESET_STATUS_TRANSITIONS[current]:
        raise InvalidChangeSetStatus(
            f"ChangeSet transition {current.value} -> {target.value} is not allowed"
        )
