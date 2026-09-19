"""``Guidance``: the operator's next step as a server-computed read model (T074, ADR-033).

The operator must never be left without a next step (plan §4). ``Guidance`` is
a deterministic projection of the ChangeSet state — product readiness, the
intake brief, the latest run, its waiting reason and its budget — into one
declarative block: what is ready (``headline``), what happened (``why``),
exactly one primary action with a stage-specific label, the secondary actions,
the blockers as ``{what, who, how}`` and what follows (``after``). The API
serves it, the CLI (``factory change status``) and the Console render it; no
interface computes a next step of its own (ADR-033 p.3).

``Guidance`` is a read model: it performs nothing and cannot unlock a forbidden
transition (ADR-033 p.2). Every action names the command or endpoint that
performs it; an action the state does not allow is still listed, ``enabled``
false, with the ``reason`` — there are no dead ends (ADR-033 p.4). It also does
not replace ``NextAction``: the stage's technical continuation is an *input*
here, the operator's step is the output (ADR-033 p.5).

M1 scope (T074): the product states, the intake states of a change and the run
states of a single run — including the waiting reason, ``blocked``, a spent
limit and exhausted rework rounds. The phase is projected from the stage per
ADR-032; the split of architecture and interface inside ``specification`` is
T098 and stays ``requirements`` here.
"""

from decimal import Decimal
from enum import StrEnum
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict

from dark_factory.changes.enums import Gate, Phase, ProductStatus, RunStatus, Stage, StageStatus
from dark_factory.changes.next_action import (
    ExecuteStageAction,
    MergeAction,
    NextAction,
    ReleaseAction,
    RequestApprovalAction,
    ReworkAction,
    StopAction,
    WaitForCIAction,
    WaitForInputAction,
)
from dark_factory.changes.product import Product
from dark_factory.changes.run import Change, ChangeRun
from dark_factory.orchestration.phase_gate import PhaseGate

__all__ = [
    "GUIDANCE_SCHEMA_VERSION",
    "Guidance",
    "GuidanceAction",
    "GuidanceActor",
    "GuidanceBlocker",
    "GuidancePhase",
    "GuidanceSubject",
    "change_guidance",
    "phase_of_run",
    "product_guidance",
]

GUIDANCE_SCHEMA_VERSION: Final[int] = 1


class GuidanceActor(StrEnum):
    """Who removes a blocker (ADR-033 p.4: a blocker is always attributed)."""

    OPERATOR = "operator"
    AGENT = "agent"
    CI = "ci"
    FACTORY = "factory"
    EXTERNAL = "external"


GuidancePhase = Phase
"""The operator phase of ADR-032; ``Phase`` lives in ``changes.enums`` since T078 so the
discussion entities bind to it — this alias keeps the M1 name."""


STAGE_PHASE: Final[dict[Stage, GuidancePhase]] = {
    Stage.SPECIFICATION: GuidancePhase.REQUIREMENTS,
    Stage.PLANNING: GuidancePhase.PLAN,
    Stage.CONSTRUCTION: GuidancePhase.EXECUTION,
    Stage.REVIEW_VERIFICATION: GuidancePhase.DEMONSTRATION,
    Stage.RELEASE: GuidancePhase.DELIVERY,
}
"""ADR-032 table; architecture/interface inside ``specification`` split in T098."""

PHASE_LABEL: Final[dict[GuidancePhase, str]] = {
    GuidancePhase.INITIATIVE: "Инициатива",
    GuidancePhase.REQUIREMENTS: "Требования",
    GuidancePhase.ARCHITECTURE: "Архитектура",
    GuidancePhase.INTERFACE: "Интерфейс",
    GuidancePhase.PLAN: "План",
    GuidancePhase.EXECUTION: "Исполнение",
    GuidancePhase.DEMONSTRATION: "Демонстрация и проверка",
    GuidancePhase.DELIVERY: "Доставка",
    GuidancePhase.DONE: "Завершено",
}

