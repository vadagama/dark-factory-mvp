"""``Guidance`` — the operator's next step as a deterministic read model (T074, ADR-033).

The projection must cover every state (ADR-033, risks): the tests enumerate
``ProductStatus``, the intake states of a change and ``RunStatus`` by the
waiting reasons, and hold the four rules of ADR-033 p.4 on each result —
exactly one primary action, a stage-specific label, every disabled action
carries a reason, every blocker names who removes it.
"""

from decimal import Decimal

import pytest

from dark_factory.changes.enums import (
    BriefAuthor,
    Gate,
    Phase,
    ProductStatus,
    Provider,
    RunStatus,
    Scenario,
    Stage,
    StageStatus,
    StopOutcome,
)
from dark_factory.changes.intake import IntakeBrief, SpendLimit
from dark_factory.changes.next_action import (
    ExecuteStageAction,
    MergeAction,
    NextAction,
    PhaseRoundAction,
    ReleaseAction,
    RequestApprovalAction,
    ReworkAction,
    StopAction,
    WaitForCIAction,
    WaitForInputAction,
)
from dark_factory.changes.product import Product
from dark_factory.changes.refs import ChangeRequestRef, RepositoryRef
from dark_factory.changes.run import Change, ChangeRun, StageRun
from dark_factory.changes.usage import BudgetSnapshot
from dark_factory.orchestration.guidance import (
    Guidance,
    GuidanceActor,
    GuidancePhase,
    change_guidance,
    phase_of_run,
    product_guidance,
    spend_forecast,
)
from dark_factory.orchestration.phase_gate import ApprovalView, GateReason, PhaseGate
from tests.changes_factories import make_change, make_product

COMPLETE_BRIEF = IntakeBrief(problem="login is slow", goal="p95 < 300 ms")


def _product(status: ProductStatus, reason: str | None = None) -> Product:
    product = make_product()
    if status is not ProductStatus.CREATED:
        product.apply_status(ProductStatus.VALIDATING)
        if status is not ProductStatus.VALIDATING:
            product.apply_status(status, reason=reason)
    return product


def _change(**update: object) -> Change:
    base = make_change().model_copy(
        update={
            "product_id": "prd-001",
            "brief": COMPLETE_BRIEF,
            "spend_limit": SpendLimit(cost_budget_usd=Decimal("20")),
        }
    )
    return base.model_copy(update=update)


def _run(
    status: RunStatus,
    *,
    stage: Stage = Stage.SPECIFICATION,
    stage_status: StageStatus = StageStatus.IN_PROGRESS,
    budget: BudgetSnapshot | None = None,
) -> ChangeRun:
    return ChangeRun(
        id="run-1",
        change_id="chg-001",
        route="standard",
        provider=Provider.GITHUB,
        status=status,
        stages=[StageRun(id="st-1", stage=stage, status=stage_status)],
        budget=budget if budget is not None else BudgetSnapshot(),
    )


def _holds_the_rules(guidance: Guidance) -> None:
    assert guidance.headline
    assert guidance.why
    assert guidance.primary.label, "exactly one primary action, with a label"
    assert guidance.primary.label not in {"Approve", "OK", "Далее"}, "stage-specific label"
    for action in (guidance.primary, *guidance.secondary):
        if not action.enabled:
            assert action.reason, "a disabled action explains itself (no dead ends)"
    for blocker in guidance.blockers:
        assert blocker.what and blocker.how
        assert blocker.who in GuidanceActor


# --- product --------------------------------------------------------------------


@pytest.mark.parametrize("status", list(ProductStatus))
def test_product_guidance_covers_every_status(status: ProductStatus) -> None:
    product = _product(
        status, reason="the repository is not available" if status is ProductStatus.ERROR else None
    )
    guidance = product_guidance(product)
    _holds_the_rules(guidance)
    assert guidance.subject.kind == "product"
    assert guidance.subject.id == product.id
    match status:
        case ProductStatus.CREATED:
            assert guidance.primary.cli == "factory product validate --id prd-001"
            assert guidance.primary.api == "POST /products/prd-001/validate"
        case ProductStatus.VALIDATING:
            assert guidance.blockers[0].who is GuidanceActor.FACTORY
        case ProductStatus.ERROR:
            assert "the repository is not available" in guidance.why
            assert guidance.blockers[0].what == "the repository is not available"
            assert guidance.primary.label == "Повторить проверку"
        case ProductStatus.READY:
            assert guidance.primary.label == "Новая фича"
            assert guidance.primary.api == "POST /changes"
            assert guidance.blockers == ()


