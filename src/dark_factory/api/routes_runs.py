"""Read-only run endpoints of the factory API (T035, contract api.md).

All reads are stateless: one session per request, no token required on the
local contour (ADR-009 p.7). Unknown runs answer 404 with the RFC 7807-like
body produced by the app-level exception handlers.
"""

from collections.abc import Callable, Iterator
from decimal import Decimal
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from dark_factory.api.aggregates import (
    build_run_trace,
    evidence_by_id,
    findings_by_id,
    latest_gate_results,
    open_blocker_count,
)
from dark_factory.api.dto import RunCard, RunSummary, RunTrace, StageSummary, UsageAggregate
from dark_factory.changes.enums import FindingSeverity, FindingStatus, RunStatus, Stage
from dark_factory.changes.findings import Finding, GateResult
from dark_factory.changes.refs import Evidence
from dark_factory.changes.run import StageResult
from dark_factory.orchestration.state.models import Execution, UsageRecord
from dark_factory.orchestration.state.models import Stage as StageRow
from dark_factory.orchestration.state.stage_results import StageResultRepository


def _summary(row: Execution) -> RunSummary:
    return RunSummary(
        run_id=row.id,
        change_id=row.change_id,
        route=row.route,
        provider=row.provider,
        status=row.status,
        state_revision=row.state_revision,
        created_at=row.created_at,
        updated_at=row.updated_at,
        finished_at=row.finished_at,
    )


def create_runs_router(
    session_dependency: Callable[..., Iterator[Session]],
) -> APIRouter:
    """Build the ``/runs`` router bound to the per-request session dependency."""
    SessionDep = Annotated[Session, Depends(session_dependency)]
    router = APIRouter()

    def _require_run(run_id: str, session: Session) -> Execution:
        execution = session.get(Execution, run_id)
        if execution is None:
            raise HTTPException(status_code=404, detail=f"Run {run_id!r} does not exist")
        return execution

    def _results(run_id: str, session: Session) -> list[StageResult]:
        return StageResultRepository(session).list_for_run(run_id)

    @router.get("/runs", response_model=list[RunSummary])
    def list_runs(
        session: SessionDep,
        change_id: str | None = None,
        status: RunStatus | None = None,
        stage: Stage | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> list[RunSummary]:
        stmt = select(Execution)
        if change_id is not None:
            stmt = stmt.where(Execution.change_id == change_id)
        if status is not None:
            stmt = stmt.where(Execution.status == status.value)
        if stage is not None:
            stmt = stmt.where(
                Execution.id.in_(select(StageRow.execution_id).where(StageRow.stage == stage.value))
            )
        rows = session.execute(
            stmt.order_by(Execution.created_at, Execution.id).limit(limit).offset(offset)
        ).scalars()
        return [_summary(row) for row in rows]

    @router.get("/runs/{run_id}", response_model=RunCard)
    def get_run(run_id: str, session: SessionDep) -> RunCard:
        execution = _require_run(run_id, session)
        stage_rows = session.execute(
            select(StageRow)
            .where(StageRow.execution_id == run_id)
            .order_by(StageRow.stage, StageRow.input_revision)
        ).scalars()
        usage_row = session.execute(
            select(
                func.coalesce(func.sum(UsageRecord.prompt_tokens), 0),
                func.coalesce(func.sum(UsageRecord.completion_tokens), 0),
                func.sum(UsageRecord.cost),
                func.coalesce(func.sum(UsageRecord.manual_interventions), 0),
            ).where(UsageRecord.execution_id == run_id)
        ).one()
        results = _results(run_id, session)
        cost = cast("Decimal | None", usage_row[2])
        return RunCard(
            run_id=execution.id,
            change_id=execution.change_id,
            route=execution.route,
            provider=execution.provider,
            status=execution.status,
            state_revision=execution.state_revision,
            created_at=execution.created_at,
            updated_at=execution.updated_at,
            finished_at=execution.finished_at,
            stages=[
                StageSummary(
                    stage=row.stage,
                    status=row.status,
                    input_revision=row.input_revision,
                    attempt_count=row.attempt_count,
                )
                for row in stage_rows
            ],
            usage=UsageAggregate(
                prompt_tokens=int(usage_row[0]),
                completion_tokens=int(usage_row[1]),
                cost=cost,
                manual_interventions=int(usage_row[3]),
            ),
            gates=latest_gate_results(results),
            open_blockers=open_blocker_count(results),
        )

    @router.get("/runs/{run_id}/stage-results", response_model=list[StageResult])
    def list_stage_results(run_id: str, session: SessionDep) -> list[StageResult]:
        _require_run(run_id, session)
        return _results(run_id, session)

    @router.get("/runs/{run_id}/trace", response_model=RunTrace)
    def get_run_trace(run_id: str, session: SessionDep) -> RunTrace:
        execution = _require_run(run_id, session)
        return build_run_trace(session, execution)

    @router.get("/runs/{run_id}/evidence", response_model=list[Evidence])
    def list_evidence(run_id: str, session: SessionDep) -> list[Evidence]:
        _require_run(run_id, session)
        items = evidence_by_id(_results(run_id, session))
        return sorted(items.values(), key=lambda item: item.id)

    @router.get("/runs/{run_id}/evidence/{evidence_id}", response_model=Evidence)
    def get_evidence(run_id: str, evidence_id: str, session: SessionDep) -> Evidence:
        _require_run(run_id, session)
        item = evidence_by_id(_results(run_id, session)).get(evidence_id)
        if item is None:
            raise HTTPException(
                status_code=404, detail=f"Evidence {evidence_id!r} does not exist in run {run_id!r}"
            )
        return item

    @router.get("/runs/{run_id}/gates", response_model=list[GateResult])
    def list_gates(run_id: str, session: SessionDep) -> list[GateResult]:
        _require_run(run_id, session)
        return latest_gate_results(_results(run_id, session))

    @router.get("/runs/{run_id}/findings", response_model=list[Finding])
    def list_findings(
        run_id: str,
        session: SessionDep,
        severity: FindingSeverity | None = None,
        status: FindingStatus | None = None,
    ) -> list[Finding]:
        _require_run(run_id, session)
        selected = [
            finding
            for finding in findings_by_id(_results(run_id, session)).values()
            if (severity is None or finding.severity is severity)
            and (status is None or finding.status is status)
        ]
        return sorted(selected, key=lambda finding: finding.id)

    return router
