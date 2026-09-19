"""Product repository against PostgreSQL (T065, ADR-030).

Requires ``DARK_FACTORY_TEST_DATABASE_URL`` (the shared fixtures build the schema
through the real Alembic migration); skipped without it.
"""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect, select
from sqlalchemy.orm import Session, sessionmaker

from dark_factory.changes import Product, ProductStatus
from dark_factory.changes.run import Change, InvalidStatusTransition
from dark_factory.orchestration.state.change_store import (
    ChangeRepository,
    ProductRepository,
)
from dark_factory.orchestration.state.engine import session_scope
from dark_factory.orchestration.state.models import AuditLogEntry
from dark_factory.orchestration.state.models import Change as ChangeRow
from dark_factory.orchestration.state.models import Product as ProductRow
from dark_factory.orchestration.state.repositories import StateConflictError
from tests.changes_factories import make_change, make_product

BASE = datetime(2026, 9, 19, 12, 0, 0, tzinfo=UTC)

REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL_ENV = "DARK_FACTORY_TEST_DATABASE_URL"
MIGRATION_HEAD = "0005_products"
MIGRATION_PARENT = "0004_runner_state"


def _table_names(engine: Engine) -> set[str]:
    return set(inspect(engine).get_table_names())


def _change_columns(engine: Engine) -> set[str]:
    return {column["name"] for column in inspect(engine).get_columns("change")}


def _seed(session_factory: sessionmaker[Session], product: Product | None = None) -> Product:
    created_product = make_product() if product is None else product
    with session_scope(session_factory) as session:
        created, inserted = ProductRepository(session).create(created_product)
        assert inserted
    return created


def test_create_and_get_round_trip(session_factory: sessionmaker[Session]) -> None:
    product = make_product()
    with session_scope(session_factory) as session:
        created, inserted = ProductRepository(session).create(product)
        assert inserted
    assert created == product

    with session_scope(session_factory) as session:
        stored = ProductRepository(session).get(product.id)
    assert stored == product


def test_create_replays_idempotently(session_factory: sessionmaker[Session]) -> None:
    first = _seed(session_factory)
    with session_scope(session_factory) as session:
        replay, inserted_again = ProductRepository(session).create(make_product())
    assert not inserted_again
    assert replay == first
    with session_scope(session_factory) as session:
        assert len(ProductRepository(session).list()) == 1


def test_list_is_ordered_and_paginated(session_factory: sessionmaker[Session]) -> None:
    products = [
        make_product().model_copy(
            update={"id": f"prd-{index}", "created_at": BASE + timedelta(minutes=index)}
        )
        for index in range(3)
    ]
    with session_scope(session_factory) as session:
        repo = ProductRepository(session)
        for product in reversed(products):  # inserted out of order on purpose
            repo.create(product)

    with session_scope(session_factory) as session:
        repo = ProductRepository(session)
        assert [product.id for product in repo.list()] == ["prd-0", "prd-1", "prd-2"]
        assert [product.id for product in repo.list(limit=1, offset=1)] == ["prd-1"]


def test_update_status_advances_the_persisted_payload(
    session_factory: sessionmaker[Session],
) -> None:
    product = _seed(session_factory)
    with session_scope(session_factory) as session:
        updated = ProductRepository(session).update_status(
            product.id, ProductStatus.VALIDATING, expected_revision=1
        )
        assert updated.status is ProductStatus.VALIDATING
        assert updated.state_revision == 2

    with session_scope(session_factory) as session:
        stored = ProductRepository(session).get(product.id)
        assert stored is not None
        assert stored.status is ProductStatus.VALIDATING
        assert stored.state_revision == 2


def test_update_status_records_the_error_reason(
    session_factory: sessionmaker[Session],
) -> None:
    product = _seed(session_factory)
    with session_scope(session_factory) as session:
        repo = ProductRepository(session)
        repo.update_status(product.id, ProductStatus.VALIDATING, expected_revision=1)
        failed = repo.update_status(
            product.id, ProductStatus.ERROR, expected_revision=2, reason="clone failed"
        )
        assert failed.status is ProductStatus.ERROR
        assert failed.status_reason == "clone failed"

    with session_scope(session_factory) as session:
        row = session.get(ProductRow, product.id)
        assert row is not None
        assert row.status == ProductStatus.ERROR.value
        assert row.status_reason == "clone failed"
        assert row.payload["status"] == ProductStatus.ERROR.value
        assert row.payload["status_reason"] == "clone failed"


def test_update_status_rejects_a_stale_revision(
    session_factory: sessionmaker[Session],
) -> None:
    product = _seed(session_factory)
    with pytest.raises(StateConflictError), session_scope(session_factory) as session:
        ProductRepository(session).update_status(
            product.id, ProductStatus.VALIDATING, expected_revision=42
        )


def test_update_status_rejects_a_transition_outside_the_table(
    session_factory: sessionmaker[Session],
) -> None:
    product = _seed(session_factory)
    with pytest.raises(InvalidStatusTransition), session_scope(session_factory) as session:
        ProductRepository(session).update_status(
            product.id, ProductStatus.READY, expected_revision=1
        )


def test_update_status_rejects_an_unknown_product(
    session_factory: sessionmaker[Session],
) -> None:
    with pytest.raises(StateConflictError), session_scope(session_factory) as session:
        ProductRepository(session).update_status(
            "prd-missing", ProductStatus.VALIDATING, expected_revision=1
        )


def test_product_repository_writes_no_audit_rows(
    session_factory: sessionmaker[Session],
) -> None:
    """Audit is an API concern (ADR-009 p.7); the repository stays audit-free."""
    product = _seed(session_factory)
    with session_scope(session_factory) as session:
        ProductRepository(session).update_status(
            product.id, ProductStatus.VALIDATING, expected_revision=1
        )
    with session_scope(session_factory) as session:
        assert list(session.execute(select(AuditLogEntry)).scalars()) == []


def test_a_change_without_product_id_reads_back(
    session_factory: sessionmaker[Session],
) -> None:
    """Pre-T065 changes carry no product and must read back unchanged (ADR-030 p.2)."""
    change = make_change()
    with session_scope(session_factory) as session:
        ChangeRepository(session).create(change)

    with session_scope(session_factory) as session:
        row = session.get(ChangeRow, change.id)
        assert row is not None
        assert row.product_id is None
        stored = ChangeRepository(session).get(change.id)
    assert stored is not None
    assert stored.product_id is None


def test_a_change_persists_its_product_id(session_factory: sessionmaker[Session]) -> None:
    change = Change.model_validate(
        {**make_change().model_dump(mode="json"), "product_id": "prd-001"}
    )
    with session_scope(session_factory) as session:
        ChangeRepository(session).create(change)

    with session_scope(session_factory) as session:
        row = session.get(ChangeRow, change.id)
        assert row is not None
        assert row.product_id == "prd-001"
        stored = ChangeRepository(session).get(change.id)
    assert stored is not None
    assert stored.product_id == "prd-001"


def test_products_migration_is_reversible(state_engine: Engine) -> None:
    url = os.environ[TEST_DATABASE_URL_ENV]
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", url)

    try:
        command.downgrade(config, MIGRATION_PARENT)
        assert "product" not in _table_names(state_engine)
        assert "product_id" not in _change_columns(state_engine)
    finally:
        command.upgrade(config, MIGRATION_HEAD)

    assert "product" in _table_names(state_engine)
    assert "product_id" in _change_columns(state_engine)