def test_ready_product_mentions_the_open_changes() -> None:
    guidance = product_guidance(_product(ProductStatus.READY), open_changes=2)
    assert "задач у продукта: 2" in guidance.why


# --- intake ---------------------------------------------------------------------


def test_a_complete_brief_on_a_ready_product_offers_to_start_requirements() -> None:
    change = _change()
    guidance = change_guidance(change, product=_product(ProductStatus.READY), run=None)
    _holds_the_rules(guidance)
    assert guidance.phase is GuidancePhase.INITIATIVE
    assert guidance.headline == "Задача готова к фазе «Требования»"
    assert guidance.primary.enabled
    assert guidance.primary.cli == f"factory run advance --change-id {change.id}"
    assert "лимит 20 USD" in guidance.why
    assert "сценарий full" in guidance.why
    assert guidance.blockers == ()


def test_a_specs_only_scenario_is_named_in_the_why() -> None:
    change = _change(scenario=Scenario.SPECS_ONLY)
    guidance = change_guidance(change, product=_product(ProductStatus.READY), run=None)
    assert "specs-only" in guidance.why


@pytest.mark.parametrize(
    "brief",
    [
        None,
        IntakeBrief(problem="only the problem"),
        IntakeBrief(source_text="x", error="harness call failed: TimeoutError"),
    ],
)
def test_a_missing_or_draft_brief_asks_to_complete_it(brief: IntakeBrief | None) -> None:
    change = _change(brief=brief)
    guidance = change_guidance(change, product=_product(ProductStatus.READY), run=None)
    _holds_the_rules(guidance)
    assert guidance.primary.api == f"PUT /changes/{change.id}/brief"
    assert guidance.blockers[0].who is GuidanceActor.OPERATOR
    start = guidance.secondary[0]
    assert not start.enabled and start.reason
    if brief is not None and brief.error is not None:
        assert "TimeoutError" in guidance.why, "the agent failure is observable"


def test_an_agent_formulated_brief_is_credited() -> None:
    change = _change(brief=COMPLETE_BRIEF.model_copy(update={"formulated_by": BriefAuthor.AGENT}))
    guidance = change_guidance(change, product=_product(ProductStatus.READY), run=None)
    assert "сформулирован агентом" in guidance.why


@pytest.mark.parametrize(
    "status", [ProductStatus.CREATED, ProductStatus.VALIDATING, ProductStatus.ERROR]
)
def test_a_product_that_is_not_ready_blocks_the_start(status: ProductStatus) -> None:
    product = _product(status, reason="unreachable" if status is ProductStatus.ERROR else None)
    guidance = change_guidance(_change(), product=product, run=None)
    _holds_the_rules(guidance)
    assert not guidance.primary.enabled
    assert guidance.primary.reason and "не готов" in guidance.primary.reason
    assert (
        guidance.blockers[0].how == "Проверьте репозиторий: factory product validate --id prd-001."
    )


def test_a_change_without_a_product_is_blocked_on_registration() -> None:
    guidance = change_guidance(_change(product_id=None), product=None, run=None)
    _holds_the_rules(guidance)
    assert not guidance.primary.enabled
    assert "factory product add" in guidance.blockers[0].how


# --- run -----------------------------------------------------------------------------


_WAITING_REASONS: list[NextAction | None] = [
    RequestApprovalAction(gate=Gate.SPECIFICATION, reason="requirements ready"),
    WaitForCIAction(
        reason="CI is running",
        change_request=ChangeRequestRef(
            repository=RepositoryRef(provider=Provider.GITHUB, slug="org/repo"),
            number=7,
            url="https://github.com/org/repo/pull/7",
            status="open",
        ),
    ),
    WaitForCIAction(reason="CI is running"),
    WaitForInputAction(reason="choose the storage engine"),
    ReworkAction(round=2, max_rounds=3, reason="blocker findings"),
    MergeAction(
        change_request=ChangeRequestRef(
            repository=RepositoryRef(provider=Provider.GITHUB, slug="org/repo"),
            number=8,
            status="open",
        )
    ),
    ReleaseAction(target_environment="dev", reason="promoted"),
    ExecuteStageAction(next_stage=Stage.PLANNING),
    StopAction(outcome=StopOutcome.BLOCKED, reason="stale"),
    None,
]


