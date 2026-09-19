"""``factory product`` commands against PostgreSQL (T070, ADR-030/ADR-031).

Requires ``DARK_FACTORY_TEST_DATABASE_URL``; skipped without it. The registry
rows and audit log are the real ones; validation runs against
``FakeRepositoryProvisioning`` (the contract fake of the provisioning port), so
the shipped states of ADR-031 p.4 are exercised without a repository. The unit
contract of the commands lives in ``tests/test_cli_products.py``.
"""

import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

import dark_factory.cli.products as products_module
from dark_factory.adapters.fakes import FakeRepositoryProvisioning
from dark_factory.changes.enums import ProductStatus, Provider
from dark_factory.changes.refs import RepositoryRef
from dark_factory.cli._common import CLI_ACTOR
from dark_factory.cli.main import (
    EXIT_ERROR,
    EXIT_INVALID_INPUT,
    EXIT_OK,
    ProductAddArgs,
    ProductBootstrapArgs,
    ProductListArgs,
    ProductShowArgs,
    ProductValidateArgs,
)
from dark_factory.orchestration.state.change_store import (
    PRODUCT_ADD_ACTION,
    PRODUCT_BOOTSTRAP_ACTION,
    PRODUCT_VALIDATE_ACTION,
)
from dark_factory.orchestration.state.engine import session_scope
from dark_factory.orchestration.state.models import AuditLogEntry

PRODUCT_ID = "prd-calc"
REPOSITORY = RepositoryRef(provider=Provider.GITHUB, slug="small/calculator")


def _add_args(*, json_output: bool = True) -> ProductAddArgs:
    return ProductAddArgs(
        product_id=PRODUCT_ID,
        name="Calculator",
        provider=Provider.GITHUB,
        slug="small/calculator",
        description="the pilot calculator",
        repository_url="https://github.com/small/calculator",
        baseline_ref=".factory/product",
        dev_env_ref="apps-dev/calculator",
        json_output=json_output,
    )


def _seeded(state: str) -> FakeRepositoryProvisioning:
    provisioning = FakeRepositoryProvisioning()
    provisioning.seed(REPOSITORY, state)
    return provisioning


def _audit(session_factory: sessionmaker[Session]) -> list[tuple[str, str, str | None]]:
    with session_scope(session_factory) as session:
        rows = session.execute(
            select(AuditLogEntry.action, AuditLogEntry.outcome, AuditLogEntry.actor)
            .where(AuditLogEntry.resource_type == "product")
            .order_by(AuditLogEntry.occurred_at)
        ).all()
    return [(row[0], row[1], row[2]) for row in rows]