GATE_LABEL: Final[dict[Gate, str]] = {
    Gate.SPECIFICATION: "требования",
    Gate.PLANNING: "план",
    Gate.CODE: "код",
    Gate.UI: "интерфейс",
    Gate.REVIEW: "ревью",
    Gate.VERIFICATION: "проверку",
    Gate.RELEASE: "релиз",
}

PHASE_GATE_LABEL: Final[dict[GuidancePhase, str]] = {
    GuidancePhase.REQUIREMENTS: "требования",
    GuidancePhase.ARCHITECTURE: "архитектуру",
    GuidancePhase.INTERFACE: "интерфейс",
    GuidancePhase.PLAN: "план",
    GuidancePhase.DEMONSTRATION: "результат",
    GuidancePhase.DELIVERY: "доставку",
}
"""Stage-specific label of the phase approval («Согласовать …», plan §4 rule 1)."""

_ACTIVE_STAGE_STATUSES: Final[frozenset[StageStatus]] = frozenset(
    {StageStatus.IN_PROGRESS, StageStatus.WAITING, StageStatus.BLOCKED, StageStatus.PENDING}
)


class GuidanceAction(BaseModel):
    """One declarative action: the label and the command/endpoint that performs it.

    ``cli`` and ``api`` are the operator's two surfaces; either may be absent
    when the surface has no equivalent yet (then the other one is the way).
    ``enabled`` false keeps the action visible with the ``reason`` it is not
    available — the "no dead ends" rule (ADR-033 p.4).
    """

    model_config = ConfigDict(frozen=True)

    label: str
    cli: str | None = None
    api: str | None = None
    enabled: bool = True
    reason: str | None = None


class GuidanceBlocker(BaseModel):
    """What stands in the way, who removes it and how (ADR-033 p.4)."""

    model_config = ConfigDict(frozen=True)

    what: str
    who: GuidanceActor
    how: str


class GuidanceSubject(BaseModel):
    """The entity the guidance is about."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["product", "change"]
    id: str


class Guidance(BaseModel):
    """The operator's next step (ADR-033 p.1). Exactly one ``primary`` action."""

    model_config = ConfigDict(frozen=True)

    schema_version: int = GUIDANCE_SCHEMA_VERSION
    subject: GuidanceSubject
    phase: GuidancePhase | None
    headline: str
    why: str
    primary: GuidanceAction
    secondary: tuple[GuidanceAction, ...] = ()
    blockers: tuple[GuidanceBlocker, ...] = ()
    after: str | None = None


# --- product -------------------------------------------------------------------