@pytest.mark.parametrize("status", list(RunStatus))
@pytest.mark.parametrize(
    "waiting_on", _WAITING_REASONS, ids=lambda a: type(a).__name__ if a else "none"
)
def test_run_guidance_is_exhaustive_over_status_and_waiting_reason(
    status: RunStatus, waiting_on: NextAction | None
) -> None:
    run = _run(
        status,
        stage_status=StageStatus.WAITING
        if status is RunStatus.WAITING
        else StageStatus.IN_PROGRESS,
    )
    guidance = change_guidance(
        _change(), product=_product(ProductStatus.READY), run=run, waiting_on=waiting_on
    )
    _holds_the_rules(guidance)
    assert guidance.subject.kind == "change"
    if status is RunStatus.SUCCEEDED:
        assert guidance.phase is GuidancePhase.DONE
    else:
        assert guidance.phase is GuidancePhase.REQUIREMENTS


def test_an_approval_gate_is_the_primary_action() -> None:
    run = _run(RunStatus.WAITING, stage_status=StageStatus.WAITING)
    guidance = change_guidance(
        _change(),
        product=_product(ProductStatus.READY),
        run=run,
        waiting_on=RequestApprovalAction(gate=Gate.SPECIFICATION),
    )
    assert guidance.headline == "Фаза «Требования» готова к согласованию"
    assert guidance.primary.label == "Согласовать требования"
    assert guidance.primary.api == "POST /changes/chg-001/approvals"
    assert guidance.blockers[0].who is GuidanceActor.OPERATOR


def test_ci_wait_is_attributed_to_ci_with_the_change_request() -> None:
    guidance = change_guidance(
        _change(),
        product=_product(ProductStatus.READY),
        run=_run(RunStatus.WAITING, stage=Stage.CONSTRUCTION, stage_status=StageStatus.WAITING),
        waiting_on=_WAITING_REASONS[1],
    )
    assert guidance.phase is GuidancePhase.EXECUTION
    assert guidance.blockers[0].who is GuidanceActor.CI
    assert "CR #7 (https://github.com/org/repo/pull/7)" in guidance.blockers[0].how


def test_a_blocked_run_carries_the_stop_reason() -> None:
    guidance = change_guidance(
        _change(),
        product=_product(ProductStatus.READY),
        run=_run(RunStatus.BLOCKED, stage_status=StageStatus.BLOCKED),
        waiting_on=StopAction(
            outcome=StopOutcome.BLOCKED, reason="implementation contract is not approved"
        ),
    )
    assert guidance.headline == "Фаза «Требования» заблокирована"
    assert "implementation contract is not approved" in guidance.blockers[0].what
    assert guidance.primary.cli == "factory run advance --run-id run-1"


def test_a_spent_limit_stops_everything_else() -> None:
    budget = BudgetSnapshot(cost_budget=Decimal("20"), cost_used=Decimal("20.5"))
    guidance = change_guidance(
        _change(),
        product=_product(ProductStatus.READY),
        run=_run(RunStatus.WAITING, stage_status=StageStatus.WAITING, budget=budget),
        waiting_on=RequestApprovalAction(gate=Gate.SPECIFICATION),
    )
    assert guidance.headline == "Лимит расхода исчерпан"
    assert guidance.primary.label == "Снять запуск"
    assert guidance.primary.api == "POST /runs/run-1/withdraw"
    assert "20.5 из 20 USD" in guidance.blockers[0].what


def test_exhausted_rework_rounds_are_a_named_blocker() -> None:
    budget = BudgetSnapshot(max_rework_rounds=3, used_rework_rounds=3)
    guidance = change_guidance(
        _change(),
        product=_product(ProductStatus.READY),
        run=_run(
            RunStatus.WAITING,
            stage=Stage.REVIEW_VERIFICATION,
            stage_status=StageStatus.WAITING,
            budget=budget,
        ),
        waiting_on=WaitForInputAction(reason="decide"),
    )
    assert any("раунды доработки: 3 из 3" in blocker.what for blocker in guidance.blockers)


