"""Durable StageResult store (T035, FR-014, ADR-006 p.3/p.4).

The ``stage_result`` table is the API's read source for stage results, gates,
findings, evidence and trace. The primary key is the ``attempt_id``
(``changes.keys.attempt_id``), so recording the same attempt twice is a no-op
and previous attempts are never overwritten (FR-014). The write-path wiring of
the CLI/engine is a later task; this repository is the public write API.
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from dark_factory.changes.enums import Stage
from dark_factory.changes.keys import attempt_id as compose_attempt_id
from dark_factory.changes.keys import operation_key as compose_operation_key
from dark_factory.changes.run import StageResult
from dark_factory.orchestration.state.models import StageResult as StageResultRow


def _result_from_row(row: StageResultRow) -> StageResult:
    return StageResult.model_validate(row.payload)


class StageResultRepository:
    """Idempotent record and deterministic reads of stage attempt results."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def record(self, result: StageResult) -> bool:
        """Record one stage attempt result; ``False`` when this attempt is already stored.

        The operation key and attempt id are recomposed from the result itself
        (ADR-006 p.3), so the stored id is identical for the same logical
        attempt regardless of the producer.
        """
        operation = compose_operation_key(result.run_id, result.stage, result.input_revision or "")
        attempt = compose_attempt_id(operation, result.attempt_number)
        payload: dict[str, Any] = result.model_dump(mode="json")
        stmt = (
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
        inserted = self._session.execute(stmt).scalar_one_or_none()
        return inserted is not None

    def get(self, run_id: str, stage: Stage, attempt_number: int) -> StageResult | None:
        """One attempt of a stage of a run (the earliest produced when ambiguous)."""
        row = self._session.execute(
            select(StageResultRow)
            .where(
                StageResultRow.run_id == run_id,
                StageResultRow.stage == stage.value,
                StageResultRow.attempt_number == attempt_number,
            )
            .order_by(StageResultRow.produced_at)
            .limit(1)
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
