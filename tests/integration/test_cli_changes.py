"""``factory change create|status`` against PostgreSQL (T073, T071, T074).

Requires ``DARK_FACTORY_TEST_DATABASE_URL``; skipped without it. The product
comes from the real registry, the change lands in intake with its brief,
scenario and limit, and the guidance printed by the CLI is the one the API
computes for the same state.
"""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

import dark_factory.cli.changes as changes_module
import dark_factory.cli.products as products_module
from dark_factory.adapters.fakes import FakeRepository
from dark_factory.changes.conversations import ArtifactAnchor, Question
from dark_factory.changes.enums import AnswerKind, Phase, Provider, RiskClass, Scenario
from dark_factory.changes.refs import RepositoryRef
from dark_factory.cli.main import (
    EXIT_ERROR,
    EXIT_INVALID_INPUT,
    EXIT_OK,
    ArtifactAction,
    ChangeAnswerArgs,
    ChangeApproveArgs,
    ChangeArtifactsArgs,
    ChangeCommentArgs,
    ChangeCreateArgs,
    ChangeReworkArgs,
    ChangeStatusArgs,
    ProductAddArgs,
)
from dark_factory.orchestration.stages.agent import branch_name
from dark_factory.orchestration.state.change_store import ChangeRepository
from dark_factory.orchestration.state.conversation_store import ConversationRepository
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


# --- M2: the discussion cycle from the CLI (T086, T087) --------------------------------


REPO = RepositoryRef(provider=Provider.GITHUB, slug="small/calculator")
REQ = ".factory/changes/2026/CHG-0001-percent/spec/requirements/REQ-001-percent.md"
V1 = (
    "---\nschema: dark-factory.dev/requirement/v1\nid: req:calc:percent\ntype: requirement\n"
    "title: Percent\nproduct: calc\nstatus: proposed\nchange: chg_calc_0001\n---\n\n"
    "# Percent\n\n- AC-1: pressing % divides by 100\n- AC-2: rounding is half up\n"
)


def _seeded_repository(change_id: str) -> tuple[FakeRepository, str]:
    repository = FakeRepository()
    branch = branch_name(change_id)
    asyncio.run(repository.ensure_branch(REPO, "main", from_revision="base", idempotency_key="m"))
    asyncio.run(repository.ensure_branch(REPO, branch, from_revision="base", idempotency_key="b"))
    head = asyncio.run(
        repository.publish_commit(
            REPO, branch, {REQ: V1.encode()}, message="spec v1", idempotency_key="c1"
        )
    )
    return repository, head


def _seed_question(session_factory: sessionmaker[Session], change_id: str, head: str) -> str:
    question = Question(
        id="q_seed",
        change_id=change_id,
        phase=Phase.REQUIREMENTS,
        text="Round half up or half even?",
        kind=AnswerKind.CHOICE,
        options=("half up", "half even"),
        anchor=ArtifactAnchor(artifact=REQ, anchor_id="AC-2", revision=head),
    )
    with session_scope(session_factory) as session:
        ConversationRepository(session).add_question(question)
    return question.id


