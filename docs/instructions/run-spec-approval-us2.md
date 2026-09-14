# Как согласовать спецификацию руками (US2) — без клона репозитория фабрики

**Кому**: оператору фабрики — человеку за клавиатурой.
**Время**: ~10 минут.
**Проверено**: 2026-09-14, фабрика 0.1.0 (коммит `6410f2640d99b74f4cfb6478ddec106ff8c10dc0`), запуск через `uvx --python 3.12 --from git+… python us2_lab.py`.
**Фича**: Phase 4 / User Story 2 «От задачи к согласованной спецификации» (`specs/001-dark-factory-mvp/tasks.md`, T014–T018).
**Перед этим**: если вы ещё не проходили сценарий US1 — начните с [`run-one-stage-us1.md`](./run-one-stage-us1.md): там установка фабрики и шпаргалка по кодам выхода.

## Простыми словами: что мы сейчас сделаем

В US1 фабрика честно исполнила одну стадию и вернула протокол. US2 — следующий участок конвейера: **задача превращается в согласованную спецификацию**. Как это устроено:

1. задание нормализуется в **ChangeSet** — proposal изменения с дельтой относительно **Product Baseline** (канонического описания продукта);
2. **спецификационный гейт** — детерминированная машина проверок — решает, готов ли proposal (критерии приёмки есть, каждая задача трассируется к требованию, противоречий scope нет);
3. человек согласовывает конкретную **ревизию** спецификации;
4. согласованное уходит в baseline (**reconciliation**), и реализация разрешается только для этой ревизии.

Чего на этом участке ещё нет: агента Product с LLM, публикации proposal как MR, кнопки в Console. Лаборатория ниже прогоняет **машинную сторону** согласования — гейт, привязку решения к ревизии, reconcile в baseline и ворота входа в реализацию. Всё детерминировано, как калькулятор: LLM не вызывается, сеть не нужна (после первого скачивания фабрики).

Важно: ChangeSet и Product Baseline живут **у вас в проекте**, в папке `.factory/`. Репозиторий фабрики не клонируется и не меняется.

План:

1. подготовим папку проекта,
2. сохраним скрипт лаборатории,
3. запустим его и разберём каждый шаг,
4. посмотрим, что фабрика записала в `.factory/`,
5. попробуем нарушить правила и посмотрим на отказ.

## Что понадобится

