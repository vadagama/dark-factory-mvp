# Как устроен цикл реализация → review → rework (US3) — без клона репозитория фабрики

**Кому**: оператору фабрики — человеку за клавиатурой.
**Время**: ~8 минут.
**Проверено**: 2026-09-14, фабрика 0.1.0 (коммит `6410f2640d99b74f4cfb6478ddec106ff8c10dc0`), запуск через `uvx --python 3.12 --from git+… python us3_lab.py`.
**Фича**: Phase 5 / User Story 3 «Агентная реализация с независимым review и ограниченным rework» (`specs/001-dark-factory-mvp/tasks.md`, T019–T023).
**Перед этим**: согласование спецификации — [`run-spec-approval-us2.md`](./run-spec-approval-us2.md); установка фабрики — [`run-one-stage-us1.md`](./run-one-stage-us1.md).

## Простыми словами: что мы сейчас сделаем

US2 дал согласованную спецификацию. US3 — участок, где по ней пишут код, и здесь три железных правила:

1. **исполнитель** работает в изолированном workspace и ветке, результат — коммит и MR;
2. **независимый reviewer** (отдельный контекст, не наследует авторский) возвращает формализованные замечания — находки;
3. **доработка ограничена**: максимум 3 раунда (бюджет прогона), исправления идут в тот же MR, каждый новый SHA обнуляет прежние допуски, а упорные проблемы **эскалируют к человеку**.

Чего на этом участке ещё нет: самого агента-исполнителя, GitHub-интеграции, машинных проверок в CI. Лаборатория ниже прогоняет **машинную сторону** цикла — гейт review, политику rework со стоп-условиями, эскалации автономии и воспроизводимый контекст приёмки. Всё детерминировано: LLM не вызывается, сеть не нужна (после первого скачивания фабрики), на диск ничего не пишется — даже git-репозиторий не нужен.

План:

1. сохраним скрипт лаборатории,
2. запустим его и разберём каждый блок,
3. попробуем поменять правила и посмотрим, как машина отреагирует.

## Что понадобится