def test_a_canceled_run_offers_a_new_change() -> None:
    guidance = change_guidance(
        _change(),
        product=_product(ProductStatus.READY),
        run=_run(RunStatus.CANCELED, stage_status=StageStatus.CANCELED),
    )
    assert guidance.headline == "Запуск снят оператором"
    assert guidance.primary.cli == "factory change create --product prd-001 …"


def test_a_succeeded_specs_only_change_is_done() -> None:
    guidance = change_guidance(
        _change(scenario=Scenario.SPECS_ONLY),
        product=_product(ProductStatus.READY),
        run=_run(RunStatus.SUCCEEDED, stage_status=StageStatus.SUCCEEDED),
    )
    assert guidance.phase is GuidancePhase.DONE
    assert "specs-only" in guidance.why


# --- phases and forecast ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("stage", "phase"),
    [
        (Stage.SPECIFICATION, GuidancePhase.REQUIREMENTS),
        (Stage.PLANNING, GuidancePhase.PLAN),
        (Stage.CONSTRUCTION, GuidancePhase.EXECUTION),
        (Stage.REVIEW_VERIFICATION, GuidancePhase.DEMONSTRATION),
        (Stage.RELEASE, GuidancePhase.DELIVERY),
    ],
)
def test_phase_of_run_follows_the_adr_032_table(stage: Stage, phase: GuidancePhase) -> None:
    assert phase_of_run(_run(RunStatus.RUNNING, stage=stage)) is phase


def test_phase_of_run_picks_the_first_active_stage() -> None:
    run = ChangeRun(
        id="run-1",
        change_id="chg-001",
        route="standard",
        provider=Provider.GITHUB,
        status=RunStatus.WAITING,
        stages=[
            StageRun(id="s1", stage=Stage.SPECIFICATION, status=StageStatus.SUCCEEDED),
            StageRun(id="s2", stage=Stage.PLANNING, status=StageStatus.WAITING),
            StageRun(id="s3", stage=Stage.CONSTRUCTION, status=StageStatus.PENDING),
        ],
    )
    assert phase_of_run(run) is GuidancePhase.PLAN
    assert phase_of_run(None) is GuidancePhase.INITIATIVE


def test_spend_forecast_is_the_remaining_limit() -> None:
    change = _change()
    assert spend_forecast(change, None) == Decimal("20")
    run = _run(RunStatus.RUNNING, budget=BudgetSnapshot(cost_used=Decimal("4.25")))
    assert spend_forecast(change, run) == Decimal("15.75")
    assert spend_forecast(_change(spend_limit=None), run) is None


# --- phase gate (T087) -----------------------------------------------------------------


def _gate(**overrides: object) -> PhaseGate:
    fields: dict[str, object] = {
        "change_id": "chg-001",
        "phase": Phase.REQUIREMENTS,
        "gate": Gate.SPECIFICATION,
        "available": True,
        "current_revision": "r2",
    }
    fields.update(overrides)
    return PhaseGate.model_validate(fields)


def _waiting_run() -> ChangeRun:
    return _run(RunStatus.WAITING, stage_status=StageStatus.WAITING)


def test_an_available_gate_renders_the_approval_with_the_send_back_as_secondary() -> None:
    guidance = change_guidance(
        _change(),
        product=_product(ProductStatus.READY),
        run=_waiting_run(),
        waiting_on=WaitForInputAction(reason="spec produced"),
        phase_gate=_gate(open_comments=1),
    )
    _holds_the_rules(guidance)
    assert guidance.headline == "Фаза «Требования» готова к согласованию"
    assert guidance.primary.label == "Согласовать требования"
    assert guidance.primary.enabled
    assert guidance.primary.cli == "factory change approve --id chg-001 --phase requirements"
    assert guidance.secondary[0].label == "На доработку"
    assert "открытых замечаний: 1" in guidance.why