def product_guidance(product: Product, *, open_changes: int = 0) -> Guidance:
    """Next step for a product (ADR-030 p.4): validate, wait, fix, or start a change."""
    subject = GuidanceSubject(kind="product", id=product.id)
    validate = GuidanceAction(
        label="Проверить репозиторий",
        cli=f"factory product validate --id {product.id}",
        api=f"POST /products/{product.id}/validate",
    )
    show = GuidanceAction(
        label="Показать продукт",
        cli=f"factory product show --id {product.id}",
        api=f"GET /products/{product.id}",
    )
    new_change = GuidanceAction(
        label="Новая фича",
        cli=f"factory change create --product {product.id} …",
        api="POST /changes",
    )
    match product.status:
        case ProductStatus.CREATED:
            return Guidance(
                subject=subject,
                phase=None,
                headline="Продукт зарегистрирован, репозиторий ещё не проверен",
                why="Фабрика не подтверждала доступ к репозиторию: готовность не наблюдалась.",
                primary=validate,
                secondary=(show,),
                blockers=(
                    GuidanceBlocker(
                        what="Репозиторий не проверен",
                        who=GuidanceActor.OPERATOR,
                        how="Запустите проверку: фабрика попробует дойти до репозитория.",
                    ),
                ),
                after="После проверки: продукт станет ready, и можно создать первую задачу.",
            )
        case ProductStatus.VALIDATING:
            return Guidance(
                subject=subject,
                phase=None,
                headline="Идёт проверка репозитория",
                why="Фабрика наблюдает репозиторий; статус обновится по завершении.",
                primary=show.model_copy(update={"label": "Обновить статус"}),
                secondary=(),
                blockers=(
                    GuidanceBlocker(
                        what="Проверка не завершена",
                        who=GuidanceActor.FACTORY,
                        how="Дождитесь результата или повторите проверку.",
                    ),
                ),
                after="После проверки: ready или error с причиной.",
            )
        case ProductStatus.ERROR:
            reason = product.status_reason or "причина не записана"
            return Guidance(
                subject=subject,
                phase=None,
                headline="Репозиторий недоступен фабрике",
                why=f"Последняя проверка: {reason}.",
                primary=validate.model_copy(update={"label": "Повторить проверку"}),
                secondary=(show,),
                blockers=(
                    GuidanceBlocker(
                        what=reason,
                        who=GuidanceActor.OPERATOR,
                        how="Проверьте, что GitHub App установлен на репозиторий и креды контура"
                        " заданы, затем повторите проверку.",
                    ),
                ),
                after="После успешной проверки: продукт ready, доступна первая задача.",
            )
        case ProductStatus.READY:
            why = (
                "Репозиторий проверен фабрикой."
                if open_changes == 0
                else f"Репозиторий проверен фабрикой; задач у продукта: {open_changes}."
            )
            return Guidance(
                subject=subject,
                phase=None,
                headline="Продукт готов к работе",
                why=why,
                primary=new_change,
                secondary=(validate.model_copy(update={"label": "Повторить проверку"}), show),
                blockers=(),
                after="Задача получит бриф, сценарий и лимит расхода; следующий шаг —"
                " фаза «Требования».",
            )


# --- change --------------------------------------------------------------------


def phase_of_run(run: ChangeRun | None) -> GuidancePhase:
    """The ADR-032 phase a run is in: the first active stage, else the last one."""
    if run is None:
        return GuidancePhase.INITIATIVE
    if run.status is RunStatus.SUCCEEDED:
        return GuidancePhase.DONE
    for stage_run in run.stages:
        if stage_run.status in _ACTIVE_STAGE_STATUSES:
            return STAGE_PHASE[stage_run.stage]
    if run.stages:
        return STAGE_PHASE[run.stages[-1].stage]
    return GuidancePhase.INITIATIVE


def change_guidance(
    change: Change,
    *,
    product: Product | None,
    run: ChangeRun | None,
    waiting_on: NextAction | None = None,
    phase_gate: PhaseGate | None = None,
) -> Guidance:
    """Next step for a change (T074): intake → requirements → run states.

    ``product`` is the owning product (``None`` for a pre-T065 change or an
    unknown ``product_id``), ``run`` the latest run of the change and
    ``waiting_on`` the ``NextAction`` of that run's latest stage result — the
    technical continuation the operator's step is derived from (ADR-033 p.5).
    ``phase_gate`` (T087) is the precondition view of the current phase's gate:
    with it, a parked human wait renders the approval step with its blockers
    (open blocking questions, a pending rework round, stale approvals) and a
    running rework round names itself; without it the M1 rendering stands.
    """
    subject = GuidanceSubject(kind="change", id=change.id)
    blockers: list[GuidanceBlocker] = []

    if product is None:
        blockers.append(
            GuidanceBlocker(
                what="Задача не привязана к зарегистрированному продукту",
                who=GuidanceActor.OPERATOR,
                how="Зарегистрируйте продукт (factory product add) и создайте задачу от него.",
            )
        )
    elif product.status is not ProductStatus.READY:
        blockers.append(
            GuidanceBlocker(
                what=f"Продукт {product.id} не готов (статус {product.status.value})",
                who=GuidanceActor.OPERATOR,
                how=f"Проверьте репозиторий: factory product validate --id {product.id}.",
            )
        )

    if run is None:
        return _intake_guidance(change, subject, product, blockers)
    return _run_guidance(change, subject, run, waiting_on, blockers, phase_gate)


