"""Pure delivery rules of the Outbox Dispatcher (T028, ADR-016 p.5/p.6/p.9).

Deterministic, pure-domain decisions: no I/O, no clock — ``now`` is always an
explicit parameter, so identical facts and an identical ``now`` yield identical
decisions (same discipline as ``orchestration.reconcile.rules``).

Ordering (ADR-016 p.6): global order is not guaranteed; delivery is ordered
only within one stream by the monotonic ``sequence``. For the MVP the stream
is the ``aggregate_id`` — the ``outbox`` table enforces uniqueness of
``(aggregate_id, sequence)``, and every change/run of an aggregate shares the
aggregate's sequence space, so per-aggregate ordering covers change/run
ordering too. A consumer receives the event with the *smallest undelivered*
sequence of its stream; a gap (any undelivered row with a smaller sequence,
including ``dead``) defers the candidate without any state change until the
gap is delivered, replayed or explicitly waived by an operator (``factory
outbox skip``).

Cleanup (ADR-016 p.9): an event leaves the outbox only when every one of its
deliveries reached a terminal status accepted by the consumer contract
(``delivered`` or operator-waived) *and* the retention period has passed.
A ``dead`` delivery blocks deletion until a replay succeeds or the operator
records a decision. Deletion by age alone is not sufficient.
"""

from collections.abc import Iterable
from datetime import datetime, timedelta

from dark_factory.orchestration.state.enums import DeliveryStatus

MAX_ATTEMPTS: int = 5
"""Delivery attempts before a delivery goes ``dead`` (ADR-016 p.5).

With the 30 s base, the x4 exponent and the 1 h cap the schedule is
30 s → 2 min → 8 min → 32 min → dead, so a permanently failing consumer is
parked within roughly an hour of the first attempt instead of retrying
indefinitely.
"""

BACKOFF_BASE_SECONDS: int = 30
"""Base delay of the exponential backoff (delay before the 2nd attempt)."""

BACKOFF_MULTIPLIER: int = 4
"""Exponential growth factor between consecutive retry delays."""

BACKOFF_CAP_SECONDS: int = 3600
"""Upper bound of a single backoff delay; bounds the whole schedule by design."""

RETENTION_DAYS: int = 30
"""Retention of delivered outbox events before cleanup may delete them (ADR-016 p.9).

Matches the MVP audit horizon of ADR-009 §9 (30-day evidence/run-record
retention); revisit together with archival (not implemented: ADR-016 p.9
allows archiving instead of deletion, which stays out of the MVP).
"""

_TERMINAL_ACCEPTED: frozenset[DeliveryStatus] = frozenset(
    {DeliveryStatus.DELIVERED, DeliveryStatus.WAIVED}
)
"""Delivery statuses accepted as terminal for ordering and cleanup (ADR-016 p.6/p.9)."""


def backoff_delay_seconds(attempts: int) -> int:
    """Delay in seconds before the next retry after ``attempts`` failed attempts.

    ``attempts`` is the number of attempts already made (>= 1): the delay
    grows exponentially from :data:`BACKOFF_BASE_SECONDS` by
    :data:`BACKOFF_MULTIPLIER` and is capped at :data:`BACKOFF_CAP_SECONDS`.
    Raises ``ValueError`` for ``attempts < 1`` (a failed delivery has at
    least one attempt by definition).
    """
    if attempts < 1:
        raise ValueError(f"attempts must be >= 1, got {attempts}")
    # int ** int is typed Any by mypy (negative exponents yield float); the
    # exponent is non-negative here, so the value is integral.
    delay = int(BACKOFF_BASE_SECONDS * BACKOFF_MULTIPLIER ** (attempts - 1))
    return min(delay, BACKOFF_CAP_SECONDS)


def is_dead(attempts: int) -> bool:
    """Whether ``attempts`` exhausted the budget and the delivery must go ``dead``."""
    return attempts >= MAX_ATTEMPTS


def is_undelivered(status: DeliveryStatus) -> bool:
    """Whether ``status`` still requires delivery: everything except ``delivered``/``waived``.

    ``dead`` counts as undelivered on purpose: it blocks the stream until an
    operator replays or waives it (ADR-016 p.6), and it blocks cleanup
    (ADR-016 p.9).
    """
    return status not in _TERMINAL_ACCEPTED


def min_undelivered_sequence(
    deliveries: Iterable[tuple[int, DeliveryStatus]],
) -> int | None:
    """Smallest undelivered sequence of one (consumer, stream) pair; ``None`` if none.

    The input is the ``(sequence, status)`` pairs of one consumer's
    deliveries within one stream (``aggregate_id`` for the MVP).
    """
    sequences = (sequence for sequence, status in deliveries if is_undelivered(status))
    return min(sequences, default=None)


def ordering_eligible(*, candidate_sequence: int, min_undelivered_sequence: int | None) -> bool:
    """Whether the candidate may be delivered given its stream's smallest undelivered sequence.

    Strict equality: when the candidate's own row is no longer undelivered
    (e.g. it changed between read and check) the minimum cannot match and the
    candidate is deferred — never a side effect on stale state.
    """
    return min_undelivered_sequence is None or min_undelivered_sequence == candidate_sequence


def is_cleanup_eligible(
    *,
    delivery_statuses: Iterable[DeliveryStatus],
    occurred_at: datetime,
    now: datetime,
    retention_days: int = RETENTION_DAYS,
) -> bool:
    """Whether the outbox event may be deleted (ADR-016 p.9).

    Eligible ⇔ every delivery is in a terminal accepted status
    (``delivered`` or operator-``waived``; ``dead`` blocks) *and* the
    retention period passed: ``occurred_at + retention_days <= now``. An
    event without deliveries is vacuously eligible. Archival (ADR-016 p.9)
    is not implemented; deletion is the only exit from the outbox.
    """
    for status in delivery_statuses:
        if status not in _TERMINAL_ACCEPTED:
            return False
    return occurred_at + timedelta(days=retention_days) <= now
