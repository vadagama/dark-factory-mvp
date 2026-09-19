"""``factory change create|status`` over a stubbed store (T073, contract cli.md).

The registry seams are stubbed like in ``test_cli_products.py``; the guidance
assembly (``build_change_guidance``/``latest_run``) is replaced by the pure
projection over the stubbed facts, so the block printed here is the same
``Guidance`` the API would serve for the same state.
"""

import json
from decimal import Decimal
from typing import Any, ClassVar, cast

import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

import dark_factory.cli.changes as changes_module
from dark_factory.changes.enums import BriefAuthor, ProductStatus, Provider, RiskClass, Scenario
from dark_factory.changes.product import Product
from dark_factory.changes.refs import RepositoryRef
from dark_factory.changes.run import Change
from dark_factory.cli._common import CLI_ACTOR
from dark_factory.cli.main import EXIT_INVALID_INPUT, EXIT_OK, ChangeCreateArgs, ChangeStatusArgs
from dark_factory.orchestration.guidance import change_guidance
from dark_factory.orchestration.state.change_store import CHANGE_INTAKE_ACTION
from tests.changes_factories import make_product

PRODUCT_ID = "prd-001"


class StubSession:
    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


class StubSessionFactory:
    def __call__(self) -> StubSession:
        return StubSession()


def _factory() -> sessionmaker[Session]:
    return cast("sessionmaker[Session]", StubSessionFactory())


class StubProducts:
    products: ClassVar[dict[str, Product]] = {}

    def __init__(self, session: StubSession) -> None: ...

    def get(self, product_id: str) -> Product | None:
        return StubProducts.products.get(product_id)


class StubChanges:
    changes: ClassVar[dict[str, Change]] = {}
    error: ClassVar[Exception | None] = None

    def __init__(self, session: StubSession) -> None: ...

    def create(self, change: Change) -> tuple[Change, bool]:
        if StubChanges.error is not None:
            raise StubChanges.error
        existing = StubChanges.changes.get(change.id)
        if existing is not None:
            return existing, False
        StubChanges.changes[change.id] = change
        return change, True

    def get(self, change_id: str) -> Change | None:
        if StubChanges.error is not None:
            raise StubChanges.error
        return StubChanges.changes.get(change_id)


class StubAudit:
    rows: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, session: StubSession) -> None: ...

    def append(self, **kwargs: Any) -> None:
        StubAudit.rows.append(kwargs)


@pytest.fixture(autouse=True)
def _stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    product = make_product()
    product.apply_status(ProductStatus.VALIDATING)
    product.apply_status(ProductStatus.READY)
    StubProducts.products = {PRODUCT_ID: product}
    StubChanges.changes = {}
    StubChanges.error = None
    StubAudit.rows = []
    monkeypatch.setattr(changes_module, "ProductRepository", StubProducts)
    monkeypatch.setattr(changes_module, "ChangeRepository", StubChanges)
    monkeypatch.setattr(changes_module, "AuditRepository", StubAudit)
    monkeypatch.setattr(changes_module, "latest_run", lambda session, change_id: None)
    monkeypatch.setattr(
        changes_module,
        "build_change_guidance",
        lambda session, change: change_guidance(
            change, product=StubProducts.products.get(change.product_id or ""), run=None
        ),
    )


def _create_args(**overrides: Any) -> ChangeCreateArgs:
    fields: dict[str, Any] = {
        "product_id": PRODUCT_ID,
        "title": "Percent button",
        "problem": "no percent operation",
        "goal": "a % button that works",
        "constraints": ("keep the keyboard layout",),
        "out_of_scope": (),
        "brief_json": None,
        "scenario": Scenario.FULL,
        "limit_usd": "25",
        "token_limit": None,
        "risk_class": RiskClass.R1,
        "description": None,
        "change_id": "chg_test000001",
        "json_output": False,
    }
    fields.update(overrides)
    return ChangeCreateArgs(**fields)


# --- change create -------------------------------------------------------------------


