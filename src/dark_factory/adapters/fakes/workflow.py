"""In-memory fakes of the workflow engine and reconciliation ports (ADR-006 p.9)."""

from dark_factory.ports import (
    ReconcileDesired,
    ReconcileObserved,
    ReconcileResult,
    ReconciliationService,
    RunNotFoundError,
    RunStatus,
    WorkflowEnginePort,
)

_TERMINAL_RUN_STATUSES = frozenset(
    {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELED, RunStatus.SUPERSEDED}
)


class FakeWorkflowEngine(WorkflowEnginePort):
    """In-memory ``WorkflowEnginePort`` keyed by ``idempotency_key``.

    ``start`` mints deterministic ``run-NNNN`` ids; a replay of the same key
    returns the same run and no duplicate run is created. ``resume`` moves a
    non-terminal run to ``running``, ``cancel`` moves it to ``canceled``;
    terminal runs are left untouched by both (a replay is a no-op). The cancel
    ``reason`` is accepted per the contract; the fake does not persist it.
    """

    def __init__(self) -> None:
        self._statuses: dict[str, RunStatus] = {}
        self._start_keys: dict[str, str] = {}
        self._resume_keys: set[tuple[str, str]] = set()
        self._cancel_keys: set[tuple[str, str]] = set()

    async def start(self, *, idempotency_key: str, expected_revision: int | None = None) -> str:
        existing = self._start_keys.get(idempotency_key)
        if existing is not None:
            return existing
        run_id = f"run-{len(self._statuses) + 1:04d}"
        self._statuses[run_id] = RunStatus.RUNNING
        self._start_keys[idempotency_key] = run_id
        return run_id

    async def resume(self, run_id: str, *, idempotency_key: str) -> str:
        status = self._status(run_id)
        if (run_id, idempotency_key) not in self._resume_keys:
            self._resume_keys.add((run_id, idempotency_key))
            if status not in _TERMINAL_RUN_STATUSES:
                self._statuses[run_id] = RunStatus.RUNNING
        return run_id

    async def cancel(self, run_id: str, *, idempotency_key: str, reason: str) -> None:
        status = self._status(run_id)
        if (run_id, idempotency_key) not in self._cancel_keys:
            self._cancel_keys.add((run_id, idempotency_key))
            if status not in _TERMINAL_RUN_STATUSES:
                self._statuses[run_id] = RunStatus.CANCELED

    async def get_status(self, run_id: str, /) -> RunStatus:
        return self._status(run_id)

    def set_status(self, run_id: str, status: RunStatus) -> None:
        """Force a run status; simulation hook for tests, not part of the port."""
        self._status(run_id)
        self._statuses[run_id] = status

    def _status(self, run_id: str) -> RunStatus:
        try:
            return self._statuses[run_id]
        except KeyError:
            raise RunNotFoundError(f"unknown workflow run {run_id!r}") from None


class FakeReconciliationService(ReconciliationService):
    """In-memory ``ReconciliationService`` (ADR-006 p.9).

    Compares the desired state (PostgreSQL) with the observed state (engine);
    on drift the decision is made in favour of PostgreSQL, so the result always
    carries the desired status and revision.
    """

    async def reconcile(
        self, *, desired: ReconcileDesired, observed: ReconcileObserved
    ) -> ReconcileResult:
        if desired.run_id != observed.run_id:
            raise ValueError(f"reconcile scope mismatch: {desired.run_id!r} vs {observed.run_id!r}")
        return ReconcileResult(
            run_id=desired.run_id,
            in_sync=desired.status is observed.status,
            status=desired.status,
            state_revision=desired.state_revision,
        )
