"""``factory change create|status`` against PostgreSQL (T073, T071, T074).

Requires ``DARK_FACTORY_TEST_DATABASE_URL``; skipped without it. The product
comes from the real registry, the change lands in intake with its brief,
scenario and limit, and the guidance printed by the CLI is the one the API
computes for the same state.
"""

import json

import pytest
from sqlalchemy.orm import Session, sessionmaker

import dark_factory.cli.changes as changes_module
import dark_factory.cli.products as products_module
from dark_factory.changes.enums import Provider, RiskClass, Scenario
from dark_factory.cli.main import (
    EXIT_INVALID_INPUT,
    EXIT_OK,
    ChangeCreateArgs,
    ChangeStatusArgs,
    ProductAddArgs,
)
from dark_factory.orchestration.state.change_store import ChangeRepository
from dark_factory.orchestration.state.engine import session_scope
from dark_factory.orchestration.state.guidance import build_change_guidance


def _add_product(session_factory: sessionmaker[Session]) -> None:
    code = products_module.run_product_add_command(
        ProductAddArgs(
            product_id="prd-calc",
            name="Calculator",
            provider=Provider.GITHUB,
            slug="small/calculator",
            description=None,
            repository_url=None,
            baseline_ref=None,
            dev_env_ref=None,
            json_output=True,
        ),
        session_factory=session_factory,
    )
    assert code == EXIT_OK


def _create_args(**overrides: object) -> ChangeCreateArgs:
    fields: dict[str, object] = {
        "product_id": "prd-calc",
        "title": "Percent button",
        "problem": "no percent operation",
        "goal": "a % button that works",
        "constraints": ("keep keyboard",),
        "out_of_scope": ("scientific mode",),
        "brief_json": None,
        "scenario": Scenario.SPECS_ONLY,
        "limit_usd": "15.50",
        "token_limit": None,
        "risk_class": RiskClass.R1,
        "description": "percent for the calculator",
        "change_id": "chg_calc_0001",
        "json_output": True,
    }
    fields.update(overrides)
    return ChangeCreateArgs(**fields)  # type: ignore[arg-type]


def test_create_and_status_share_the_guidance(
    session_factory: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    _add_product(session_factory)
    capsys.readouterr()

    code = changes_module.run_change_create_command(_create_args(), session_factory=session_factory)
    assert code == EXIT_OK
    created = json.loads(capsys.readouterr().out)
    assert created["outcome"] == "created"
    change = created["change"]
    assert change["product"] == {"provider": "github", "slug": "small/calculator"}
    assert change["product_id"] == "prd-calc"
    assert change["source"] == "cli"
    assert change["scenario"] == "specs_only"
    assert change["spend_limit"]["cost_budget_usd"] == "15.50"
    assert change["brief"]["status"] == "complete"
    assert change["brief"]["formulated_by"] == "operator"

    code = changes_module.run_change_status_command(
        ChangeStatusArgs(change_id="chg_calc_0001", json_output=True),
        session_factory=session_factory,
    )
    assert code == EXIT_OK
    status = json.loads(capsys.readouterr().out)
    assert status["run"] is None
    assert status["guidance"] == created["guidance"], "create and status print one next step"
    # ... and it is the very object the API computes from the store.
    with session_scope(session_factory) as session:
        stored = ChangeRepository(session).get("chg_calc_0001")
        assert stored is not None
        api_guidance = build_change_guidance(session, stored).model_dump(mode="json")
    assert api_guidance == status["guidance"]
    # The product is registered but not validated: the start is blocked honestly.
    assert status["guidance"]["primary"]["enabled"] is False
    assert status["guidance"]["blockers"][0]["how"].endswith(
        "factory product validate --id prd-calc."
    )


def test_create_replays_by_id_and_refuses_an_unknown_product(
    session_factory: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    _add_product(session_factory)
    changes_module.run_change_create_command(_create_args(), session_factory=session_factory)
    capsys.readouterr()

    code = changes_module.run_change_create_command(
        _create_args(title="Renamed"), session_factory=session_factory
    )
    assert code == EXIT_OK
    replayed = json.loads(capsys.readouterr().out)
    assert replayed["outcome"] == "replayed"
    assert replayed["change"]["title"] == "Percent button"

    code = changes_module.run_change_create_command(
        _create_args(product_id="prd-missing", change_id="chg_other"),
        session_factory=session_factory,
    )
    assert code == EXIT_INVALID_INPUT
    assert json.loads(capsys.readouterr().out)["detail"] == "unknown product 'prd-missing'"