def _intake_guidance(
    change: Change,
    subject: GuidanceSubject,
    product: Product | None,
    blockers: list[GuidanceBlocker],
) -> Guidance:
    brief = change.brief
    edit_brief = GuidanceAction(label="Дополнить бриф", api=f"PUT /changes/{change.id}/brief")
    start = GuidanceAction(
        label="Запустить фазу «Требования»",
        cli=f"factory run advance --change-id {change.id}",
        api=None,
    )
    scenario_note = (
        "сценарий specs-only: задача завершится после согласования спецификаций"
        if change.scenario.value == "specs_only"
        else "сценарий full: до доставки в dev"
    )
    limit_note = (
        f"лимит {change.spend_limit.cost_budget_usd} USD"
        if change.spend_limit is not None
        else "лимит расхода не задан"
    )
    if brief is None or not brief.is_complete:
        missing = brief.missing if brief is not None else ("problem", "goal")
        what = "Бриф не заполнен" if brief is None else "Бриф в черновике"
        detail = f"не заполнены поля: {', '.join(missing)}" if missing else "бриф не подтверждён"
        why = detail if brief is None or brief.error is None else f"{detail}; {brief.error}"
        blockers.insert(
            0,
            GuidanceBlocker(
                what=f"{what}: {detail}",
                who=GuidanceActor.OPERATOR,
                how="Заполните проблему и цель (или попросите агента сформулировать) и"
                " сохраните бриф.",
            ),
        )
        return Guidance(
            subject=subject,
            phase=GuidancePhase.INITIATIVE,
            headline=what,
            why=f"{_sentence(why)}; {scenario_note}; {limit_note}.",
            primary=edit_brief,
            secondary=(
                start.model_copy(update={"enabled": False, "reason": "нужен заполненный бриф"}),
            ),
            blockers=tuple(blockers),
            after="После заполнения брифа: запуск фазы «Требования».",
        )
    ready = not blockers
    author = (
        "сформулирован агентом"
        if brief.formulated_by is not None and brief.formulated_by.value == "agent"
        else "заполнен оператором"
    )
    return Guidance(
        subject=subject,
        phase=GuidancePhase.INITIATIVE,
        headline=(
            "Задача готова к фазе «Требования»"
            if ready
            else "Задача создана, но запуск заблокирован"
        ),
        why=f"Бриф {author}; {scenario_note}; {limit_note}.",
        primary=(
            start
            if ready
            else start.model_copy(update={"enabled": False, "reason": blockers[0].what})
        ),
        secondary=(edit_brief.model_copy(update={"label": "Изменить бриф"}),),
        blockers=tuple(blockers),
        after=(
            "Агент подготовит дельту требований и вопросы; вы согласуете их или"
            " отправите на доработку."
        ),
    )


