"""Operation-level idempotency for stage runs (T012, FR-017, ADR-006 p.3).

The effectively-once guarantee of a stage rests on two layers (ADR-006 p.3):

- **Operation level (this module)**: a repeat of ``stage run`` with the same
  ``operation_key`` (``run_id + stage + input_revision``) returns the already
  committed :class:`StageResult` instead of executing again — no second
  execution, hence no second external effect. A committed ``succeeded`` result
  is final (no outgoing transition in ``STAGE_STATUS_TRANSITIONS``) and a
  committed ``waiting`` result is persisted before the external wait
  (ADR-006 p.8) and still current — both replay. ``failed``/``blocked`` allow
  a retry per the stage state machine (FAILED/BLOCKED → IN_PROGRESS), so a
  repeat starts a NEW attempt (fresh execution); the external effects of that
  new attempt are deduplicated at the effect level.
- **Effect level (``orchestration.state.ensure_effect`` + ``effect_key``,
  T004)**: external effects created by stage steps (branch/commit/MR from
  T024 on) go through the durable effect ledger with a lookup by the
  deterministic marker before the call — already implemented and
  integration-tested in T004; this module references that layer and does not
  re-implement it.

The store is the evidence directory (T009/T011 layout): the persisted
``stage_result.json`` is the operation's committed artifact until the durable
state store is wired into the CLI. ``StageResult`` carries the full operation
identity (``run_id``, ``stage``, ``input_revision``), so identity is verified
by recomposing the operation key from the stored result.
"""

from pathlib import Path
from typing import Final, Protocol

from dark_factory.changes.enums import StageStatus
from dark_factory.changes.keys import operation_key
from dark_factory.changes.run import StageResult
from dark_factory.changes.run_records import from_json, to_json

STAGE_RESULT_EVIDENCE_NAME: Final[str] = "stage_result.json"
"""Evidence file with the serialized StageResult, written before any wait (ADR-006 p.8)."""

REPLAYABLE_RESULT_STATUSES: Final[frozenset[StageStatus]] = frozenset(
    {StageStatus.SUCCEEDED, StageStatus.WAITING}
)
"""Committed statuses that replay instead of re-executing (ADR-006 p.3).

``succeeded`` is final; ``waiting`` was persisted before the external wait
(ADR-006 p.8) and is still current. ``failed``/``blocked`` are absent on
purpose: the stage state machine allows FAILED/BLOCKED → IN_PROGRESS, so a
repeat starts a new attempt of the same logical operation.
"""


class StageOperationStore(Protocol):
    """Committed-result storage of one logical stage operation (ADR-006 p.3)."""

    def find_existing(self, operation_key: str) -> StageResult | None:
        """Return the committed result of ``operation_key`` or ``None`` when absent."""
        ...

    def record(self, operation_key: str, result: StageResult) -> None:
        """Commit ``result`` as the outcome of the operation ``operation_key``."""
        ...


def stage_operation_key(result: StageResult) -> str | None:
    """Operation key recomposed from the result's own identity (ADR-006 p.3).

    ``None`` when ``input_revision`` is None: without the revision the result
    does not carry a complete operation identity and cannot match any key.
    """
    if result.input_revision is None:
        return None
    return operation_key(result.run_id, result.stage, result.input_revision)


class EvidenceOperationStore:
    """Single-slot StageResult store over one evidence directory (T009 layout).

    The evidence file is the operation's committed artifact: ``record`` replaces
    it atomically, ``find_existing`` accepts only a record of the very same
    operation. Every failure mode of the file (absent, unreadable, torn,
    foreign) yields "no committed result" — the caller then executes fresh, and
    external effects stay protected by the effect ledger.
    """

    def __init__(self, evidence_dir: Path) -> None:
        self._result_path = evidence_dir / STAGE_RESULT_EVIDENCE_NAME

    def find_existing(self, operation_key: str) -> StageResult | None:
        """Return the committed result of ``operation_key`` or ``None``.

        ``None`` when the record is absent (``FileNotFoundError``), unreadable
        (``OSError`` — e.g. the path is a directory; external-effect safety in
        that window comes from the effect ledger, not the operation record),
        unparseable (a torn write: ``ValidationError``/``ValueError`` from
        :func:`from_json`), or foreign — the operation key recomposed from the
        stored result differs from ``operation_key``. Otherwise the parsed
        :class:`StageResult` is returned.
        """
        try:
            result = from_json(StageResult, self._result_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if stage_operation_key(result) != operation_key:
            return None
        return result

    def record(self, operation_key: str, result: StageResult) -> None:
        """Commit ``result`` as the outcome of ``operation_key`` (atomic replace).

        The identity guard is a programmer-error invariant: a result whose
        recomposed key differs from ``operation_key`` must never be persisted.
        The write goes to a ``.tmp`` sibling first and then replaces the target,
        so a crash between write and replace leaves the previous committed
        result intact (at-least-once is allowed; the ledger protects external
        effects).
        """
        composed = stage_operation_key(result)
        if composed is not None and composed != operation_key:
            raise ValueError(
                f"stage result {composed!r} does not match the operation key {operation_key!r}"
            )
        path = self._result_path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(to_json(result), encoding="utf-8")
        tmp.replace(path)