def test_create_registers_the_change_with_brief_scenario_and_limit(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = changes_module.run_change_create_command(_create_args(), session_factory=_factory())

    assert code == EXIT_OK
    stored = StubChanges.changes["chg_test000001"]
    assert stored.product == RepositoryRef(provider=Provider.GITHUB, slug="small/pilot")
    assert stored.product_id == PRODUCT_ID
    assert stored.source.value == "cli"
    assert stored.brief is not None and stored.brief.is_complete
    assert stored.brief.formulated_by is BriefAuthor.OPERATOR
    assert stored.brief.constraints == ("keep the keyboard layout",)
    assert stored.spend_limit is not None
    assert stored.spend_limit.cost_budget_usd == Decimal("25")
    out = capsys.readouterr().out
    assert out.startswith("change chg_test000001: created\n")
    assert "scenario=full, limit=25 USD" in out
    assert "Следующий шаг: Задача готова к фазе «Требования»" in out
    assert (
        "→ Запустить фазу «Требования» (cli: factory run advance --change-id chg_test000001)" in out
    )
    assert StubAudit.rows == [
        {
            "actor": CLI_ACTOR,
            "role": None,
            "action": CHANGE_INTAKE_ACTION,
            "resource_type": "change",
            "resource_id": "chg_test000001",
            "outcome": "created",
        }
    ]


def test_create_json_carries_the_change_and_the_guidance(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = changes_module.run_change_create_command(
        _create_args(json_output=True, scenario=Scenario.SPECS_ONLY, token_limit=50_000),
        session_factory=_factory(),
    )

    assert code == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["outcome"] == "created"
    assert payload["persisted"] is True
    assert payload["change"]["scenario"] == "specs_only"
    assert payload["change"]["spend_limit"] == {"cost_budget_usd": "25", "token_budget": 50000}
    assert payload["guidance"]["subject"] == {"kind": "change", "id": "chg_test000001"}
    assert payload["guidance"]["primary"]["enabled"] is True


def test_create_generates_an_id_when_omitted() -> None:
    changes_module.run_change_create_command(
        _create_args(change_id=None), session_factory=_factory()
    )
    (change_id,) = StubChanges.changes
    assert change_id.startswith("chg_") and len(change_id) == 16


def test_create_replays_an_existing_id(capsys: pytest.CaptureFixture[str]) -> None:
    changes_module.run_change_create_command(_create_args(), session_factory=_factory())
    capsys.readouterr()

    code = changes_module.run_change_create_command(
        _create_args(title="Another title"), session_factory=_factory()
    )

    assert code == EXIT_OK
    assert StubChanges.changes["chg_test000001"].title == "Percent button"
    assert "replayed (already registered, nothing was written)" in capsys.readouterr().out
    assert [row["outcome"] for row in StubAudit.rows] == ["created", "replayed"]


def test_a_draft_brief_is_stored_and_the_next_step_asks_to_complete_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = changes_module.run_change_create_command(
        _create_args(goal=None), session_factory=_factory()
    )

    assert code == EXIT_OK
    out = capsys.readouterr().out
    assert "brief: draft (missing: goal)" in out
    assert "Следующий шаг: Бриф в черновике" in out
    assert "→ Дополнить бриф (api: PUT /changes/chg_test000001/brief)" in out


def test_a_brief_json_file_is_accepted(tmp_path: Any, capsys: pytest.CaptureFixture[str]) -> None:
    brief_file = tmp_path / "brief.json"
    brief_file.write_text(
        json.dumps(
            {
                "problem": "p",
                "goal": "g",
                "constraints": ["c1"],
                "out_of_scope": ["o1"],
                "source_text": "free text",
                "formulated_by": "agent",
            }
        ),
        encoding="utf-8",
    )

    code = changes_module.run_change_create_command(
        _create_args(problem=None, goal=None, constraints=(), brief_json=str(brief_file)),
        session_factory=_factory(),
    )

    assert code == EXIT_OK
    stored = StubChanges.changes["chg_test000001"]
    assert stored.brief is not None
    assert stored.brief.formulated_by is BriefAuthor.AGENT, "an agent brief passes through"
    assert stored.brief.source_text == "free text"
    assert "сформулирован агентом" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"limit_usd": "free"}, "--limit-usd must be a decimal amount"),
        ({"limit_usd": "0"}, "--limit-usd must be positive"),
        ({"limit_usd": "1.23456"}, "--limit-usd must be positive"),
        (
            {
                "brief_json": "/nonexistent/brief.json",
                "problem": None,
                "goal": None,
                "constraints": (),
            },
            "cannot read the brief",
        ),
        ({"product_id": "prd-missing"}, "unknown product 'prd-missing'"),
    ],
)
def test_invalid_intake_input_is_exit_2(
    overrides: dict[str, Any], fragment: str, capsys: pytest.CaptureFixture[str]
) -> None:
    code = changes_module.run_change_create_command(
        _create_args(**overrides), session_factory=_factory()
    )

    assert code == EXIT_INVALID_INPUT
    assert fragment in capsys.readouterr().err
    assert StubChanges.changes == {}
    assert StubAudit.rows == []