def test_blocking_questions_close_the_gate_and_lead_to_the_questions() -> None:
    guidance = change_guidance(
        _change(),
        product=_product(ProductStatus.READY),
        run=_waiting_run(),
        waiting_on=RequestApprovalAction(gate=Gate.SPECIFICATION),
        phase_gate=_gate(
            available=False,
            reasons=(
                GateReason(what="Блокирующих вопросов без ответа: 2 (q1, q2)", how="Ответьте"),
            ),
            blocking_questions=2,
            open_questions=2,
        ),
    )
    _holds_the_rules(guidance)
    assert guidance.headline == "Фаза «Требования»: нужно решение, гейт пока закрыт"
    assert guidance.primary.label == "Ответить на вопросы"
    assert guidance.primary.api == "GET /changes/chg-001/questions?status=open"
    assert guidance.blockers[0].what.startswith("Блокирующих вопросов")
    assert guidance.blockers[0].who is GuidanceActor.OPERATOR
    assert "Согласовать" not in [action.label for action in guidance.secondary]


def test_a_closed_gate_without_questions_keeps_the_approval_visible_but_disabled() -> None:
    guidance = change_guidance(
        _change(),
        product=_product(ProductStatus.READY),
        run=_waiting_run(),
        waiting_on=RequestApprovalAction(gate=Gate.SPECIFICATION),
        phase_gate=_gate(
            available=False,
            reasons=(
                GateReason(what="Отправлено на доработку: раунд ещё не запущен", how="Запустите"),
            ),
            rework_pending=True,
        ),
    )
    _holds_the_rules(guidance)
    assert guidance.primary.label == "Согласовать требования"
    assert not guidance.primary.enabled
    assert guidance.primary.reason == "Отправлено на доработку: раунд ещё не запущен"


def test_a_current_approval_moves_on_and_a_stale_one_is_counted() -> None:
    approved = change_guidance(
        _change(),
        product=_product(ProductStatus.READY),
        run=_waiting_run(),
        waiting_on=RequestApprovalAction(gate=Gate.SPECIFICATION),
        phase_gate=_gate(
            approved=True,
            approvals=(
                ApprovalView(decision_id="d1", outcome="approved", revision="r1", state="stale"),
                ApprovalView(decision_id="d2", outcome="approved", revision="r2", state="current"),
            ),
        ),
    )
    _holds_the_rules(approved)
    assert approved.headline == "Фаза «Требования» согласована на текущей ревизии"
    assert approved.primary.label == "Продолжить после согласования"
    assert "прежних согласований неактуально: 1" in approved.why


def test_a_running_rework_round_names_itself_on_a_running_run() -> None:
    guidance = change_guidance(
        _change(),
        product=_product(ProductStatus.READY),
        run=_run(RunStatus.RUNNING, stage_status=StageStatus.IN_PROGRESS),
        phase_gate=_gate(available=False, rework_in_progress=True, rework_rounds_used=1),
    )
    _holds_the_rules(guidance)
    assert guidance.headline == "Доработка «Требования»: раунд 1 из 3"
    assert guidance.primary.label == "Выполнить раунд доработки"
    assert guidance.primary.cli == "factory run advance --change-id chg-001"


# --- M3 (ADR-039): the design rounds of the specification stage -------------------------


def test_the_architecture_phase_offers_the_alternative_request() -> None:
    guidance = change_guidance(
        _change(),
        product=_product(ProductStatus.READY),
        run=_waiting_run(),
        waiting_on=WaitForInputAction(reason="design produced"),
        phase_gate=_gate(phase=Phase.ARCHITECTURE, current_revision="d1"),
        phase=Phase.ARCHITECTURE,
    )
    _holds_the_rules(guidance)
    assert guidance.phase is Phase.ARCHITECTURE
    assert guidance.headline == "Фаза «Архитектура» готова к согласованию"
    assert guidance.primary.label == "Согласовать архитектуру"
    assert guidance.primary.cli == "factory change approve --id chg-001 --phase architecture"
    assert guidance.secondary[0].label == "Запросить альтернативу"
    assert guidance.secondary[0].api == "POST /changes/chg-001/decisions/{decision_id}/alternative"
    assert guidance.after is not None and "интерфейс" in guidance.after