def _out(capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    payload = json.loads(capsys.readouterr().out)
    assert isinstance(payload, dict)
    return payload


def test_add_show_list_and_validate_round_trip(
    session_factory: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    code = products_module.run_product_add_command(_add_args(), session_factory=session_factory)
    assert code == EXIT_OK
    added = _out(capsys)
    assert added["outcome"] == "created"
    assert added["persisted"] is True

    code = products_module.run_product_show_command(
        ProductShowArgs(product_id=PRODUCT_ID, json_output=True), session_factory=session_factory
    )
    assert code == EXIT_OK
    shown = _out(capsys)
    assert shown["status"] == ProductStatus.CREATED.value
    assert shown["repository"] == {"provider": "github", "slug": "small/calculator"}
    assert shown["baseline_ref"] == ".factory/product"
    assert shown["state_revision"] == 1

    assert (
        products_module.run_product_list_command(
            ProductListArgs(limit=50, offset=0, json_output=True), session_factory=session_factory
        )
        == EXIT_OK
    )
    listed = json.loads(capsys.readouterr().out)
    assert [product["id"] for product in listed] == [PRODUCT_ID]

    assert (
        products_module.run_product_validate_command(
            ProductValidateArgs(product_id=PRODUCT_ID, json_output=True),
            provisioning=_seeded("baseline_absent"),
            session_factory=session_factory,
        )
        == EXIT_OK
    )
    validated = _out(capsys)
    assert validated["outcome"] == "ready"
    product = validated["product"]
    assert isinstance(product, dict)
    assert product["status"] == ProductStatus.READY.value
    assert product["status_reason"] is None
    assert product["state_revision"] == 3, "created -> validating -> ready"
    observation = validated["validation"]
    assert isinstance(observation, dict)
    assert observation["state"] == "baseline_absent"
    assert observation["default_branch"] == "main"

    assert _audit(session_factory) == [
        (PRODUCT_ADD_ACTION, "created", CLI_ACTOR),
        (PRODUCT_VALIDATE_ACTION, "created", CLI_ACTOR),
    ]


def test_add_replays_by_id_and_writes_nothing(
    session_factory: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    products_module.run_product_add_command(_add_args(), session_factory=session_factory)
    capsys.readouterr()

    code = products_module.run_product_add_command(_add_args(), session_factory=session_factory)

    assert code == EXIT_OK
    replayed = _out(capsys)
    assert replayed["outcome"] == "replayed"
    assert replayed["persisted"] is False
    assert sorted(row[1] for row in _audit(session_factory)) == ["created", "replayed"]
    products_module.run_product_list_command(
        ProductListArgs(limit=50, offset=0, json_output=True), session_factory=session_factory
    )
    assert len(json.loads(capsys.readouterr().out)) == 1


def test_validate_records_an_unreachable_repository_as_error(
    session_factory: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    products_module.run_product_add_command(_add_args(), session_factory=session_factory)
    capsys.readouterr()

    code = products_module.run_product_validate_command(
        ProductValidateArgs(product_id=PRODUCT_ID, json_output=False),
        provisioning=_seeded("unavailable"),
        session_factory=session_factory,
    )

    assert code == EXIT_ERROR
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[0].startswith("product prd-calc: error (state=unavailable")
    assert lines[1] == "reason: the repository is not available to the factory"
    products_module.run_product_show_command(
        ProductShowArgs(product_id=PRODUCT_ID, json_output=True), session_factory=session_factory
    )
    shown = _out(capsys)
    assert shown["status"] == ProductStatus.ERROR.value
    assert shown["status_reason"] == "the repository is not available to the factory"


def test_validate_without_provisioning_changes_nothing(
    session_factory: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    products_module.run_product_add_command(_add_args(), session_factory=session_factory)
    capsys.readouterr()

    code = products_module.run_product_validate_command(
        ProductValidateArgs(product_id=PRODUCT_ID, json_output=True),
        provisioning=None,
        session_factory=session_factory,
    )

    assert code == EXIT_INVALID_INPUT
    assert _out(capsys)["error"] == "invalid_input"
    products_module.run_product_show_command(
        ProductShowArgs(product_id=PRODUCT_ID, json_output=True), session_factory=session_factory
    )
    assert _out(capsys)["state_revision"] == 1
    assert [row[0] for row in _audit(session_factory)] == [PRODUCT_ADD_ACTION]


def test_unknown_product_is_invalid_input(
    session_factory: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    code = products_module.run_product_validate_command(
        ProductValidateArgs(product_id="prd-missing", json_output=True),
        provisioning=_seeded("empty"),
        session_factory=session_factory,
    )

    assert code == EXIT_INVALID_INPUT
    assert _out(capsys) == {"error": "invalid_input", "detail": "unknown product 'prd-missing'"}
    assert _audit(session_factory) == []


def test_bootstrap_applies_the_baseline_and_replays(
    session_factory: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        products_module.run_product_add_command(_add_args(), session_factory=session_factory)
        == EXIT_OK
    )
    provisioning = FakeRepositoryProvisioning()
    provisioning.seed(REPOSITORY, "empty")
    args = ProductBootstrapArgs(product_id=PRODUCT_ID, packs=(), json_output=True)

    code = products_module.run_product_bootstrap_command(
        args, provisioning=provisioning, session_factory=session_factory
    )

    assert code == EXIT_OK
    first = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert first["result"]["applied_packs"][0]["name"] == "product-baseline"
    code = products_module.run_product_bootstrap_command(
        args, provisioning=provisioning, session_factory=session_factory
    )
    assert code == EXIT_OK
    assert json.loads(capsys.readouterr().out)["result"]["revision"] == first["result"]["revision"]
    with session_scope(session_factory) as session:
        actions = (
            session.execute(
                select(AuditLogEntry.action).where(AuditLogEntry.resource_id == PRODUCT_ID)
            )
            .scalars()
            .all()
        )
    assert actions.count(PRODUCT_BOOTSTRAP_ACTION) == 2
    assert (
        products_module.run_product_bootstrap_command(args, session_factory=session_factory)
        == EXIT_INVALID_INPUT
    )
