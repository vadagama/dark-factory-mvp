"""``factory change decisions|alternative|ui`` against PostgreSQL (T093/T094).

Requires ``DARK_FACTORY_TEST_DATABASE_URL``; skipped without it.
"""

import asyncio
import json
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

import dark_factory.cli.changes as changes_module
import dark_factory.cli.products as products_module
from dark_factory.adapters.fakes import FakeRepository
from dark_factory.changes.enums import Provider, RiskClass, Scenario
from dark_factory.changes.refs import RepositoryRef
from dark_factory.cli.main import (
    EXIT_INVALID_INPUT,
    EXIT_OK,
    ChangeAlternativeArgs,
    ChangeCreateArgs,
    ChangeDecisionsArgs,
    ChangeUiArgs,
    ProductAddArgs,
    UiSection,
)
from dark_factory.orchestration.stages.agent import branch_name

PACK = Path(__file__).resolve().parents[2] / "packs" / "product-baseline" / "changeset" / "design"
REPO = RepositoryRef(provider=Provider.GITHUB, slug="small/calculator")
CHANGE_ID = "chg_calc_0001"
CHG = ".factory/changes/2026/CHG-0001-percent"
ADR = f"{CHG}/design/decisions/ADR-001-example.md"
SCN = f"{CHG}/design/ui/scenarios/SCN-001-example.md"
SCR = f"{CHG}/design/ui/screens/SCR-001-example.md"


def _prepare(session_factory: sessionmaker[Session]) -> tuple[FakeRepository, str]:
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
    code = changes_module.run_change_create_command(
        ChangeCreateArgs(
            product_id="prd-calc",
            title="Percent button",
            problem="no percent",
            goal="percent works",
            constraints=(),
            out_of_scope=(),
            brief_json=None,
            scenario=Scenario.SPECS_ONLY,
            limit_usd="15.50",
            token_limit=None,
            risk_class=RiskClass.R1,
            description=None,
            change_id=CHANGE_ID,
            json_output=True,
        ),
        session_factory=session_factory,
    )
    assert code == EXIT_OK
    repository = FakeRepository()
    branch = branch_name(CHANGE_ID)
    asyncio.run(repository.ensure_branch(REPO, "main", from_revision="base", idempotency_key="m"))
    asyncio.run(repository.ensure_branch(REPO, branch, from_revision="base", idempotency_key="b"))
    head = asyncio.run(
        repository.publish_commit(
            REPO,
            branch,
            {
                ADR: (PACK / "decisions" / "ADR-001-example.md").read_bytes(),
                SCN: (PACK / "ui" / "scenarios" / "SCN-001-example.md").read_bytes(),
                SCR: (PACK / "ui" / "screens" / "SCR-001-example.md").read_bytes(),
                ".factory/product/factory.yaml": b"dev_url: https://dev.calc.test\n",
            },
            message="design v1",
            idempotency_key="c1",
        )
    )
    return repository, head