def test_a_ui_free_change_asks_the_operator_to_confirm_the_skip() -> None:
    guidance = change_guidance(
        _change(),
        product=_product(ProductStatus.READY),
        run=_waiting_run(),
        waiting_on=WaitForInputAction(reason="design produced"),
        phase_gate=_gate(
            phase=Phase.INTERFACE,
            gate=Gate.UI,
            available=False,
            current_revision=None,
            reasons=[GateReason(what="UI не требуется", how="подтвердите")],
            ui_requirement={"required": False, "source": "agent", "reason": "backend-only"},
        ),
        phase=Phase.INTERFACE,
    )
    _holds_the_rules(guidance)
    assert guidance.headline == "Фаза «Интерфейс» не требуется: подтвердите пропуск"
    assert guidance.primary.label == "Подтвердить пропуск UI"
    assert guidance.primary.enabled
    assert "--phase interface --waive" in (guidance.primary.cli or "")
    assert "backend-only" in guidance.why
    assert guidance.after is not None and "план" in guidance.after.lower()


def test_a_waived_phase_reads_as_settled_and_continues() -> None:
    guidance = change_guidance(
        _change(),
        product=_product(ProductStatus.READY),
        run=_waiting_run(),
        waiting_on=WaitForInputAction(reason="design produced"),
        phase_gate=_gate(
            phase=Phase.INTERFACE,
            gate=Gate.UI,
            available=False,
            current_revision=None,
            waived=True,
            ui_requirement={"required": False, "source": "operator", "reason": "backend-only"},
        ),
        phase=Phase.INTERFACE,
    )
    _holds_the_rules(guidance)
    assert guidance.headline == "Фаза «Интерфейс» пропущена с основанием"
    assert guidance.primary.label == "Продолжить после согласования"
    assert guidance.primary.cli == "factory run advance --change-id chg-001"


def test_a_phase_round_wait_names_the_next_round() -> None:
    guidance = change_guidance(
        _change(),
        product=_product(ProductStatus.READY),
        run=_waiting_run(),
        waiting_on=PhaseRoundAction(phase=Phase.ARCHITECTURE, reason="requirements approved"),
        phase=Phase.REQUIREMENTS,
    )
    _holds_the_rules(guidance)
    assert guidance.headline == "Следующий раунд: «Архитектура»"
    assert guidance.primary.label == "Запустить раунд «Архитектура»"
    assert guidance.primary.cli == "factory run advance --change-id chg-001"


def test_the_known_phase_overrides_the_stage_projection() -> None:
    guidance = change_guidance(
        _change(),
        product=_product(ProductStatus.READY),
        run=_run(RunStatus.RUNNING),
        phase=Phase.INTERFACE,
    )
    assert guidance.phase is Phase.INTERFACE
    assert guidance.headline == "Идёт фаза «Интерфейс»"


def test_a_settled_earlier_round_asks_for_the_advance_not_for_a_decision() -> None:
    """M3 (ADR-039): the requirements checkpoint is parked, its approval is already recorded
    and the decisions read as the architecture phase — the next step is the advance that
    starts the architect's round, not «Согласовать архитектуру» (found on the M3 live run)."""
    guidance = change_guidance(
        _change(),
        product=_product(ProductStatus.READY),
        run=_waiting_run(),
        waiting_on=WaitForInputAction(reason="spec produced"),
        phase_gate=_gate(phase=Phase.ARCHITECTURE, current_revision=None, available=False),
        phase=Phase.ARCHITECTURE,
        waiting_phase=Phase.REQUIREMENTS,
    )
    _holds_the_rules(guidance)
    assert guidance.phase is Phase.ARCHITECTURE
    assert guidance.headline == "Следующий раунд: «Архитектура»"
    assert guidance.primary.label == "Запустить раунд «Архитектура»"
    assert guidance.primary.cli == "factory run advance --change-id chg-001"
    assert "Требования" in guidance.why


def test_the_waiting_round_of_the_current_phase_still_renders_its_gate() -> None:
    guidance = change_guidance(
        _change(),
        product=_product(ProductStatus.READY),
        run=_waiting_run(),
        waiting_on=WaitForInputAction(reason="spec produced"),
        phase_gate=_gate(),
        phase=Phase.REQUIREMENTS,
        waiting_phase=Phase.REQUIREMENTS,
    )
    assert guidance.primary.label == "Согласовать требования"
