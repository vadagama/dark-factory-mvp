"""Contract tests for ReconciliationService (ADR-006 p.9)."""

import asyncio

from dark_factory.ports import (
    ReconcileDesired,
    ReconcileObserved,
    ReconciliationService,
    RunStatus,
)


def test_adapter_satisfies_protocol(reconciliation_service: ReconciliationService) -> None:
    assert isinstance(reconciliation_service, ReconciliationService)


def test_reconcile_reports_in_sync_when_states_match(
    reconciliation_service: ReconciliationService,
) -> None:
    result = asyncio.run(
        reconciliation_service.reconcile(
            desired=ReconcileDesired(run_id="run-001", status=RunStatus.RUNNING, state_revision=3),
            observed=ReconcileObserved(
                run_id="run-001", status=RunStatus.RUNNING, state_revision=3
            ),
        )
    )
    assert result.in_sync is True
    assert result.status is RunStatus.RUNNING
    assert result.state_revision == 3


def test_reconcile_resolves_drift_in_favour_of_postgresql(
    reconciliation_service: ReconciliationService,
) -> None:
    result = asyncio.run(
        reconciliation_service.reconcile(
            desired=ReconcileDesired(run_id="run-001", status=RunStatus.RUNNING, state_revision=5),
            observed=ReconcileObserved(
                run_id="run-001", status=RunStatus.CANCELED, state_revision=7
            ),
        )
    )
    assert result.in_sync is False
    assert result.status is RunStatus.RUNNING
    assert result.state_revision == 5
