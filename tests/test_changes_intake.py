"""Intake brief, scenario and spend limit of a change (T071, plan §2/§6)."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from dark_factory.changes import (
    BriefAuthor,
    BriefStatus,
    BudgetSnapshot,
    Change,
    IntakeBrief,
    Scenario,
    SpendLimit,
)
from tests.changes_factories import make_change


def test_a_brief_with_problem_and_goal_is_complete() -> None:
    brief = IntakeBrief(
        problem=" login is slow ", goal="p95 < 300 ms", constraints=("no new infra", "")
    )
    assert brief.status is BriefStatus.COMPLETE
    assert brief.is_complete
    assert brief.problem == "login is slow"
    assert brief.constraints == ("no new infra",), "blank lines are dropped, order kept"
    assert brief.missing == ()


@pytest.mark.parametrize(
    ("problem", "goal", "missing"),
    [
        (None, "goal", ("problem",)),
        ("problem", "   ", ("goal",)),
        ("", None, ("problem", "goal")),
    ],
)
def test_a_brief_without_problem_or_goal_is_a_draft(
    problem: str | None, goal: str | None, missing: tuple[str, ...]
) -> None:
    brief = IntakeBrief(problem=problem, goal=goal)
    assert brief.status is BriefStatus.DRAFT
    assert brief.missing == missing


def test_a_claimed_status_is_overridden_by_the_derived_one() -> None:
    # The wire may say "complete"; the fields decide (fail-closed on a bare claim).
    brief = IntakeBrief.model_validate({"status": "complete", "problem": "only a problem"})
    assert brief.status is BriefStatus.DRAFT
    ready = IntakeBrief.model_validate({"status": "draft", "problem": "p", "goal": "g"})
    assert ready.status is BriefStatus.COMPLETE


def test_the_brief_keeps_the_source_text_and_the_agent_error() -> None:
    brief = IntakeBrief(
        source_text="  make it faster  ",
        formulated_by=BriefAuthor.AGENT,
        error="harness call failed: TimeoutError",
    )
    assert brief.source_text == "make it faster"
    assert brief.formulated_by is BriefAuthor.AGENT
    assert brief.error == "harness call failed: TimeoutError"
    assert brief.status is BriefStatus.DRAFT


def test_the_spend_limit_feeds_the_run_budget() -> None:
    limit = SpendLimit(cost_budget_usd=Decimal("25.50"), token_budget=200_000)
    budget = limit.to_budget()
    assert budget.cost_budget == Decimal("25.50")
    assert budget.token_budget == 200_000
    assert budget.max_rework_rounds == BudgetSnapshot().max_rework_rounds, "rework rounds untouched"
    kept = limit.to_budget(BudgetSnapshot(max_rework_rounds=1, cost_used=Decimal("3")))
    assert kept.max_rework_rounds == 1
    assert kept.cost_used == Decimal("3")


@pytest.mark.parametrize("value", ["0", "-1", "1.00001"])
def test_the_spend_limit_rejects_non_positive_or_too_precise_amounts(value: str) -> None:
    with pytest.raises(ValidationError):
        SpendLimit(cost_budget_usd=Decimal(value))


def test_a_change_defaults_stay_backward_compatible() -> None:
    legacy = Change.model_validate(
        {
            "id": "chg-legacy",
            "title": "Legacy",
            "source": "tracker",
            "product": {"provider": "github", "slug": "org/repo"},
            "risk_class": "R1",
        }
    )
    assert legacy.brief is None
    assert legacy.scenario is Scenario.FULL
    assert legacy.spend_limit is None


def test_a_change_round_trips_the_intake_fields() -> None:
    change = make_change().model_copy(
        update={
            "brief": IntakeBrief(problem="p", goal="g", out_of_scope=("mobile",)),
            "scenario": Scenario.SPECS_ONLY,
            "spend_limit": SpendLimit(cost_budget_usd=Decimal("10")),
        }
    )
    restored = Change.model_validate(change.model_dump(mode="json"))
    assert restored == change
    assert restored.spend_limit is not None
    assert isinstance(restored.spend_limit.cost_budget_usd, Decimal)
    assert change.model_dump(mode="json")["spend_limit"]["cost_budget_usd"] == "10"