- [uv](https://docs.astral.sh/uv/) — тот же, что для US1;
- интернет — один раз, чтобы скачать фабрику (дальше кэш);
- **Не понадобится**: клон фабрики, git, папка проекта, ключи LLM, база данных, секреты.

## Шаг 0. Скрипт лаборатории

Сохраните скрипт `us3_lab.py` в любую папку:

```python
"""US3 lab: implementation contract -> review -> bounded rework -> green pass -> escalations.

No LLM, no network, no .factory needed: this lab exercises the deterministic
policies of the rework loop, the review gate and the escalation checks.
"""

from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256

from dark_factory.changes.escalations import EscalationViolation
from dark_factory.changes.enums import (
    BoundaryArea,
    EscalationRule,
    FindingOrigin,
    FindingSeverity,
    FindingStatus,
    RiskClass,
    Role,
    Route,
    Stage,
)
from dark_factory.changes.findings import Finding
from dark_factory.changes.implementation_contract import (
    AcceptanceCriterion,
    ChangeScope,
    ContractApproval,
    ContractBudget,
    ImplementationContract,
)
from dark_factory.changes.usage import BudgetSnapshot
from dark_factory.orchestration.policy.escalation import (
    BoundaryChange,
    boundary_change_violation,
    contract_entry_violation,
    risk_escalation_violation,
    scope_exit_violation,
)
from dark_factory.orchestration.rework import ReviewPass, finding_signature, plan_rework
from dark_factory.quality.acceptance import (
    DiffMaterial,
    EvidenceMaterial,
    MrComment,
    SpecMaterial,
    build_acceptance_context,
    classify_findings,
    evaluate_review_gate,
    human_comment_findings,
)

SHA1 = "c0ffee000001"  # первый commit реализации
SHA2 = "c0ffee000002"  # commit после доработки
CHANGE_ID = "chg:my-product:2026:0001"

print("== Шаг 1. Implementation Contract: без approval строительства нет ==")
contract = ImplementationContract(
    id="ict-my-product-0001",
    scope=ChangeScope(
        in_scope=("checkout timeout configuration",),
        out_of_scope=("payment flow",),
    ),
    acceptance_criteria=(
        AcceptanceCriterion(id="AC-1", description="checkout times out with a clear error"),
    ),
    risk_class=RiskClass.R1,
    budget=ContractBudget(max_autonomous_iterations=3, max_cost=Decimal("5.00")),
)
violation = contract_entry_violation(contract)
print("not approved: blocked" if violation else "not approved: allowed", end="")
print(f" ({violation.rule.value})" if violation else "")
approved = contract.model_copy(
    update={"approval": ContractApproval(approved_by=Role.PRODUCT, decided_at=datetime.now(UTC))}
)
print("approved:", "blocked" if contract_entry_violation(approved) else "allowed")

print()
print("== Шаг 2. Независимое review: находки отдельного контекста ==")
reviewer_finding = Finding(
    id="REV-1",
    origin=FindingOrigin.AGENT,
    role=Role.QUALITY,
    severity=FindingSeverity.BLOCKER,
    category="correctness",
    file="src/checkout.py",
    line=42,
    reviewed_sha=SHA1,
    required_action="handle the timeout exception",
    status=FindingStatus.OPEN,
    confidence=0.9,
)
human_remark = human_comment_findings(
    [MrComment(comment_id="MR-1", body="почему 30 секунд?", file="src/checkout.py", line=42)],
    reviewed_sha=SHA1,
)[0]
classification = classify_findings([reviewer_finding, human_remark], sha=SHA1)
print(f"blocking: {[f.id for f in classification.blocking]}")
print(f"non-blocking: {[f.id for f in classification.non_blocking]}")
gate = evaluate_review_gate([reviewer_finding, human_remark], sha=SHA1)
print(f"review gate at {gate.sha}: {gate.status.value}")
print(f"summary: {gate.summary}")

print()
print("== Шаг 3. Bounded rework: план раунда ==")
budget = BudgetSnapshot()  # по умолчанию: максимум 3 раунда доработки
signature = finding_signature(reviewer_finding)
pass1 = ReviewPass(round=1, sha=SHA1, blocking=(signature,))
decision = plan_rework(budget=budget, passes=[pass1])
print(f"outcome: {decision.outcome}, round: {decision.round}/{decision.max_rounds}")
print(f"reason: {decision.reason}")

print()
print("== Шаг 4. Стоп-условия цикла (детерминированные) ==")
same_sha = plan_rework(
    budget=budget,
    passes=[pass1, ReviewPass(round=2, sha=SHA1, blocking=(signature,))],
)
print(f"rework without a new commit -> {same_sha.outcome}: {same_sha.reason}")
same_findings = plan_rework(
    budget=budget,
    passes=[pass1, ReviewPass(round=2, sha=SHA2, blocking=(signature,))],
)
print(f"same findings after rework -> {same_findings.outcome}: {same_findings.reason}")
escalated = plan_rework(
    budget=budget,
    passes=[
        ReviewPass(
            round=1,
            sha=SHA1,
            blocking=(signature,),
            escalations=[
                EscalationViolation(
                    rule=EscalationRule.NEW_ADR_PROPOSAL,
                    reason="agent proposes a new ADR",
                )
            ],
        )
    ],
)
print(f"declared escalation -> {escalated.outcome}: {escalated.reason}")
round2 = Finding(id="REV-2", origin=FindingOrigin.AGENT, severity=FindingSeverity.BLOCKER,
                 category="types", file="src/checkout.py", line=7,
                 reviewed_sha=SHA2, status=FindingStatus.OPEN)
round3 = Finding(id="REV-3", origin=FindingOrigin.AGENT, severity=FindingSeverity.BLOCKER,
                 category="tests", file="tests/test_checkout.py", line=1,
                 reviewed_sha="c0ffee000003", status=FindingStatus.OPEN)
exhausted = BudgetSnapshot(max_rework_rounds=3, used_rework_rounds=3)
limit = plan_rework(
    budget=exhausted,
    passes=[
        pass1,
        ReviewPass(round=2, sha=SHA2, blocking=(finding_signature(round2),)),
        ReviewPass(round=3, sha="c0ffee000003", blocking=(finding_signature(round3),)),
        ReviewPass(round=4, sha="c0ffee000004", blocking=(finding_signature(round3),)),
    ],
)
print(f"fourth round requested -> {limit.outcome}: {limit.reason}")

print()
print("== Шаг 5. Зелёный проход: новый SHA аннулирует прежний допуск ==")
minor = Finding(id="REV-4", origin=FindingOrigin.AGENT, severity=FindingSeverity.MINOR,
                category="style", file="src/checkout.py", line=42,
                reviewed_sha=SHA2, status=FindingStatus.OPEN)
after = [reviewer_finding, minor, human_remark]
final = classify_findings(after, sha=SHA2)
print(f"blocking: {[f.id for f in final.blocking]}")
print(f"stale (другой SHA): {[f.id for f in final.stale]}")
green = evaluate_review_gate(after, sha=SHA2)
print(f"review gate at {green.sha}: {green.status.value}")
print(f"summary: {green.summary}")
try:
    plan_rework(budget=budget, passes=[ReviewPass(round=2, sha=SHA2, blocking=())])
except ValueError as exc:
    print(f"rework loop for a passed review is not planned: {exc}")

print()
print("== Шаг 6. Эскалации автономии (ADR-018) ==")
scope_violation = scope_exit_violation(["payment flow refactor"], approved.scope)
print(f"scope exit -> {scope_violation.rule.value}: {scope_violation.reason}")
boundary_violation = boundary_change_violation(
    BoundaryChange(area=BoundaryArea.PUBLIC_API, compatible=False), approved
)
print(f"boundary change -> {boundary_violation.rule.value}: {boundary_violation.reason}")
risk_violation = risk_escalation_violation(
    risk_class=RiskClass.R2, route=Route.QUICK, stage=Stage.PLANNING
)
print(f"risk raise -> {risk_violation.rule.value}: {risk_violation.reason}")

print()
print("== Шаг 7. Контекст приёмки строится заново и воспроизводим ==")
spec = SpecMaterial(
    location=".factory/changes/2026/CHG-0001-checkout-timeout",
    revision="7f6437bae4dcd3fe",
    content="Оформление заказа прерывается по таймауту (AC-1, AC-2).",
)
diff = DiffMaterial(repository="my-product", sha=SHA2, content="diff --git a/src/checkout.py")
evidence = EvidenceMaterial(
    evidence_id="ev-tests",
    location="ci://run-us3-demo/junit.xml",
    content_hash=sha256(b"junit xml bytes").hexdigest(),
)
bundle_one = build_acceptance_context(
    change_id=CHANGE_ID,
    run_id="run_us3_demo",
    spec=spec,
    diff=diff,
    evidence=[evidence],
    now=datetime(2026, 9, 14, 12, 0, tzinfo=UTC),
)
bundle_two = build_acceptance_context(
    change_id=CHANGE_ID,
    run_id="run_us3_demo",
    spec=spec,
    diff=diff,
    evidence=[evidence],
    now=datetime(2026, 9, 14, 18, 0, tzinfo=UTC),
)
print(f"bundle_hash reproducible: {bundle_one.bundle_hash == bundle_two.bundle_hash}")
print(f"sources: {[s.kind.value for s in bundle_one.sources]}")
try:
    build_acceptance_context(
        change_id=CHANGE_ID,
        run_id="run_us3_demo",
        spec=spec,
        diff=diff,
        evidence=[evidence, evidence],
        now=datetime(2026, 9, 14, tzinfo=UTC),
    )
except ValueError as exc:
    print(f"duplicate evidence rejected: {exc}")
```

## Шаг 1. Запуск (главный шаг)

```bash
uvx --python 3.12 \
  --from git+https://github.com/vadagama/dark-factory-mvp@6410f2640d99b74f4cfb6478ddec106ff8c10dc0 \
  python us3_lab.py
```

Ожидаемый вывод (полностью):

```
== Шаг 1. Implementation Contract: без approval строительства нет ==
not approved: blocked (implementation_contract_unapproved)
approved: allowed

== Шаг 2. Независимое review: находки отдельного контекста ==
blocking: ['REV-1']
non-blocking: ['MR-1']
review gate at c0ffee000001: failed
summary: review gate failed: 1 blocking finding(s) at c0ffee000001: REV-1

== Шаг 3. Bounded rework: план раунда ==
outcome: rework, round: 1/3
reason: rework round 1: fix 1 blocking finding(s) from review pass 1 at c0ffee000001: agent/correctness src/checkout.py:42

== Шаг 4. Стоп-условия цикла (детерминированные) ==
rework without a new commit -> blocked: rework did not produce a new commit: review pass 2 ran again at c0ffee000001; the previous acceptance of that SHA stands, re-review is impossible
same findings after rework -> blocked: rework round 1 did not change the set of blocking findings: the same 1 finding(s) repeat
declared escalation -> blocked: new_adr_proposal: agent proposes a new ADR
fourth round requested -> blocked: rework limit exhausted: 3/3 rounds used

== Шаг 5. Зелёный проход: новый SHA аннулирует прежний допуск ==
blocking: []
stale (другой SHA): ['REV-1', 'MR-1']
review gate at c0ffee000002: passed
summary: review gate passed: 1 non-blocking finding(s) at c0ffee000002; 2 stale finding(s) excluded
rework loop for a passed review is not planned: review pass 2 at c0ffee000002 has no blocking findings: the rework loop is planned only for a failed review

== Шаг 6. Эскалации автономии (ADR-018) ==
scope exit -> scope_exit: implementation leaves the approved scope: payment flow refactor
boundary change -> boundary_change: public_api change is not provided for by the approved implementation contract (ADR-018 p.5: public API, data schema, IAM, architecture boundary)
risk raise -> risk_raised_to_r2: risk class R2 obligations are not met: route 'quick' does not allow risk class R2 (band R0-R1); no human approval on the control points of stage 'planning': solution

== Шаг 7. Контекст приёмки строится заново и воспроизводим ==
bundle_hash reproducible: True
sources: ['evidence', 'repo', 'spec']
duplicate evidence rejected: duplicate evidence id 'ev-tests'
```

Если все семь блоков совпали — сценарий пройден. Разбор — ниже.

## Шаг 2. Разбор: что произошло в каждом блоке

**Блок 1. Контракт реализации.** Implementation Contract — одобренная человеком граница автономии: что можно трогать (scope), по каким критериям принимать работу, какой риск и бюджет. Неутверждённый контракт — ворота строительства закрыты (`implementation_contract_unapproved`). Так фабрика гарантирует: никакая реализация не начинается без согласованной спецификации и одобренных рамок (мост из US2).

**Блок 2. Независимое review.** Два вида находок: `REV-1` от агента Quality (blocker, файл и строка, требуемое действие) и человеческий комментарий MR (`MR-1`). Комментарии MR входят в общий набор находок **как данные**: они не интерпретируются и не блокируют (у них severity `info`). Гейт review вынес вердикт `failed` — и вердикт **привязан к SHA**: `at c0ffee000001`.

**Блок 3. План раунда.** `plan_rework` — чистая функция: по бюджету и истории review-проходов она решает, тратить ли ещё один раунд. Ответ: `rework round 1/3` с человекочитаемой причиной — какие находки, в каком файле, на каком SHA. История цикла **append-only**: новый проход добавляется, старые не переписываются.

**Блок 4. Четыре стоп-условия.** Цикл останавливается детерминированно, первое сработавшее условие даёт `Blocked` с диагностикой:

| Сценарий | Вердикт |
|---|---|
| rework не дал нового коммита (тот же SHA) | blocked: прежний допуск на SHA остаётся, re-review невозможен |
| набор блокирующих находок не изменился после раунда | blocked: повторяющаяся ошибка |
| на проходе объявлена эскалация (например, агент предлагает новый ADR) | blocked: решение за человеком, раунд не тратится |
| запрошен 4-й раунд при бюджете 3 | blocked: `rework limit exhausted: 3/3 rounds used` |

Обратите внимание: счётчик потраченных раундов живёт в бюджете прогона (`BudgetSnapshot`), а не «на память» у цикла — лимит нельзя обойти пересозданием истории.

**Блок 5. Зелёный проход.** После доработки новый SHA: `REV-1` всё ещё открытый blocker, но его `reviewed_sha` — старый SHA, поэтому он **stale** и в решении не участвует. Остались minor-находка и человеческий комментарий — гейт `passed`. Итог: **допуск действителен только для своего SHA** — новый коммит обнуляет прежние проверки (машинные в CI повторяются на итоговом SHA, FR-009). Зелёное ревью в цикл rework не попадает вовсе — это `ValueError` по дизайну: passed уходит дальше по конвейеру, а не в доработку.

**Блок 6. Эскалации автономии.** Три проверяемых выхода за рамки: реализация трогает то, чего нет в scope (`scope_exit`); меняет защищённую границу — публичный API, схему данных, IAM, архитектурную границу — без разрешения в контракте (`boundary_change`); поднимает риск до R2+ (`risk_escalation_violation`, T-080). Эскалация риска — не ручная пометка, а машинная проверка обязательств класса: маршрут с полосой `R0–R1` не допускает R2, и человеческая точка контроля стадии (`solution` — гейт `planning`) должна иметь `APPROVED`-решение, привязанное к SHA; `reason` перечисляет всё невыполненное. Каждая эскалация — это veto автономному продолжению: дальше только человек.

**Блок 7. Независимый контекст приёмки.** Reviewer не наследует контекст автора: приёмка собирает **свой** набор материалов — закреплённая спецификация, diff на конкретном SHA, evidence. `bundle_hash` воспроизводим: одинаковые входы дают одинаковый отпечаток, даже если время сбора (`retrieved_at`) разное — время в хеш не входит. Дубликат evidence — ошибка: одна snapshot каждого артефакта.

## Шаг 3. Эксперименты

- **Ослабьте находку**: поменяйте `FindingSeverity.BLOCKER` на `MINOR` у `REV-1` — гейт review на SHA1 станет `passed`: блокируют только blockers из политики.
- **Закройте находку**: поставьте `REV-1` статус `RESOLVED` — открытые и решённые находки весят по-разному, решённые не блокируют.
- **Сожмите бюджет**: `BudgetSnapshot(max_rework_rounds=1)` — уже второй раунд даст `blocked` с исчерпанием лимита.
- **Уберите эскалацию из контракта**: передайте `contract=None` в `boundary_change_violation` — сообщение изменится: изменение границы вообще не предусмотрено контрактом.

## Чек-лист «сценарий пройден»

- [ ] блок 1: неутверждённый контракт — `implementation_contract_unapproved`; утверждённый — `allowed`
- [ ] блок 2: `blocking: ['REV-1']`, `non-blocking: ['MR-1']`, гейт `failed` на `c0ffee000001`
- [ ] блок 3: план — `rework, round 1/3` с перечнем находок
- [ ] блок 4: четыре сценария — все `blocked` с детерминированными причинами
- [ ] блок 5: stale-находки исключены, гейт `passed` на новом SHA; passed-ревью в цикл не входит
- [ ] блок 6: `scope_exit`, `boundary_change`, `risk_raised_to_r2` с перечнем обязательств (`does not allow risk class R2`, `solution`)
- [ ] блок 7: `bundle_hash reproducible: True`; дубликат evidence отклонён

## Чего на этом участке конвейера пока нет

Чтобы не искать лишнего:

- **исполнителя нет**: агент Develop, изолированный workspace и ветка, коммит и MR с описанием — следующие задачи (GitHub-адаптер — T-030+, MR-контур — US4);
- **проверки в CI нет**: машинные проверки (стиль, типы, тесты) обязаны быть зелёными на итоговом SHA в CI, не локально (FR-009) — CI-джобы фабрики появятся в T-031;
- **wiring в исполнение стадии не завершён**: политика цикла готова, подключение её к исполнителю стадии и TaskGraph внутри стадии — T022 (ещё в работе);
- **параллельного исполнения нет**: два независимых агента с последовательной интеграцией патчей (лимит ≤ 2) — следующая задача;
- **merge нет**: merge — ручной либо доверенным финализатором по правилам (US4, ADR-011: агенты MR не мержат).

## Справочник терминов

| Термин | Простыми словами |
|---|---|
| Finding | формализованное замечание: id, источник, severity, файл/строка, проверяемый SHA, действие, статус |
| `severity` | вес находки: `blocker` блокирует, `major`/`minor`/`info` — нет |
| `origin` | кто нашёл: `agent`, `human`, `ci` — комментарии MR входят как `human`/`info` |
| Сигнатура находки | устойчивая идентичность (источник, категория, файл, строка) — по ней цикл узнаёт «ту же ошибку» |
| `ReviewPass` | один завершённый проход review: раунд, SHA, блокирующие сигнатуры, эскалации |
| Раунд доработки | одна итерация «исправь и принеси новый коммит»; максимум 3 по умолчанию |
| `BudgetSnapshot` | бюджет прогона: лимит раундов и сколько уже потрачено — авторитетный счётчик |
| Стоп-условия | детерминированные причины `Blocked`: эскалация, лимит, нет нового SHA, повтор ошибки |
| Stale | находка с чужого SHA: новый коммит обнуляет прежний review |
| `GateResult` | вердикт гейта: passed/failed/blocked для конкретного SHA |
| Implementation Contract | одобренная человеком граница автономии: scope, критерии, риск, бюджет, эскалации |
| `EscalationViolation` | зафиксированный выход за рамки контракта — veto автономному продолжению |
| ContextBundle / `bundle_hash` | зафиксированный набор материалов приёмки с воспроизводимым отпечатком |

## Откуда взяты правила

- Официальный сценарий проверки — `specs/001-dark-factory-mvp/quickstart.md` (§4).
- Задачи фазы — `specs/001-dark-factory-mvp/tasks.md` (Phase 5 / US3, T019–T023).
- Границы автономной реализации и эскалации — `docs/adr/ADR-018-*`.
- Гейты и находки в доменной модели — `specs/001-dark-factory-mvp/data-model.md` (§5).
- Machine checks выполняются в CI на итоговом SHA — `specs/001-dark-factory-mvp/spec.md` (FR-009, SC-004).
