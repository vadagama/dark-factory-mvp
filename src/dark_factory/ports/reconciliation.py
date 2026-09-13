"""Reconciliation contracts: desired vs observed state (ADR-006 p.9)."""

from pydantic import BaseModel, ConfigDict, Field

from dark_factory.changes.enums import RunStatus


class ReconcileDesired(BaseModel):
    """Desired execution state read from PostgreSQL (ADR-006 p.9)."""

    model_config = ConfigDict(frozen=True)

    run_id: str = Field(min_length=1)
    status: RunStatus
    state_revision: int = Field(ge=1)


class ReconcileObserved(BaseModel):
    """Execution state observed from the workflow engine (ADR-006 p.9).

    ``state_revision`` is optional: engines are not required to report it.
    """

    model_config = ConfigDict(frozen=True)

    run_id: str = Field(min_length=1)
    status: RunStatus
    state_revision: int | None = Field(default=None, ge=1)


class ReconcileResult(BaseModel):
    """Outcome of one reconciliation pass (ADR-006 p.9).

    On drift the decision is made in favour of PostgreSQL: ``status`` and
    ``state_revision`` always carry the desired (authoritative) values.
    """

    model_config = ConfigDict(frozen=True)

    run_id: str = Field(min_length=1)
    in_sync: bool
    status: RunStatus
    state_revision: int = Field(ge=1)
