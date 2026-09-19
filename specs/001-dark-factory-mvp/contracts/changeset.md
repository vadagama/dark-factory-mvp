# Contract: ChangeSet artifacts

**Feature**: `001-dark-factory-mvp` | **Date**: 2026-09-13 | **Updated**: 2026-09-19 | **Plan**: [`../plan.md`](../plan.md) | **ADR**: ADR-020, ADR-039

Каноническая SDD-модель — Native SDD Core (ADR-020, полная спецификация — [`docs/sdd-native-core.md`](../../../docs/sdd-native-core.md)). Каждая разработка — ChangeSet со стабильным ID и цепочкой `Intent → Spec → Design → Tasks → Verification → Evidence → Reconciliation`; артефакты ChangeSet — `.factory/changes/<year>/<CHG-NNNN-slug>/`, канонический Product Baseline — `.factory/product/`. ChangeSet содержит дельту (`add/modify/supersede/retire`) относительно baseline и **не принимает решение о прохождении гейта**: гейт T-021 работает поверх нормализованного контракта ChangeSet и фиксирует `GateDecision` (ADR-020).

> В bootstrap-фазе (до T-020) действующий инструмент — Spec Kit (ADR-001), артефакты — `specs/<фича>/`. Этот договор описывает целевую форму; `SpecKitAdapter` обеспечивает bootstrap-импорт legacy-артефактов, `OpenSpecAdapter` — compatibility import/export (ADR-020 п.8).

## Артефакты ChangeSet

```text
.factory/changes/<year>/<CHG-NNNN-slug>/
├── change.yaml          # манифест и точка входа: id, status, workflow profile, risk_class, artifacts
├── intent.md            # зачем: цель, scope / out of scope, ограничения
├── spec/
│   ├── delta.yaml       # операции над baseline: add / modify / supersede / retire
│   ├── requirements/    # требования и acceptance criteria
│   ├── scenarios/       # сценарии
│   ├── acceptance/      # критерии приёмки (если отдельные узлы)
│   └── constraints/     # ограничения
├── design/              # целевое решение (фазы «Архитектура» и «Интерфейс», ADR-039)
│   ├── overview.md      # обзор архитектуры: frontmatter `ui: required | not_required` + `ui_reason`; тело — обзор с mermaid-схемой, компоненты, ссылки на ADR
│   ├── decisions/
│   │   └── ADR-NNN-<slug>.md   # решение: frontmatter `status: proposed`, `impact: [...]`; секции Контекст / Решение / Обоснование / Альтернативы / Последствия
│   └── ui/
│       ├── scenarios/
│       │   └── SCN-NNN-<slug>.md   # сценарий: `steps[{id: S<n>, text, screen: SCR-…}]`
│       └── screens/
│           └── SCR-NNN-<slug>.md   # экран: `route`, `preview_url`, `states{loading, empty, error, success, access}`, `elements[{id: EL-…, kind, label, component}]`, `transitions[{to, trigger, condition}]`
├── contracts/           # openapi / asyncapi / schemas (если изменение затрагивает контракты)
├── tasks/
│   ├── graph.yaml       # канонический TaskGraph: зависимости, repository, satisfies
│   └── tasks.md         # generated view графа для человека
├── verification/
│   ├── plan.yaml        # план проверок, определяемый до реализации
│   └── acceptance.md    # результаты приёмки
├── evidence/
│   └── index.yaml       # ссылки, digest, provenance; тяжёлое — CI artifacts / Object Storage
├── gates/               # GateDecision по стадиям: specification, design, implementation, release
└── reconciliation/
    ├── plan.yaml        # план переноса принятых артефактов в baseline
    └── result.yaml      # результат reconciliation
```

Пустые каталоги не создаются: состав артефактов определяется `workflow.profile` и `risk_class` из `change.yaml`.

## ID и workflow profile

- ID ChangeSet — стабильный и глобально адресуемый: `chg:<product>:<year>:<NNNN>`; каталог — `<CHG-NNNN>-<slug>`, slug английский, kebab-case, стабильный (например `CHG-0142-reservation-timeout`).
- Строгость задаёт `workflow.profile` (`bugfix-r0`, `product-feature`, `ui-research`, `architecture-change`, `repository-rebuild`, `platform-change`) и `risk_class` (R0–R4); профили `factory-sdd`/`product-sdd` не используются.
- Baseline живёт рядом с кодом (`.factory/` репозитория системы); для multi-repo продукта — в отдельном product-spec репозитории.
- **Один канонический формат в момент времени**; параллельные SDD-процессы запрещены (конституция, ADR-020).

## YAML frontmatter