def _json_out(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    return dict(json.loads(capsys.readouterr().out.strip().splitlines()[-1]))


def test_answer_comment_rework_and_approve_from_the_cli(
    session_factory: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    _add_product(session_factory)
    assert (
        changes_module.run_change_create_command(_create_args(), session_factory=session_factory)
        == EXIT_OK
    )
    capsys.readouterr()
    repository, head = _seeded_repository("chg_calc_0001")
    question_id = _seed_question(session_factory, "chg_calc_0001", head)

    # status lists the open question and the closed gate (blocking question).
    code = changes_module.run_change_status_command(
        ChangeStatusArgs(change_id="chg_calc_0001", json_output=True),
        session_factory=session_factory,
        repository=repository,
    )
    assert code == EXIT_OK
    status = _json_out(capsys)
    assert [q["id"] for q in status["questions"]] == [question_id]

    # An answer outside the options is invalid input; a valid one is recorded.
    code = changes_module.run_change_answer_command(
        ChangeAnswerArgs(
            change_id="chg_calc_0001",
            question_id=question_id,
            value="banker's",
            comment=None,
            json_output=True,
        ),
        session_factory=session_factory,
        repository=repository,
    )
    assert code == EXIT_INVALID_INPUT
    capsys.readouterr()
    code = changes_module.run_change_answer_command(
        ChangeAnswerArgs(
            change_id="chg_calc_0001",
            question_id=question_id,
            value="half up",
            comment="as in the shop",
            json_output=True,
        ),
        session_factory=session_factory,
        repository=repository,
    )
    assert code == EXIT_OK
    answered = _json_out(capsys)
    assert answered["outcome"] == "answered"
    assert answered["question"]["status"] == "answered"

    # A comment binds to the head revision; alone it does not start rework.
    code = changes_module.run_change_comment_command(
        ChangeCommentArgs(
            change_id="chg_calc_0001",
            artifact=REQ,
            anchor_id="AC-1",
            body="make it testable",
            phase=Phase.REQUIREMENTS,
            json_output=True,
        ),
        session_factory=session_factory,
        repository=repository,
    )
    assert code == EXIT_OK
    comment = _json_out(capsys)["comment"]
    assert comment["anchor"]["revision"] == head

    # The send-back creates the pending order and records the rejected decision.
    code = changes_module.run_change_rework_command(
        ChangeReworkArgs(
            change_id="chg_calc_0001",
            phase=Phase.REQUIREMENTS,
            comment_ids=(comment["id"],),
            question_ids=(question_id,),
            instruction="tighten",
            json_output=True,
        ),
        session_factory=session_factory,
        repository=repository,
    )
    assert code == EXIT_OK
    order = _json_out(capsys)["rework_order"]
    assert order["status"] == "pending" and order["revisions"][REQ] == head
    # A second send-back while one is pending is refused; approval is closed too.
    code = changes_module.run_change_rework_command(
        ChangeReworkArgs(
            change_id="chg_calc_0001",
            phase=Phase.REQUIREMENTS,
            comment_ids=(),
            question_ids=(),
            instruction="again",
            json_output=True,
        ),
        session_factory=session_factory,
        repository=repository,
    )
    assert code == EXIT_INVALID_INPUT
    capsys.readouterr()
    code = changes_module.run_change_approve_command(
        ChangeApproveArgs(
            change_id="chg_calc_0001",
            phase=Phase.REQUIREMENTS,
            waive=False,
            comment=None,
            revision=None,
            json_output=True,
        ),
        session_factory=session_factory,
        repository=repository,
    )
    assert code == EXIT_INVALID_INPUT
    assert "раунд ещё не запущен" in json.loads(capsys.readouterr().out)["detail"]


def test_approve_binds_to_the_current_revision_and_artifacts_commands_read_git(
    session_factory: sessionmaker[Session], capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _add_product(session_factory)
    assert (
        changes_module.run_change_create_command(_create_args(), session_factory=session_factory)
        == EXIT_OK
    )
    capsys.readouterr()
    repository, head = _seeded_repository("chg_calc_0001")

    code = changes_module.run_change_approve_command(
        ChangeApproveArgs(
            change_id="chg_calc_0001",
            phase=Phase.REQUIREMENTS,
            waive=False,
            comment="ok",
            revision=None,
            json_output=True,
        ),
        session_factory=session_factory,
        repository=repository,
    )
    assert code == EXIT_OK
    approved = _json_out(capsys)
    assert approved["decision"]["commit_sha"] == head
    assert approved["phase_gate"]["approved"] is True

    # artifacts: list, show, edit (a new revision makes the approval stale), versions, diff.
    code = changes_module.run_change_artifacts_command(
        ChangeArtifactsArgs(
            change_id="chg_calc_0001",
            action=ArtifactAction.LIST,
            path=None,
            revision=None,
            from_revision=None,
            to_revision=None,
            file=None,
            base_revision=None,
            json_output=False,
        ),
        session_factory=session_factory,
        repository=repository,
    )
    assert code == EXIT_OK
    assert REQ in capsys.readouterr().out
    code = changes_module.run_change_artifacts_command(
        ChangeArtifactsArgs(
            change_id="chg_calc_0001",
            action=ArtifactAction.SHOW,
            path=REQ,
            revision=None,
            from_revision=None,
            to_revision=None,
            file=None,
            base_revision=None,
            json_output=False,
        ),
        session_factory=session_factory,
        repository=repository,
    )
    assert code == EXIT_OK and capsys.readouterr().out == V1
    edited = tmp_path / "req.md"
    edited.write_text(
        V1.replace("- AC-2: rounding is half up\n", "- AC-2: rounding is half even\n"),
        encoding="utf-8",
    )
    code = changes_module.run_change_artifacts_command(
        ChangeArtifactsArgs(
            change_id="chg_calc_0001",
            action=ArtifactAction.EDIT,
            path=REQ,
            revision=None,
            from_revision=None,
            to_revision=None,
            file=str(edited),
            base_revision=head,
            json_output=True,
        ),
        session_factory=session_factory,
        repository=repository,
    )
    assert code == EXIT_OK
    written = _json_out(capsys)
    assert written["outcome"] == "created" and written["previous_revision"] == head
    new_revision = str(written["revision"])
    assert written["guidance"]["headline"], "every command ends with the next step"
    code = changes_module.run_change_artifacts_command(
        ChangeArtifactsArgs(
            change_id="chg_calc_0001",
            action=ArtifactAction.VERSIONS,
            path=REQ,
            revision=None,
            from_revision=None,
            to_revision=None,
            file=None,
            base_revision=None,
            json_output=True,
        ),
        session_factory=session_factory,
        repository=repository,
    )
    assert code == EXIT_OK
    versions = json.loads(capsys.readouterr().out)
    assert [v["revision"] for v in versions] == [new_revision, head]
    code = changes_module.run_change_artifacts_command(
        ChangeArtifactsArgs(
            change_id="chg_calc_0001",
            action=ArtifactAction.DIFF,
            path=REQ,
            revision=None,
            from_revision=head,
            to_revision=new_revision,
            file=None,
            base_revision=None,
            json_output=False,
        ),
        session_factory=session_factory,
        repository=repository,
    )
    assert code == EXIT_OK and "+- AC-2: rounding is half even" in capsys.readouterr().out
    # The approval is stale now: status says so through the gate; a conflicting edit is exit 1.
    code = changes_module.run_change_status_command(
        ChangeStatusArgs(change_id="chg_calc_0001", json_output=True),
        session_factory=session_factory,
        repository=repository,
    )
    assert code == EXIT_OK
    status = _json_out(capsys)
    assert status["phase_gate"]["approved"] is False
    assert status["phase_gate"]["approvals"][0]["state"] == "stale"
    code = changes_module.run_change_artifacts_command(
        ChangeArtifactsArgs(
            change_id="chg_calc_0001",
            action=ArtifactAction.EDIT,
            path=REQ,
            revision=None,
            from_revision=None,
            to_revision=None,
            file=str(edited),
            base_revision="base",
            json_output=False,
        ),
        session_factory=session_factory,
        repository=repository,
    )
    # Same content as the head: an unchanged save is a no-op, not a conflict.
    assert code == EXIT_OK
    edited.write_text("conflicting", encoding="utf-8")
    code = changes_module.run_change_artifacts_command(
        ChangeArtifactsArgs(
            change_id="chg_calc_0001",
            action=ArtifactAction.EDIT,
            path=REQ,
            revision=None,
            from_revision=None,
            to_revision=None,
            file=str(edited),
            base_revision=head,
            json_output=False,
        ),
        session_factory=session_factory,
        repository=repository,
    )
    assert code == EXIT_ERROR
    # Without the repository seam the artifact commands refuse before the store.
    code = changes_module.run_change_artifacts_command(
        ChangeArtifactsArgs(
            change_id="chg_calc_0001",
            action=ArtifactAction.LIST,
            path=None,
            revision=None,
            from_revision=None,
            to_revision=None,
            file=None,
            base_revision=None,
            json_output=False,
        ),
        session_factory=session_factory,
    )
    assert code == EXIT_INVALID_INPUT
