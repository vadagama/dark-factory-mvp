"""Assemble the inputs of ``Guidance`` from the state store (T074, ADR-033).

Shared by the API (``GET /changes/{id}/guidance``, ``GET /products/{id}/guidance``)
and the CLI (``factory change status``): both surfaces read the same facts and
call the same pure projection.

The projection itself lives in ``orchestration.guidance`` and is pure; this
module only gathers what it needs for one request — the owning product, the
latest run of the change and the technical continuation of that run's latest
stage result — inside the caller's session, so the API and the CLI compute the
very same next step from the very same facts.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from dark_factory.changes.next_action import NextAction
from dark_factory.changes.product import Product
from dark_factory.changes.run import Change, ChangeRun
from dark_factory.orchestration.guidance import Guidance, change_guidance, product_guidance
from dark_factory.orchestration.state.change_store import ChangeRepository, ProductRepository
from dark_factory.orchestration.state.models import Execution
from dark_factory.orchestration.state.run_store import RunStore

__all__ = ["build_change_guidance", "build_product_guidance", "latest_run"]


def latest_run(session: Session, change_id: str) -> ChangeRun | None:
    """The most recently created run of the change, or ``None`` before the first one."""
    run_id = session.execute(
        select(Execution.id)
        .where(Execution.change_id == change_id)
        .order_by(Execution.created_at.desc(), Execution.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    return RunStore(session).load(run_id) if run_id is not None else None


def _waiting_on(session: Session, run: ChangeRun | None) -> NextAction | None:
    if run is None:
        return None
    history = RunStore(session).load_history(run.id)
    return history[-1].next_action if history else None


def build_change_guidance(session: Session, change: Change) -> Guidance:
    """Next step of one change from the store: product, latest run, its waiting reason."""
    product: Product | None = (
        ProductRepository(session).get(change.product_id) if change.product_id else None
    )
    run = latest_run(session, change.id)
    return change_guidance(change, product=product, run=run, waiting_on=_waiting_on(session, run))


def build_product_guidance(session: Session, product: Product) -> Guidance:
    """Next step of one product; the count of its changes feeds the ``why``."""
    changes = ChangeRepository(session).list(limit=200, product_id=product.id)
    return product_guidance(product, open_changes=len(changes))