- [uv](https://docs.astral.sh/uv/) — тот же, что для US1;
- интернет — один раз, чтобы скачать фабрику (дальше кэш);
- **Не понадобится**: клон фабрики, git-репозиторий проекта (хотя в реальной работе baseline живёт в git), ключи LLM, база данных, секреты.

## Шаг 0. Папка проекта и скрипт лаборатории

```bash
mkdir -p ~/projects/my-product-lab && cd ~/projects/my-product-lab
```

Сохраните рядом скрипт `us2_lab.py`. Каждый запуск — в **свежей папке** (или удалите `.factory/` перед повтором): сценарий ведёт одно изменение от proposal до reconciliation, и второй прогон в той же папке честно откажется — дельта уже принята (это увидим в шаге 5).

```python
"""US2 lab: intake -> proposal (ChangeSet) -> specification gate -> approval -> reconciliation."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from dark_factory.changes.enums import DecisionSource, GateStatus, RiskClass, Role
from dark_factory.changes.implementation_contract import (
    AcceptanceCriterion,
    ChangeScope,
    ContractApproval,
    ContractBudget,
    ImplementationContract,
)
from dark_factory.context.sdd.baseline import current_revision, init_baseline
from dark_factory.context.sdd.errors import BaselineMismatchError
from dark_factory.context.sdd.frontmatter import parse_frontmatter
from dark_factory.context.sdd.native import NativeChangeSetAdapter
from dark_factory.orchestration.policy.escalation import contract_entry_violation
from dark_factory.quality.gates import evaluate_specification_gate
from dark_factory.quality.gates.decision import (
    GateOverrideError,
    apply_override,
    write_gate_decision,
)

ROOT = Path(".factory")
YEAR = str(datetime.now(UTC).year)
CHANGE_ID = f"chg:my-product:{YEAR}:0001"
CHANGE_SLUG = "checkout-timeout"
REQ_ID = "req:my-product:checkout:timeout"

FRONTMATTER = """---
schema: dark-factory.dev/{kind}
id: {id}
type: {type}
title: {title}
product: my-product
status: proposed
change: {change_id}
---
"""

FACTORY_YAML = """schema: dark-factory.dev/factory/v1
product: my-product
title: My Product
"""

CHANGE_YAML = """schema: dark-factory.dev/change/v1
id: {change_id}
title: Checkout timeout
slug: checkout-timeout
product: my-product
kind: product-feature
risk_class: R1
status: draft
baseline:
  revision: "{revision}"
workflow:
  profile: product-feature
  version: "1.0"
owners:
  product: team-me
  technical: team-me
targets:
  - repository: my-product
    role: implementation
artifacts:
  intent: intent.md
  spec: spec/delta.yaml
  design: design/overview.md
  tasks: tasks/graph.yaml
  verification: verification/plan.yaml
  evidence: evidence/index.yaml
"""

INTENT_MD = """# Intent

Зависшее оформление заказа должно прерываться по таймауту, а не ожидать бесконечно.
"""

DESIGN_MD = (
    FRONTMATTER.format(
        kind="design/v1",
        id="design:my-product:checkout-timeout",
        type="design",
        title="Checkout timeout design",
        change_id=CHANGE_ID,
    )
    + "\nТаймаут задаётся конфигурацией сервиса и применяется на шаге подтверждения заказа.\n"
)

REQUIREMENT_MD = (
    FRONTMATTER.format(
        kind="requirement/v1",
        id=REQ_ID,
        type="requirement",
        title="Checkout timeout",
        change_id=CHANGE_ID,
    )
    + "\n"
    + """Оформление заказа, не завершившееся за отведённое время, прерывается по таймауту
с явной ошибкой для пользователя.

## Acceptance Criteria

- AC-1: оформление заказа прерывается по таймауту с понятным сообщением об ошибке.
- AC-2: длительность таймаута задаётся конфигурацией без изменения кода.
"""
)

DELTA_YAML = """baseline_revision: "{revision}"
operations:
  - operation: add
    target: req:my-product:checkout:timeout
    artifact: requirements/REQ-001-checkout-timeout.md
"""

TASKS_YAML = """schema: dark-factory.dev/task-graph/v1
tasks:
  - id: TASK-001
    title: Implement checkout timeout
    repository: my-product
    type: implementation
    satisfies:
      - req:my-product:checkout:timeout
    depends_on: []
"""

VERIFICATION_YAML = """schema: dark-factory.dev/verification-plan/v1
requirements:
  - requirement: req:my-product:checkout:timeout
    checks:
      - type: unit-test
      - type: acceptance-scenario
"""

EVIDENCE_YAML = """schema: dark-factory.dev/evidence-index/v1
evidence: []
"""


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def create_changeset(revision: str) -> Path:
    change_dir = ROOT / "changes" / YEAR / f"CHG-0001-{CHANGE_SLUG}"
    substitutions = {
        "change.yaml": CHANGE_YAML.format(change_id=CHANGE_ID, revision=revision),
        "intent.md": INTENT_MD,
        "design/overview.md": DESIGN_MD,
        "spec/delta.yaml": DELTA_YAML.format(revision=revision),
        "spec/requirements/REQ-001-checkout-timeout.md": REQUIREMENT_MD,
        "tasks/graph.yaml": TASKS_YAML,
        "verification/plan.yaml": VERIFICATION_YAML,
        "evidence/index.yaml": EVIDENCE_YAML,
    }
    for name, content in substitutions.items():
        write(change_dir / name, content)
    return change_dir


print("== Шаг 1. Product Baseline (.factory/) ==")
init_baseline(ROOT, product="my-product", title="My Product", change="bootstrap")
revision = current_revision(ROOT)
print(f"baseline revision: {revision}")

print()
print("== Шаг 2. Proposal: ChangeSet (8 файлов под .factory/changes/) ==")
change_dir = create_changeset(revision)
adapter = NativeChangeSetAdapter(ROOT)
print(f"change set: {CHANGE_ID} -> {change_dir}")

print()
print("== Шаг 3. Спецификационный гейт: сначала сломанный вариант ==")
good = adapter.read_change(CHANGE_ID)
weakened = good.verification.model_copy(
    update={
        "requirements": [
            good.verification.requirements[0].model_copy(update={"checks": []})
        ]
    }
)
bad_decision = evaluate_specification_gate(good.model_copy(update={"verification": weakened}))
print(f"result: {bad_decision.result.value}")
for finding in bad_decision.findings:
    mark = "BLOCKING" if finding.blocking else "info"
    print(f"  [{mark}] {finding.axis.value}/{finding.code}: {finding.message}")

print()
print("== Шаг 4. Override: только человек, только для failed ==")
try:
    apply_override(bad_decision, decided_by=DecisionSource.AGENT, reason="agent says fine")
except GateOverrideError as exc:
    print(f"agent override rejected: {exc}")
overridden = apply_override(
    bad_decision, decided_by=DecisionSource.HUMAN, reason="release needs it now"
)
print(f"human override recorded, result stays: {overridden.result.value}")

print()
print("== Шаг 5. Гейт на полном proposal ==")
decision = evaluate_specification_gate(good)
print(f"result: {decision.result.value}; explanation: {decision.explanation}")
if decision.result is not GateStatus.PASSED:
    raise SystemExit("expected the full proposal to pass the gate")
write_gate_decision(change_dir, decision)
print(f"decision pinned to changeset revision: {decision.changeset_revision == revision}")
print(f"decision saved: {change_dir / 'gates' / 'specification.yaml'}")

print()
print("== Шаг 6. Что примет реализация (requirements snapshot) ==")
snapshot = asyncio.run(adapter.read_requirements(CHANGE_ID))
for entry in snapshot.requirements:
    print(f"  {entry.operation.value} {entry.id} (artifact: {entry.artifact})")

print()
print("== Шаг 7. Согласование принято: дельта уходит в baseline (reconciliation) ==")
new_revision = asyncio.run(adapter.apply_delta(CHANGE_ID, expected_revision=revision))
print(f"baseline revision: {revision[:12]}... -> {new_revision[:12]}...")
requirement_doc = ROOT / "product" / "requirements" / "REQ-001-checkout-timeout.md"
status = parse_frontmatter(requirement_doc.read_text(encoding="utf-8")).status
print(f"requirement in baseline: {requirement_doc.is_file()} (status: {status})")
print(f"reconciliation result: {(change_dir / 'reconciliation' / 'result.yaml').is_file()}")

print()
print("== Шаг 8. Повтор со старой ревизией = параллельное изменение ==")
try:
    asyncio.run(adapter.apply_delta(CHANGE_ID, expected_revision=revision))
except BaselineMismatchError as exc:
    print(f"rejected: {exc}")

print()
print("== Шаг 9. Без утверждённого контракта реализация не начнётся ==")
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
for label, candidate in (("no contract", None), ("contract not approved", contract)):
    violation = contract_entry_violation(candidate)
    print(f"{label}: {violation.rule.value}" if violation else f"{label}: allowed")
approved = contract.model_copy(
    update={
        "approval": ContractApproval(approved_by=Role.PRODUCT, decided_at=datetime.now(UTC))
    }
)
violation = contract_entry_violation(approved)
print("approved contract:", "allowed" if violation is None else violation.rule.value)
```

## Шаг 1. Запуск (главный шаг)

```bash
uvx --python 3.12 \
  --from git+https://github.com/vadagama/dark-factory-mvp@6410f2640d99b74f4cfb6478ddec106ff8c10dc0 \
  python us2_lab.py
```

- SHA в адресе фиксирует версию фабрики — прогон воспроизводим.
- В отличие от US1, здесь `factory`-команда не нужна: лаборатория вызывает машинные компоненты фабрики как библиотеку, а `uvx` создаёт ей изолированное окружение и запускает `python` уже с установленной фабрикой.

Ожидаемый вывод (полностью):

```
== Шаг 1. Product Baseline (.factory/) ==
baseline revision: 7f6437bae4dcd3fe506df6cdef482b083bbd42b1df98175924419f75c95d549e

== Шаг 2. Proposal: ChangeSet (8 файлов под .factory/changes/) ==
change set: chg:my-product:2026:0001 -> .factory/changes/2026/CHG-0001-checkout-timeout

== Шаг 3. Спецификационный гейт: сначала сломанный вариант ==
result: failed
  [BLOCKING] completeness/requirement_without_acceptance_scenario: requirement 'req:my-product:checkout:timeout' has no 'acceptance-scenario' check in the verification plan

== Шаг 4. Override: только человек, только для failed ==
agent override rejected: only a human may override a gate decision, got 'agent' (ADR-018)
human override recorded, result stays: failed

== Шаг 5. Гейт на полном proposal ==
result: passed; explanation: specification gate passed: no findings
decision pinned to changeset revision: True
decision saved: .factory/changes/2026/CHG-0001-checkout-timeout/gates/specification.yaml

== Шаг 6. Что примет реализация (requirements snapshot) ==
  add req:my-product:checkout:timeout (artifact: requirements/REQ-001-checkout-timeout.md)

== Шаг 7. Согласование принято: дельта уходит в baseline (reconciliation) ==
baseline revision: 7f6437bae4dc... -> e8af24d63977...
requirement in baseline: True (status: active)
reconciliation result: True

== Шаг 8. Повтор со старой ревизией = параллельное изменение ==
rejected: baseline revision mismatch: expected '7f6437bae4dcd3fe506df6cdef482b083bbd42b1df98175924419f75c95d549e', actual 'e8af24d639772c4868f9364907e755f9f1acfb3a90f8c1b640970a41c3829534'

== Шаг 9. Без утверждённого контракта реализация не начнётся ==
no contract: implementation_contract_unapproved
contract not approved: implementation_contract_unapproved
approved contract: allowed
```

Если все девять блоков совпали — сценарий пройден. Разбор — в следующем шаге.

## Шаг 2. Разбор: что произошло на каждом шаге

**Шаг 1. Product Baseline.** Фабрика создала `.factory/` — каноническое описание продукта: `factory.yaml` (идентичность) и `product/` (принятые артефакты). Ревизия baseline — это content-hash: детерминированный отпечаток всех файлов `product/`. Нет временных меток — Git владеет историей, ревизия вычисляется заново по содержимому.

**Шаг 2. Proposal.** В `.factory/changes/2026/CHG-0001-checkout-timeout/` появилось задание-предложение: `change.yaml` (манифест: профиль строгости `product-feature`, риск `R1`, статус `draft`, привязка к ревизии baseline), дельта `spec/delta.yaml` (одна операция `add` требования), документ требования с критериями приёмки, task-graph, план проверок и пустой индекс evidence. Это **дельта**, а не копия спецификации: фабрика запрещает «переписывать» baseline целиком.

**Шаг 3. Гейт ловит дыру.** Сначала мы оценили «сломанный» вариант — из плана верификации убрали проверку типа `acceptance-scenario`. Гейт ответил `failed` с блокирующей находкой: у требования нет критерия приёмки. Находки — это **данные** (ось `completeness`, код, сообщение), а гейт их взвешивает по политике: 12 кодов блокируют, остальные — предупреждения.

**Шаг 4. Override — привилегия человека.** Агент не может «продавить» failed-решение — фабрика отвечает отказом со ссылкой на ADR-018. Человек может записать override, но важно: **результат остаётся `failed`** — override фиксируется рядом с решением, а не отменяет его. Правильный путь — починить proposal (что и делает следующий шаг).

**Шаг 5. Гейт на полном proposal.** Со всеми проверками (`unit-test` + `acceptance-scenario`, задача `TASK-001`, трассировка `satisfies`, план верификации) гейт отвечает `passed` без находок. Решение записано в `gates/specification.yaml` с полями `policy`, `policy_version`, `changeset_revision` — решение **привязано к конкретной ревизии** ChangeSet. Смена ревизии делает прежнее решение чужим.

**Шаг 6. Snapshot требований.** `read_requirements` — то, что увидит реализация: одна операция `add` требования с артефактом. Это стык между US2 и US3: дальше работает только согласованный снапшот.

**Шаг 7. Reconciliation.** Дельта принята: требование скопировано в `product/requirements/` со статусом `active`, записан протокол `reconciliation/result.yaml` (какая ревизия была, какая стала, сколько операций). Ревизия baseline изменилась — продукт вырос.

**Шаг 8. Повтор — отказ.** Попытка применить ту же дельту со **старой** ревизией отклонена: `BaselineMismatchError`. Так машина ловит параллельные изменения: пока вы согласовывали, baseline уже ушёл вперёд — согласование устарело, начинайте новое изменение от актуальной ревизии.

**Шаг 9. Ворота реализации.** Напоследок — гейт входа в строительство (мост к US3): без контракта реализации и без его утверждения человеком вход запрещён (`implementation_contract_unapproved`), с утверждённым контрактом — разрешён. Implementation Contract — это одобренная человеком граница автономии: scope, критерии приёмки, риск-класс, бюджет.

## Шаг 3. Что фабрика записала (у вас в проекте)

```bash
find .factory -type f | sort
```

Три группы:

- `.factory/factory.yaml`, `.factory/product/product.md` — baseline;
- `.factory/changes/2026/CHG-0001-checkout-timeout/` — ChangeSet: задание, дельта, требование, задачи, план верификации, evidence-индекс, **гейт-решение** (`gates/specification.yaml`) и **протокол reconciliation** (`reconciliation/result.yaml`);
- `.factory/product/requirements/REQ-001-checkout-timeout.md` — требование, принятое в baseline.

Откройте гейт-решение:

```bash
cat .factory/changes/2026/CHG-0001-checkout-timeout/gates/specification.yaml
```

```
schema: dark-factory.dev/gate-decision/v1
policy: specification-gate
policy_version: '1.0'
gate: specification
changeset_id: chg:my-product:2026:0001
changeset_revision: 7f6437bae4dcd3fe506df6cdef482b083bbd42b1df98175924419f75c95d549e
risk_class: R1
result: passed
findings: []
evidence:
- design/overview.md
- evidence/index.yaml
- intent.md
- spec/delta.yaml
- tasks/graph.yaml
- verification/plan.yaml
explanation: 'specification gate passed: no findings'
decided_by: policy
```

На что смотреть: `changeset_revision` — решение действительно только для этой ревизии; `decided_by: policy` — машину проголосовала политика, а не агент; `evidence` — ссылки на артефакты, на которых решение стоит. Файлы в `.factory/` — yours: в реальной работе они живут в git вашего продуктового репозитория.

## Шаг 4. Эксперименты

- **Сломайте proposal на диске**: удалите из `verification/plan.yaml` проверку `acceptance-scenario` (в свежей папке) — гейт поймает то же самое, но теперь по-настоящему, а не на копии в памяти.
- **Прогоните скрипт второй раз в той же папке** — он остановится с `BaselineMismatchError` ещё до шага 8. Это не поломка: дельта уже принята в baseline, второй раз её принять нельзя.
- **Поднимите риск**: поставьте `risk_class: R2` в `change.yaml` и добавьте слот `reconciliation` — гейт потребует план reconciliation (для R2+ он обязателен).
- **Человеческий override** — попробуйте `apply_override` с `decided_by="policy"`: отказ. Override — всегда человек.

## Чек-лист «сценарий пройден»

- [ ] шаг 3: сломанный вариант — `failed`, находка `requirement_without_acceptance_scenario` с пометкой `BLOCKING`
- [ ] шаг 4: override агента отклонён; override человека записан, результат остался `failed`
- [ ] шаг 5: полный proposal — `passed`; `gates/specification.yaml` записан; `decision pinned to changeset revision: True`
- [ ] шаг 6: снапшот требований — одна операция `add`
- [ ] шаг 7: требование в baseline со `status: active`; `reconciliation/result.yaml` существует; ревизия изменилась
- [ ] шаг 8: повтор со старой ревизией — `BaselineMismatchError`
- [ ] шаг 9: без контракта и без approval — `implementation_contract_unapproved`; с approval — `allowed`

## Чего на этом участке конвейера пока нет

Чтобы не искать лишнего:

- **агента Product нет**: уточнение требований и авторинг спецификации через LLM появятся в следующих задачах (профили и skills уже лежат в `src/dark_factory/agents/`, но к конвейеру исполнители ещё не подключены);
- **публикации MR нет**: proposal пока просто папка в `.factory/` — GitHub-адаптер и MR-контур это US4 (T-030+);
- **CLI-команды и Console для согласования нет**: сегодня approval — это поле Implementation Contract и записи `GateDecision`; отдельная команда «согласовать ревизию» появится позже;
- **intake из трекера нет**: события Plane и Console-вход — US6;
- известная косметическая особенность: при reconciliation исходный frontmatter требования дублируется в принятом файле (фабрика читает первый блок, на работу не влияет).

## Справочник терминов

| Термин | Простыми словами |
|---|---|
| Product Baseline | каноническое описание продукта в `.factory/`: только принятые артефакты (`active`/`superseded`/`retired`) |
| ChangeSet | proposal изменения: дельта над baseline + задачи + верификация + evidence |
| Дельта (`delta`) | операции `add`/`modify`/`supersede`/`retire` над требованиями, не копия спецификации |
| Ревизия baseline | content-hash всех файлов `product/`; меняется при каждом принятом изменении |
| Спецификационный гейт | детерминированные проверки proposal по пяти осям: полнота, согласованность, политика, покрытие, evidence |
| `GateDecision` | записанное решение гейта: политика, ревизия, результат, находки, evidence |
| Находка (finding) | результат проверки: ось, код, сообщение, метка blocking |
| Override | запись о том, что человек сознательно прошёл мимо failed-решения; результат не отменяет |
| Reconciliation | перенос согласованной дельты в baseline со сменой ревизии |
| `BaselineMismatchError` | «baseline уже другой»: защита от параллельных изменений |
| Workflow profile | профиль строгости: `product-feature`, `bugfix-r0`, … — какой набор артефактов обязателен |
| `risk_class` | R0–R4: от него зависит набор обязательных артефактов и контроль человека |
| Implementation Contract | одобренная человеком граница автономной реализации: scope, критерии, бюджет, эскалации |

## Откуда взяты правила

- Официальный сценарий проверки — `specs/001-dark-factory-mvp/quickstart.md` (§3).
- Контракт ChangeSet — `specs/001-dark-factory-mvp/contracts/changeset.md`.
- Задачи фазы — `specs/001-dark-factory-mvp/tasks.md` (Phase 4 / US2, T014–T018).
- Каноническая модель SDD — `docs/sdd-native-core.md` (ADR-020); границы автономии и override — `docs/adr/ADR-018-*`.
- Шаблоны для реальных продуктовых репозиториев — `packs/product-baseline/` (T-022): тот же контракт, что в этой лаборатории.