def _run_guidance(
    change: Change,
    subject: GuidanceSubject,
    run: ChangeRun,
    waiting_on: NextAction | None,
    blockers: list[GuidanceBlocker],
    phase_gate: PhaseGate | None = None,
) -> Guidance:
    phase = phase_of_run(run)
    phase_label = PHASE_LABEL[phase]
    status = GuidanceAction(
        label="Показать состояние",
        cli=f"factory run status --run-id {run.id}",
        api=f"GET /runs/{run.id}",
    )
    advance = GuidanceAction(
        label="Продолжить запуск",
        cli=f"factory run advance --run-id {run.id}",
    )
    withdraw = GuidanceAction(
        label="Снять запуск",
        cli=f"factory run withdraw --run-id {run.id}",
        api=f"POST /runs/{run.id}/withdraw",
    )
    budget = run.budget
    limit_spent = budget.cost_budget is not None and budget.cost_used >= budget.cost_budget
    if limit_spent:
        blockers.insert(
            0,
            GuidanceBlocker(
                what=f"Лимит расхода исчерпан: {budget.cost_used} из {budget.cost_budget} USD",
                who=GuidanceActor.OPERATOR,
                how="Снимите запуск или создайте задачу с большим лимитом.",
            ),
        )
        return Guidance(
            subject=subject,
            phase=phase,
            headline="Лимит расхода исчерпан",
            why=f"Фаза «{phase_label}» остановлена: расход достиг лимита задачи.",
            primary=withdraw,
            secondary=(status,),
            blockers=tuple(blockers),
            after="После снятия: результаты сохранены, задачу можно завести заново.",
        )
    if budget.rework_exhausted:
        blockers.insert(
            0,
            GuidanceBlocker(
                what=f"Исчерпаны раунды доработки: {budget.used_rework_rounds} из"
                f" {budget.max_rework_rounds}",
                who=GuidanceActor.OPERATOR,
                how="Решите: принять результат как есть, снять запуск или пересмотреть"
                " требования новой задачей.",
            ),
        )

    match run.status:
        case RunStatus.PENDING | RunStatus.RUNNING:
            if phase_gate is not None and phase_gate.rework_in_progress:
                # A rework round was spent on the send-back (T081): the next
                # advance executes it — one step, like every other advance.
                return Guidance(
                    subject=subject,
                    phase=phase,
                    headline=(
                        f"Доработка «{phase_label}»: раунд {phase_gate.rework_rounds_used}"
                        f" из {phase_gate.rework_rounds_max}"
                    ),
                    why="Замечания и ответы переданы агенту; раунд исполняется следующим"
                    " запуском стадии.",
                    primary=advance.model_copy(
                        update={
                            "label": "Выполнить раунд доработки",
                            "cli": f"factory run advance --change-id {change.id}",
                        }
                    ),
                    secondary=(status, withdraw),
                    blockers=tuple(blockers),
                    after="После раунда: сводка агента «что изменил / что осталось» и повторное"
                    " согласование.",
                )
            return Guidance(
                subject=subject,
                phase=phase,
                headline=f"Идёт фаза «{phase_label}»",
                why="Агент работает; результат появится по завершении стадии.",
                primary=status.model_copy(update={"label": "Обновить состояние"}),
                secondary=(withdraw,),
                blockers=tuple(blockers),
                after=f"После завершения: результат фазы «{phase_label}» и следующий шаг.",
            )
        case RunStatus.WAITING:
            if phase_gate is not None and isinstance(
                waiting_on, RequestApprovalAction | WaitForInputAction
            ):
                return _approval_guidance(
                    change,
                    subject,
                    phase,
                    phase_gate,
                    waiting_on,
                    blockers,
                    status,
                    advance,
                    withdraw,
                )
            return _waiting_guidance(
                change, subject, run, phase, waiting_on, blockers, status, advance, withdraw
            )
        case RunStatus.BLOCKED:
            reason = _stop_reason(waiting_on) or "причина в результате стадии"
            blockers.insert(
                0,
                GuidanceBlocker(
                    what=f"Запуск заблокирован: {reason}",
                    who=GuidanceActor.OPERATOR,
                    how="Устраните причину и продолжите запуск (новая попытка стадии) или"
                    " снимите его.",
                ),
            )
            return Guidance(
                subject=subject,
                phase=phase,
                headline=f"Фаза «{phase_label}» заблокирована",
                why=_sentence(reason) + ".",
                primary=advance.model_copy(update={"label": "Повторить попытку"}),
                secondary=(withdraw, status),
                blockers=tuple(blockers),
                after="Новая попытка той же стадии; committed-результаты не переписываются.",
            )
        case RunStatus.FAILED:
            reason = _stop_reason(waiting_on) or "стадия завершилась ошибкой"
            blockers.insert(
                0,
                GuidanceBlocker(
                    what=f"Стадия завершилась ошибкой: {reason}",
                    who=GuidanceActor.OPERATOR,
                    how="Повторите попытку или снимите запуск.",
                ),
            )
            return Guidance(
                subject=subject,
                phase=phase,
                headline=f"Фаза «{phase_label}» завершилась ошибкой",
                why=_sentence(reason) + ".",
                primary=advance.model_copy(update={"label": "Повторить попытку"}),
                secondary=(withdraw, status),
                blockers=tuple(blockers),
                after="Новая попытка той же стадии.",
            )
        case RunStatus.CANCELED:
            return Guidance(
                subject=subject,
                phase=phase,
                headline="Запуск снят оператором",
                why="История и результаты сохранены; запуск не продолжается.",
                primary=GuidanceAction(
                    label="Новая задача",
                    cli=(
                        f"factory change create --product {change.product_id} …"
                        if change.product_id
                        else "factory change create …"
                    ),
                    api="POST /changes",
                ),
                secondary=(status,),
                blockers=tuple(blockers),
                after=None,
            )
        case RunStatus.SUPERSEDED:
            return Guidance(
                subject=subject,
                phase=phase,
                headline="Запуск заменён новым",
                why="Этот запуск больше не актуален; смотрите текущий запуск задачи.",
                primary=GuidanceAction(
                    label="Открыть задачу",
                    cli=f"factory change status --id {change.id}",
                    api=f"GET /changes/{change.id}",
                ),
                secondary=(status,),
                blockers=tuple(blockers),
                after=None,
            )
        case RunStatus.SUCCEEDED:
            return Guidance(
                subject=subject,
                phase=GuidancePhase.DONE,
                headline="Задача завершена",
                why=(
                    "Сценарий specs-only: спецификации согласованы."
                    if change.scenario.value == "specs_only"
                    else "Все стадии завершены."
                ),
                primary=GuidanceAction(
                    label="Открыть результат",
                    cli=f"factory change status --id {change.id}",
                    api=f"GET /changes/{change.id}",
                ),
                secondary=(),
                blockers=tuple(blockers),
                after=None,
            )