def test_brief_json_cannot_be_combined_with_field_flags(
    tmp_path: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    brief_file = tmp_path / "brief.json"
    brief_file.write_text('{"problem": "p", "goal": "g"}', encoding="utf-8")
    code = changes_module.run_change_create_command(
        _create_args(brief_json=str(brief_file)), session_factory=_factory()
    )
    assert code == EXIT_INVALID_INPUT
    assert "cannot be combined" in capsys.readouterr().err


# --- change status -------------------------------------------------------------------


def test_status_prints_the_change_and_the_same_guidance(
    capsys: pytest.CaptureFixture[str],
) -> None:
    changes_module.run_change_create_command(_create_args(), session_factory=_factory())
    created = capsys.readouterr().out

    code = changes_module.run_change_status_command(
        ChangeStatusArgs(change_id="chg_test000001", json_output=False), session_factory=_factory()
    )

    assert code == EXIT_OK
    status = capsys.readouterr().out
    assert "change chg_test000001: Percent button" in status
    assert "run: none" in status
    guidance_block = status[status.index("Следующий шаг:") :]
    assert guidance_block == created[created.index("Следующий шаг:") :], "one next step everywhere"


def test_status_json_shape(capsys: pytest.CaptureFixture[str]) -> None:
    changes_module.run_change_create_command(_create_args(), session_factory=_factory())
    capsys.readouterr()

    code = changes_module.run_change_status_command(
        ChangeStatusArgs(change_id="chg_test000001", json_output=True), session_factory=_factory()
    )

    assert code == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {"change", "run", "guidance"}
    assert payload["run"] is None
    assert payload["guidance"]["headline"] == "Задача готова к фазе «Требования»"


def test_status_of_an_unknown_change_is_invalid_input(capsys: pytest.CaptureFixture[str]) -> None:
    code = changes_module.run_change_status_command(
        ChangeStatusArgs(change_id="chg_missing", json_output=True), session_factory=_factory()
    )
    assert code == EXIT_INVALID_INPUT
    assert json.loads(capsys.readouterr().out) == {
        "error": "invalid_input",
        "detail": "unknown change 'chg_missing'",
    }


def test_a_refusing_store_is_exit_2_without_the_url(capsys: pytest.CaptureFixture[str]) -> None:
    StubChanges.error = SQLAlchemyError("postgresql://user:hunter2@db/factory refused")
    code = changes_module.run_change_status_command(
        ChangeStatusArgs(change_id="chg_x", json_output=True), session_factory=_factory()
    )
    assert code == EXIT_INVALID_INPUT
    captured = capsys.readouterr()
    assert "hunter2" not in captured.out + captured.err
    assert json.loads(captured.out)["detail"] == "the state store is not reachable or misconfigured"
