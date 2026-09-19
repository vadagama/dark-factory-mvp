"""Durable StageResult store (T035, FR-014, ADR-006 p.3/p.4/p.8).

The ``stage_result`` table is the API's read source for stage results, gates,
findings, evidence and trace. The primary key is the ``attempt_id``
(``changes.keys.attempt_id``), so recording the same attempt twice is a no-op
and previous attempts are never overwritten (FR-014). The write-path wiring of
the CLI/engine is a later task; this repository is the public write API.

One attempt may legitimately be recorded twice (T-092 S3, ADR-006 p.8): a
stage parked on an external wait persists its *interim* ``waiting`` checkpoint
first, and the resolution of the wait is the final outcome of the **same**
attempt — the same attempt id. Recording a non-``waiting`` result over such a
checkpoint *supersedes* it in place: the row keeps its id and its attempt
number, only payload, status and ``produced_at`` move to the final outcome.
Every other repeat is inert — ``waiting`` over anything (a waiting result
carries no outcome, so it cannot supersede one) and any status over a final
one — so a committed outcome is never rewritten and FR-014 stays intact:
supersede touches only the current attempt's own interim checkpoint, never
another attempt's row.
"""

from enum import StrEnum
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from dark_factory.changes.enums import Stage, StageStatus
from dark_factory.changes.keys import attempt_id as compose_attempt_id
from dark_factory.changes.keys import operation_key as compose_operation_key
from dark_factory.changes.run import StageResult
from dark_factory.orchestration.state.models import StageResult as StageResultRow


class RecordOutcome(StrEnum):
    """What :meth:`StageResultRepository.record_outcome` did with one result (T-092 S3)."""

    STORED = "stored"
    """Fresh attempt: the row was inserted."""

    SUPERSEDED = "superseded"
    """A non-``waiting`` result replaced the attempt's own ``waiting`` checkpoint
    in place (ADR-006 p.8): same row, same attempt, final outcome."""

    REPLAYED = "replayed"
    """Nothing was written: the row already holds an authoritative result."""


def _result_from_row(row: StageResultRow) -> StageResult:
    return StageResult.model_validate(row.payload)


class StageResultRepository:
    """Idempotent record and deterministic reads of stage attempt results."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def record_outcome(self, result: StageResult) -> RecordOutcome:
        """Record one stage attempt result and tell how the store took it (T-092 S3).

        The operation key and attempt id are recomposed from the result itself
        (ADR-006 p.3), so the stored id is identical for the same logical
        attempt regardless of the producer. Three outcomes:

        - ``stored``: a fresh attempt was inserted;
        - ``superseded``: a non-``waiting`` result replaced the attempt's own
          ``waiting`` checkpoint in place — the resolution of an external wait
          is the final outcome of the same attempt (ADR-006 p.8), so the row
          keeps its id and attempt number (FR-014: previous attempts are never
          touched, only the current attempt's interim checkpoint moves);
        - ``replayed``: the row already holds an authoritative result — a
          repeat of a ``waiting`` checkpoint or of any final outcome — and
          nothing was written.
        """
        operation = compose_operation_key(result.run_id, result.stage, result.input_revision or "")
        attempt = compose_attempt_id(operation, result.attempt_number)
        payload: dict[str, Any] = result.model_dump(mode="json")
        insert = (
            pg_insert(StageResultRow)
            .values(
                id=attempt,
                operation_key=operation,
                run_id=result.run_id,
                change_id=result.change_id,
                stage=result.stage.value,
                attempt_number=result.attempt_number,
                input_revision=result.input_revision,
                status=result.status.value,
                produced_at=result.produced_at,
                payload=payload,
            )
            .on_conflict_do_nothing(index_elements=[StageResultRow.id])
            .returning(StageResultRow.id)
        )
        if self._session.execute(insert).scalar_one_or_none() is not None:
            return RecordOutcome.STORED
        if result.status is StageStatus.WAITING:
            # A waiting result carries no outcome: it can never supersede a
            # checkpoint — least of all its own.
            return RecordOutcome.REPLAYED
        supersede = (
            update(StageResultRow)
            .where(
                StageResultRow.id == attempt,
                StageResultRow.status == StageStatus.WAITING.value,
            )
            .values(
                payload=payload,
                status=result.status.value,
                produced_at=result.produced_at,
            )
            .returning(StageResultRow.id)
        )
        if self._session.execute(supersede).scalar_one_or_none() is not None:
            return RecordOutcome.SUPERSEDED
        return RecordOutcome.REPLAYED

    def record(self, result: StageResult) -> bool:
        """Record one stage attempt result; ``False`` when nothing was written.

        ``True`` for a fresh attempt and for the supersede of a ``waiting``
        checkpoint by its own final outcome (ADR-006 p.8); ``False`` when the
        attempt already holds an authoritative result. See
        :meth:`record_outcome` for the exact semantics.
        """
        return self.record_outcome(result) is not RecordOutcome.REPLAYED

    def get(
        self,
        run_id: str,
        stage: Stage,
        attempt_number: int,
        *,
        input_revision: str | None = None,
    ) -> StageResult | None:
        """One attempt of a stage of a run.

        ``input_revision`` names the logical operation (ADR-006 p.3): a rework
        round or a phase round of the same stage is another operation whose
        attempts start at 1 again, so ``(run, stage, attempt)`` alone is
        ambiguous — without the revision the earliest produced result won
        and the driver mistook the second round's waiting checkpoint for
        absent, re-executing it (found on the M3 live run). With the revision
        the lookup is exact; without it the earliest result stands (the
        pre-M3 reading, for callers that hold no operation).
        """
        query = select(StageResultRow).where(
            StageResultRow.run_id == run_id,
            StageResultRow.stage == stage.value,
            StageResultRow.attempt_number == attempt_number,
        )
        if input_revision is not None:
            query = query.where(StageResultRow.input_revision == input_revision)
        row = self._session.execute(
            query.order_by(StageResultRow.produced_at).limit(1)
        ).scalar_one_or_none()
        return _result_from_row(row) if row is not None else None

    def list_for_run(self, run_id: str) -> list[StageResult]:
        """All results of a run, ordered by (produced_at, stage, attempt_number)."""
        rows = self._session.execute(
            select(StageResultRow)
            .where(StageResultRow.run_id == run_id)
            .order_by(
                StageResultRow.produced_at, StageResultRow.stage, StageResultRow.attempt_number
            )
        ).scalars()
        return [_result_from_row(row) for row in rows]

    def list_for_change(self, change_id: str) -> list[StageResult]:
        """All results of a change, ordered by (produced_at, stage, attempt_number)."""
        rows = self._session.execute(
            select(StageResultRow)
            .where(StageResultRow.change_id == change_id)
            .order_by(
                StageResultRow.produced_at, StageResultRow.stage, StageResultRow.attempt_number
            )
        ).scalars()
        return [_result_from_row(row) for row in rows]