Все самостоятельно адресуемые SDD-артефакты (requirement, scenario, constraint, design-узел, ADR и т.п.) имеют YAML frontmatter (`schema`, `id`, `type`, `title`, `product`, `status`, `change`, при необходимости `owner`, `relations`) и становятся узлами OKF-графа. Generated views (`tasks.md`, собранная спека) и вспомогательная документация могут его не иметь (ADR-020 §7–8).

### Узлы фазы проектирования (M3, ADR-039 п.6/п.8/п.10)

| Узел | `schema` / `type` | Ключи frontmatter и тело |
|---|---|---|
| `design/overview.md` | `dark-factory.dev/design/v1` / `design` | общие (`id: design:<product>:<slug>`, `title`, `product`, `status`, `change`) + `ui: required \| not_required` — предложение архитектора о фазе UI; `ui_reason` — основание (обязательно при `not_required`). Пропуск фазы подтверждает оператор решением `waived`; файл при этом не меняется |
| `design/decisions/ADR-NNN-<slug>.md` | `dark-factory.dev/adr/v1` / `adr` | общие (`id: adr:<product>:NNNN`) + `impact: [<область>, …]`; агент пишет `status: proposed`; `superseded` читается из frontmatter, `accepted`/`needs_revision` — производные статусы карточки, в git не пишутся. Тело: `## Контекст`, `## Решение`, `## Обоснование`, `## Альтернативы` (таблица `Вариант / Плюсы / Минусы / Почему не выбран` или подразделы `###`), `## Последствия`; английские заголовки допустимы |
| `design/ui/scenarios/SCN-NNN-<slug>.md` | `dark-factory.dev/ui-scenario/v1` / `ui_scenario` | `id: SCN-NNN`, общие + `steps: [{id: S<n>, text, screen?: SCR-NNN}]`; тело — описание сценария |
| `design/ui/screens/SCR-NNN-<slug>.md` | `dark-factory.dev/ui-screen/v1` / `ui_screen` | `id: SCR-NNN`, общие + `route?`, `preview_url?` (абсолютный — как есть, относительный — к `dev_url` baseline), `states: {loading, empty, error, success, access}` (необъявленное состояние показывается как «не описано»), `elements: [{id: EL-<slug>, kind, label, component}]` (компонент UIKit), `transitions: [{to: SCR-NNN, trigger, condition?}]`; тело — назначение экрана |

Идентификаторы `SCN-NNN`, `SCR-NNN`, `EL-<slug>` и шаги `S<n>` — стабильные якоря замечаний и вопросов (ADR-034 п.1): `context/artifacts.py: anchors_of` находит их и во frontmatter, и в теле; потерянный якорь — `anchor_state: detached`. Шаблоны узлов — `packs/product-baseline/changeset/design/**`.

## Нормализованный контракт для гейта (T-021)

Внутренний контракт, поверх которого работает машинный spec-gate:

| Ось | Что проверяется |
|---|---|
| completeness | intent/spec/design-артефакты профиля присутствуют; критерии приёмки проверяемы |
| consistency | дельта не противоречит себе и каноническому Product Baseline (baseline revision) |
| policy compliance | risk_class, гейты и участие человека соответствуют ADR-011/018 |
| coverage | каждое требование покрыто задачей (`tasks/graph.yaml`) и проверкой (`verification/plan.yaml`) |
| evidence | для завершённого изменения есть evidence в `evidence/index.yaml`; `required && !available` запрещает успех |

**Наличие артефакта ≠ пройденный гейт**: гейт вычисляет результат и фиксирует `GateDecision` (policy и её версия, проверенная revision ChangeSet, результат, evidence, объяснение, агент/человек, разрешённый override); артефакт — вход, не доказательство прохождения.

## Жизненный цикл

```text
draft → proposed → specified → designed
→ ready → accepted → reconciled → closed
```

- Семантическое состояние хранится в `change.yaml` (Git); runtime-состояние (job, lease, retry, stage attempt, техническая ошибка) — в PostgreSQL (ADR-004).
- Каталог ChangeSet не перемещается между `active` и `archive`: путь стабилен, состояние — в `change.yaml`.
- Изменение спецификации фиксируется ревизией baseline; approval связан с ревизией и инвалидируется при её смене (FR-003).
- После acceptance reconciliation проверяет исходную baseline revision, обнаруживает параллельные изменения, применяет дельту (`add/modify/supersede/retire`), переводит принятые артефакты в `active` и фиксирует новую baseline revision; результат — `reconciliation/result.yaml`.
- Реализация разрешена только для согласованной ревизии спецификации (FR-003).

## Трассируемость

Цепочка «задача → спецификация (SHA) → план → код (SHA) → проверки → релиз» (SC-007) восстанавливается по связям OKF-узлов (frontmatter `id`/`relations`) между ID ChangeSet, ревизией baseline, `RunManifest` и `RunRecord`.