def _json_out(capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    return dict(json.loads(capsys.readouterr().out.strip().splitlines()[-1]))


def test_decisions_alternative_and_ui_from_the_cli(
    session_factory: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    repository, head = _prepare(session_factory)
    capsys.readouterr()

    code = changes_module.run_change_decisions_command(
        ChangeDecisionsArgs(change_id=CHANGE_ID, json_output=True),
        session_factory=session_factory,
        repository=repository,
    )
    assert code == EXIT_OK
    out = _json_out(capsys)
    view = out["decisions"]
    assert view["revision"] == head and view["approved"] is False  # type: ignore[index]
    (card,) = view["decisions"]  # type: ignore[index]
    assert card["id"] == "adr:example-product:0001" and card["status"] == "proposed"
    assert out["guidance"]["headline"], "every command ends with the next step"  # type: ignore[index]

    code = changes_module.run_change_decisions_command(
        ChangeDecisionsArgs(change_id=CHANGE_ID, json_output=False),
        session_factory=session_factory,
        repository=repository,
    )
    assert code == EXIT_OK
    text = capsys.readouterr().out
    assert "adr:example-product:0001" in text and "status=proposed" in text
    assert "alternatives=2" in text and "pending_alternative=-" in text

    code = changes_module.run_change_alternative_command(
        ChangeAlternativeArgs(
            change_id=CHANGE_ID,
            decision_id="adr:nope",
            instruction="x",
            comment_ids=(),
            json_output=True,
        ),
        session_factory=session_factory,
        repository=repository,
    )
    assert code == EXIT_INVALID_INPUT
    assert "not among the ADRs" in json.loads(capsys.readouterr().out)["detail"]

    code = changes_module.run_change_alternative_command(
        ChangeAlternativeArgs(
            change_id=CHANGE_ID,
            decision_id="adr:example-product:0001",
            instruction="consider a client-side timeout",
            comment_ids=(),
            json_output=True,
        ),
        session_factory=session_factory,
        repository=repository,
    )
    assert code == EXIT_OK
    out = _json_out(capsys)
    order = out["rework_order"]
    assert out["outcome"] == "created"
    assert order["phase"] == "architecture" and order["status"] == "pending"  # type: ignore[index]
    assert order["decision_ids"] == ["adr:example-product:0001"]  # type: ignore[index]
    assert order["revisions"][ADR] == head  # type: ignore[index]
    assert out["guidance"]["headline"]  # type: ignore[index]

    # A second request while the first is pending is refused (exit 2); the card needs revision.
    code = changes_module.run_change_alternative_command(
        ChangeAlternativeArgs(
            change_id=CHANGE_ID,
            decision_id="adr:example-product:0001",
            instruction="again",
            comment_ids=(),
            json_output=True,
        ),
        session_factory=session_factory,
        repository=repository,
    )
    assert code == EXIT_INVALID_INPUT
    capsys.readouterr()
    code = changes_module.run_change_decisions_command(
        ChangeDecisionsArgs(change_id=CHANGE_ID, json_output=True),
        session_factory=session_factory,
        repository=repository,
    )
    assert code == EXIT_OK
    card = _json_out(capsys)["decisions"]["decisions"][0]  # type: ignore[index]
    assert card["status"] == "needs_revision"
    assert card["pending_alternative"]["id"] == order["id"]  # type: ignore[index]

    code = changes_module.run_change_ui_command(
        ChangeUiArgs(change_id=CHANGE_ID, section=None, json_output=True),
        session_factory=session_factory,
        repository=repository,
    )
    assert code == EXIT_OK
    ui = _json_out(capsys)
    assert ui["dev_url"] == "https://dev.calc.test" and ui["revision"] == head
    screens = ui["screens"]
    assert isinstance(screens, list) and [s["id"] for s in screens] == ["SCR-001"]
    assert screens[0]["preview_url"] == "https://dev.calc.test/checkout/confirm"
    code = changes_module.run_change_ui_command(
        ChangeUiArgs(change_id=CHANGE_ID, section=UiSection.LINKS, json_output=True),
        session_factory=session_factory,
        repository=repository,
    )
    assert code == EXIT_OK
    links_only = _json_out(capsys)
    assert set(links_only) == {"change_id", "revision", "dev_url", "errors", "links"}
    code = changes_module.run_change_ui_command(
        ChangeUiArgs(change_id=CHANGE_ID, section=UiSection.SCREENS, json_output=False),
        session_factory=session_factory,
        repository=repository,
    )
    assert code == EXIT_OK
    text = capsys.readouterr().out
    assert "SCR-001" in text and "EL-retry" in text and "SCN-001" not in text


def test_unknown_change_and_missing_repository_are_invalid_input(
    session_factory: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    repository, _head = _prepare(session_factory)
    capsys.readouterr()
    codes = (
        changes_module.run_change_decisions_command(
            ChangeDecisionsArgs(change_id="chg_missing", json_output=True),
            session_factory=session_factory,
            repository=repository,
        ),
        changes_module.run_change_ui_command(
            ChangeUiArgs(change_id="chg_missing", section=None, json_output=True),
            session_factory=session_factory,
            repository=repository,
        ),
        changes_module.run_change_alternative_command(
            ChangeAlternativeArgs(
                change_id="chg_missing",
                decision_id="adr:x",
                instruction="x",
                comment_ids=(),
                json_output=True,
            ),
            session_factory=session_factory,
            repository=repository,
        ),
    )
    assert codes == (EXIT_INVALID_INPUT,) * 3
    reports = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    assert [r["detail"] for r in reports] == ["unknown change 'chg_missing'"] * 3
    assert (
        changes_module.run_change_decisions_command(
            ChangeDecisionsArgs(change_id=CHANGE_ID, json_output=True),
            session_factory=session_factory,
            repository=None,
        )
        == EXIT_INVALID_INPUT
    )
    assert "not configured" in json.loads(capsys.readouterr().out)["detail"]
    assert (
        changes_module.run_change_alternative_command(
            ChangeAlternativeArgs(
                change_id=CHANGE_ID,
                decision_id="adr:x",
                instruction="  ",
                comment_ids=(),
                json_output=True,
            ),
            session_factory=session_factory,
            repository=repository,
        )
        == EXIT_INVALID_INPUT
    )