def _approval_guidance(
    change: Change,
    subject: GuidanceSubject,
    phase: GuidancePhase,
    gate: PhaseGate,
    waiting_on: RequestApprovalAction | WaitForInputAction,
    blockers: list[GuidanceBlocker],
    status: GuidanceAction,
    advance: GuidanceAction,
    withdraw: GuidanceAction,
) -> Guidance:
    """The human gate of a phase with its preconditions (T087, ADR-032 p.4).

    Exactly one primary — «Согласовать …» — enabled only when the gate is
    available; otherwise it stays visible with the first reason, and every
    reason becomes an attributed blocker with its action (ADR-033 p.4). The
    send-back (the rework order) is always a secondary action: a comment alone
    never starts a round (ADR-034 p.2).
    """
    phase_label = PHASE_LABEL[phase]
    approve = GuidanceAction(
        label=f"Согласовать {PHASE_GATE_LABEL.get(phase, phase_label.lower())}",
        cli=f"factory change approve --id {change.id} --phase {phase.value}",
        api=f"POST /changes/{change.id}/approvals",
    )
    rework = GuidanceAction(
        label="На доработку",
        cli=f"factory change rework --id {change.id} --phase {phase.value} …",
        api=f"POST /changes/{change.id}/rework-orders",
    )
    questions = GuidanceAction(
        label="Ответить на вопросы",
        cli=f"factory change status --id {change.id}",
        api=f"GET /changes/{change.id}/questions?status=open",
    )
    artifacts = GuidanceAction(
        label="Посмотреть артефакты",
        cli=f"factory change artifacts --id {change.id} list",
        api=f"GET /changes/{change.id}/artifacts",
    )
    for reason in gate.reasons:
        blockers.insert(
            0, GuidanceBlocker(what=reason.what, who=GuidanceActor.OPERATOR, how=reason.how)
        )
    counts = (
        f"вопросов без ответа: {gate.open_questions} (блокирующих {gate.blocking_questions});"
        f" открытых замечаний: {gate.open_comments}"
        + (
            f", ждут повторной проверки: {gate.addressed_comments}"
            if gate.addressed_comments
            else ""
        )
        + (f"; отвязанных замечаний: {gate.detached_comments}" if gate.detached_comments else "")
    )
    stale = sum(1 for view in gate.approvals if view.state == "stale")
    if stale:
        counts += f"; прежних согласований неактуально: {stale}"
    if gate.approved:
        return Guidance(
            subject=subject,
            phase=phase,
            headline=f"Фаза «{phase_label}» согласована на текущей ревизии",
            why=f"Решение записано на ревизии {gate.current_revision}; {counts}.",
            primary=advance.model_copy(update={"label": "Продолжить после согласования"}),
            secondary=(rework, artifacts, status),
            blockers=tuple(blockers),
            after=f"Следующая фаза после «{phase_label}».",
        )
    if not gate.available:
        first = gate.reasons[0].what if gate.reasons else "гейт недоступен"
        secondary: list[GuidanceAction] = []
        if gate.blocking_questions:
            secondary.append(questions)
        secondary.extend((rework, artifacts, withdraw))
        primary = (
            questions
            if gate.blocking_questions
            else approve.model_copy(update={"enabled": False, "reason": first})
        )
        return Guidance(
            subject=subject,
            phase=phase,
            headline=f"Фаза «{phase_label}»: нужно решение, гейт пока закрыт",
            why=f"{_sentence(first)}; {counts}.",
            primary=primary,
            secondary=tuple(action for action in secondary if action is not primary),
            blockers=tuple(blockers),
            after=f"После снятия причин: согласование «{phase_label}» или доработка.",
        )
    blockers.insert(
        0,
        GuidanceBlocker(
            what=f"Нужно решение человека: согласовать «{phase_label}»",
            who=GuidanceActor.OPERATOR,
            how=f"Просмотрите артефакты на ревизии {gate.current_revision} и запишите решение"
            " (согласовать / на доработку).",
        ),
    )
    return Guidance(
        subject=subject,
        phase=phase,
        headline=f"Фаза «{phase_label}» готова к согласованию",
        why=f"{_sentence(waiting_on.reason or 'агент подготовил результат')}; {counts}.",
        primary=approve,
        secondary=(rework, artifacts, withdraw),
        blockers=tuple(blockers),
        after=f"После согласования: следующая фаза после «{phase_label}».",
    )


def _waiting_guidance(
    change: Change,
    subject: GuidanceSubject,
    run: ChangeRun,
    phase: GuidancePhase,
    waiting_on: NextAction | None,
    blockers: list[GuidanceBlocker],
    status: GuidanceAction,
    advance: GuidanceAction,
    withdraw: GuidanceAction,
) -> Guidance:
    phase_label = PHASE_LABEL[phase]
    match waiting_on:
        case RequestApprovalAction(gate=gate):
            gate_label = GATE_LABEL[gate]
            blockers.insert(
                0,
                GuidanceBlocker(
                    what=f"Нужно решение человека: согласовать {gate_label}",
                    who=GuidanceActor.OPERATOR,
                    how="Просмотрите результат фазы и запишите решение (approve/reject) на"
                    " текущей ревизии.",
                ),
            )
            return Guidance(
                subject=subject,
                phase=phase,
                headline=f"Фаза «{phase_label}» готова к согласованию",
                why=waiting_on.reason
                or f"Агент подготовил результат; ожидается решение по гейту {gate.value}.",
                primary=GuidanceAction(
                    label=f"Согласовать {gate_label}",
                    api=f"POST /changes/{change.id}/approvals",
                ),
                secondary=(
                    advance.model_copy(update={"label": "Продолжить после решения"}),
                    withdraw,
                ),
                blockers=tuple(blockers),
                after=f"После согласования: следующая фаза после «{phase_label}».",
            )
        case WaitForCIAction(reason=reason, change_request=change_request):
            if change_request is None:
                where = "change request"
            else:
                where = f"CR #{change_request.number}"
                if change_request.url:
                    where += f" ({change_request.url})"
            blockers.insert(
                0,
                GuidanceBlocker(
                    what=f"Ожидается CI: {reason}",
                    who=GuidanceActor.CI,
                    how=f"Дождитесь зелёного CI на {where}; затем продолжите запуск.",
                ),
            )
            return Guidance(
                subject=subject,
                phase=phase,
                headline=f"Фаза «{phase_label}»: ожидается CI",
                why=reason,
                primary=advance.model_copy(update={"label": "Продолжить по результату CI"}),
                secondary=(status, withdraw),
                blockers=tuple(blockers),
                after="Зелёный CI продвинет запуск; красный — новая попытка или доработка.",
            )
        case WaitForInputAction(reason=reason):
            blockers.insert(
                0,
                GuidanceBlocker(
                    what=f"Нужен ввод оператора: {reason}",
                    who=GuidanceActor.OPERATOR,
                    how="Дайте недостающий ввод и продолжите запуск.",
                ),
            )
            return Guidance(
                subject=subject,
                phase=phase,
                headline=f"Фаза «{phase_label}» ждёт ввода оператора",
                why=reason,
                primary=advance,
                secondary=(status, withdraw),
                blockers=tuple(blockers),
                after="После ввода агент продолжит стадию.",
            )
        case ReworkAction(round=round_number, max_rounds=max_rounds, reason=reason):
            return Guidance(
                subject=subject,
                phase=phase,
                headline=f"Доработка {round_number} из {max_rounds}",
                why=reason,
                primary=advance.model_copy(update={"label": "Запустить доработку"}),
                secondary=(status, withdraw),
                blockers=tuple(blockers),
                after="После доработки: повторная проверка результата.",
            )
        case MergeAction(change_request=change_request):
            blockers.insert(
                0,
                GuidanceBlocker(
                    what=f"Нужен merge CR #{change_request.number} человеком (ADR-011)",
                    who=GuidanceActor.OPERATOR,
                    how="Проверьте чек-лист и смержите CR; фабрика продолжит по наблюдаемому"
                    " merge.",
                ),
            )
            return Guidance(
                subject=subject,
                phase=phase,
                headline="Готово к merge",
                why=waiting_on.reason or "Проверки пройдены; решение о merge — за человеком.",
                primary=GuidanceAction(
                    label="Смержить CR",
                    cli=None,
                    api=None,
                    enabled=False,
                    reason="Помощник merge появится в M5 (T112–T113); пока — merge в провайдере.",
                ),
                secondary=(advance.model_copy(update={"label": "Продолжить после merge"}), status),
                blockers=tuple(blockers),
                after="После merge: доставка в dev.",
            )
        case ReleaseAction(target_environment=environment):
            blockers.insert(
                0,
                GuidanceBlocker(
                    what=f"Ожидается доставка в {environment}",
                    who=GuidanceActor.EXTERNAL,
                    how="Дождитесь синхронизации GitOps и smoke, затем продолжите запуск.",
                ),
            )
            return Guidance(
                subject=subject,
                phase=phase,
                headline=f"Доставка в {environment}",
                why=waiting_on.reason or "Релиз продвинут; ожидается наблюдаемое состояние.",
                primary=advance.model_copy(update={"label": "Продолжить по наблюдению"}),
                secondary=(status, withdraw),
                blockers=tuple(blockers),
                after="После smoke: подтверждение baseline и закрытие.",
            )
        case ExecuteStageAction() | StopAction() | None:
            blockers.insert(
                0,
                GuidanceBlocker(
                    what="Запуск ожидает внешнего события",
                    who=GuidanceActor.EXTERNAL,
                    how="Продолжите запуск, когда событие наступит, или снимите его.",
                ),
            )
            return Guidance(
                subject=subject,
                phase=phase,
                headline=f"Фаза «{phase_label}» ожидает",
                why="Причина ожидания не записана в последнем результате стадии.",
                primary=advance,
                secondary=(status, withdraw),
                blockers=tuple(blockers),
                after=None,
            )


def _stop_reason(action: NextAction | None) -> str | None:
    return action.reason if isinstance(action, StopAction) else None


def _sentence(text: str) -> str:
    """Upper-case the first letter only; identifiers inside the text keep their case."""
    return text[:1].upper() + text[1:]


def spend_forecast(change: Change, run: ChangeRun | None) -> Decimal | None:
    """Remaining money of the change's limit, or ``None`` without a limit (T076 «прогноз»)."""
    if change.spend_limit is None:
        return None
    used = run.budget.cost_used if run is not None else Decimal("0")
    return change.spend_limit.cost_budget_usd - used
