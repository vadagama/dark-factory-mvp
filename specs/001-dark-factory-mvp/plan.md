# Implementation Plan: Dark Factory MVP

**Branch**: `001-dark-factory-mvp` | **Date**: 2026-09-13 | **Spec**: [`spec.md`](./spec.md)

**Input**: Feature specification from `/specs/001-dark-factory-mvp/spec.md`

**Note**: Bootstrap-фаза Spec Kit (ADR-001). Канонический SDD — Native SDD Core (ADR-020); переход — в T-020. Этот план и его артефакты живут в `specs/001-dark-factory-mvp/` как bootstrap evidence и после T-020 не переписываются; артефакты актуализированы под Native SDD Core 2026-09-14.

## Summary

MVP «тёмной фабрики»: сквозной агентный конвейер `intake → спецификация → согласование → реализация → review/rework → merge → сборка → dev-деплой → smoke → обновление трекера` (spec.md). Технический подход уже зафиксирован ADR-001…ADR-019 и сведён в `docs/hld.md`; задачи и порядок — в `docs/plan.md` (T-001…T-091).

Ключевые технические решения, определяющие план:

- **Модульный монолит на Python 3.12** с hexagonal-границами (`ports`/`adapters`), проверяемыми CI (ADR-002, ADR-015 §3).
- **Два уровня оркестрации**: `pydantic-graph` только внутри стадии; между стадиями — собственный лёгкий durable workflow-core на неизменяемой таблице переходов и типизированных `StageResult`/`NextAction` (ADR-005).
- **Ephemeral execution**: pod на стадию + идемпотентный Reconciler CronJob; CI — durable substrate, не state store (ADR-006).
- **PostgreSQL — authoritative operational state**, Git — долгоживущая истина, CI artifacts — тяжёлая evidence (ADR-004, ADR-015 §4).
- **Идемпотентность по умолчанию**: operation/attempt/effect keys + durable effect ledger; at-least-once запуск и effectively-once side effects (ADR-006 §3).
- **Мультипровайдерный SC/CI**: GitHub-адаптер первым, GitLab — вторым; единый `ChangeRequestRef`; инвариант «один run — один провайдер» (ADR-019). Терминология: «MR» в `spec.md` — change request в широком смысле (GitHub PR / GitLab MR), канонический доменный тип — `ChangeRequestRef`.
- **Delivery через GitOps** в локальном Docker Desktop Kubernetes + Helm + Argo CD (ADR-010).
- **Канонический SDD — Native SDD Core** (ADR-020): ChangeSet + Product Baseline `.factory/`, дельты и reconciliation; Spec Kit — bootstrap до T-020, OpenSpec — compatibility-адаптер.

Этот план не переизобретает архитектуру: он переводит spec.md в исполнимую структуру (Technical Context, Constitution Check, Project Structure) и производит артефакты Phase 0/1 (`research.md`, `data-model.md`, `contracts/`, `quickstart.md`).

## Technical Context

**Language/Version**: Python 3.12 (`.python-version` = 3.12; `requires-python = ">=3.12"`); TypeScript/React для Console (ADR-002, ADR-014).

**Primary Dependencies**:
- Core/контракты: `pydantic` v2 (уже есть, `>=2.9`), `pyyaml` (`>=6.0.2`) — run records JSON/YAML.
- Агенты: `pydantic-ai` за `HarnessPort` (PydanticAI, ADR-002 §2); `pydantic-graph` внутри стадии (ADR-005 §1).
- State store: `sqlalchemy` 2.x + `alembic` (миграции) + `psycopg` 3 (PostgreSQL, ADR-004).
- API: `fastapi` + `uvicorn` (ADR-002).
- Наблюдаемость: `opentelemetry-sdk`/OTLP (ADR-009 §2).
- Конфигурация/секреты: env + Kubernetes Secret; без Vault в MVP (ADR-009).
- Console: React + TypeScript + Vite + Radix/shadcn + Storybook (ADR-014).

Конкретные версии, не закреплённые ADR, фиксируются при старте соответствующей задачи и попадают в `uv.lock` (см. `research.md` R-18).

**Storage**: PostgreSQL — authoritative operational state (`execution`, `stage`, `attempt`, `execution_leases`, `outbox`, `event_delivery`, effect ledger, usage) по ADR-004/006/016. Git-репозиторий `dark-factory-runs` — компактный immutable индекс evidence (`RunManifest`, итоговый `StageResult`, approvals, ссылки/digest). Тяжёлая evidence (логи, отчёты, SBOM, скриншоты) — CI artifacts через `ArtifactStorePort`. Продуктовая БД — данные пилота, **не** state store фабрики (spec.md, ADR-004).

**Testing**: `pytest` (уже есть) для unit + contract + integration; `ruff` (lint) и `mypy --strict` (typecheck) — обязательные гейты каждой задачи; contract-тест-сюита портов на фейках, затем против GitHub и GitLab (ADR-019 §6); e2e — T-072. Локальные команды совпадают с CI (ADR-019, T-002).

**Target Platform**: локальный Docker Desktop Kubernetes на MacBook (Apple Silicon, ARM64), 10–12 ГБ под Docker, concurrency=1 (ADR-010 §1–2); CI — GitHub Actions (self-hosted runners для агентных jobs, ADR-019). Пилот — веб-приложение (UI + API + БД продукта).

**Project Type**: модульный монолит (Python-пакет `src/dark_factory`) с несколькими точками входа (CLI, FastAPI API, React Console) и адаптерами за портами; отдельно — Helm chart и CI-шаблоны (ADR-002, ADR-008, ADR-015).

**Performance Goals**: один узел, concurrency=1 для тяжёлых агентных заданий; окно восстановления ограничено интервалом Reconciler CronJob (2–5 мин); webhook — ускоритель, не источник истины (ADR-006 §1/§10). SC-002: типовое небольшое изменение (границы ADR-018 — без публичного API, схемы данных, IAM, миграций; дифф ≤ 10 файлов и ≤ 200 строк) — от согласованной спецификации до принятого MR ≤ 1 рабочего дня.

**Constraints**: без постоянно работающего сервиса/воркера; at-least-once запуск + effectively-once side effects; 100% обязательных машинных проверок на итоговом SHA; статус «выпущено» — только после успешного smoke; `evidence_unavailable` запрещает успешный терминальный статус; секреты только через Secret/env; prod вне MVP (spec.md, ADR-006/009/011).

**Scale/Scope**: 1 оператор, 1 пилотный продукт, ≤2 параллельных агента внутри одного задания; каталог из 9 ролей (ядро MVP — Product/Develop/Quality, ADR-007); 7 гейтов MVP; таблица переходов на 5 стадий; ~91 запланированная задача (T-001…T-091, `docs/plan.md`).

**Действующие ограничения ADR-018 (границы автономной реализации)**: автономный цикл ограничен утверждённым Implementation Contract (scope, acceptance criteria, архитектурные ограничения, UI evidence, риск-класс, бюджет, правила эскалации). Выход за границы или эскалация (публичный API, схема данных, IAM, архитектурная граница) останавливают автономный цикл и требуют человека.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Источник — `.specify/memory/constitution.md` v3.0.0.

| Принцип | Гейт для этого плана | Статус | Обоснование |
|---|---|---|---|
| I. Specification-Driven Development | Фича проходит SDD-слой; артефакты коммитятся; ровно один канонический формат | ✅ PASS | Bootstrap Spec Kit (ADR-001) до T-020; Native SDD Core вводится в T-020 (ADR-020). Параллельных SDD-процессов нет. |
| II. Minimal Surgical Changes | Смена стека/зависимостей/структуры — только через ADR или явное согласование | ✅ PASS | Стек и структура заданы ADR-002/008/015. Новых стековых решений план не вводит. |
| III. Evidence-Based Validation | Каждое изменение валидируется фактически: тесты + диагностика | ✅ PASS | DoD каждой задачи — `ruff` + `mypy --strict` + `pytest`; `quickstart.md` задаёт проверяемые сценарии. |
| IV. Governance via ADR & Docs-First | Значимые решения — ADR до реализации; противоречия не допускаются | ✅ PASS | Все значимые решения уже приняты (ADR-001…020, HLD). План новых ADR не требует; при выявлении — заводится до кода. |
| V. Safety & Git Discipline | Секреты вне git; git-цикл задачи (ветка → проверка → MR); Conventional Commits | ✅ PASS | Секреты — k8s Secret/env (ADR-009); работа — в ветке задачи, MR против `main`, merge — человек (ADR-011, `docs/development-workflow.md`). |

**Итог гейта Phase 0**: PASS. Нарушений, требующих обоснования в Complexity Tracking, нет.

*Post-Phase 1 re-check*: PASS — артефакты Phase 1 не вводят новых зависимостей, стека или границ за пределами ADR; см. раздел ниже.

## Project Structure

### Documentation (this feature)

```text
specs/001-dark-factory-mvp/
├── plan.md              # Этот файл (/speckit-plan)
├── research.md          # Phase 0 (/speckit-plan)
├── data-model.md        # Phase 1 (/speckit-plan)
├── quickstart.md        # Phase 1 (/speckit-plan)
├── contracts/           # Phase 1 (/speckit-plan)
│   ├── ports.md         #   Python Protocol-контракты портов
│   ├── cli.md           #   контракт Factory Runner CLI
│   ├── api.md           #   контракт FastAPI API
│   ├── events.md        #   контракт outbox-событий
│   └── changeset.md     # контракт артефактов ChangeSet (ADR-020)
├── checklists/          # /speckit-checklist
└── tasks.md             # Phase 2 (/speckit-tasks — НЕ создаётся этим планом)
```

### Source Code (repository root)

Репозитории и их границы — по ADR-015. Ниже — структура репозитория `dark-factory` (§6 HLD).

```text
src/dark_factory/            # модульный монолит (src-layout, ADR-002)
├── changes/                 # домен изменения (T-003 — реализовано)
│   ├── enums.py             #   перечисления контрактов (wire-стабильные)
│   ├── run.py               #   Change/ChangeRun/StageRun/StageResult + переходы
│   ├── next_action.py       #   закрытое дискончированное объединение NextAction
│   ├── refs.py              #   ArtifactRef/Evidence/ChangeRequestRef/RepositoryRef
│   ├── findings.py          #   Finding/GateResult
│   ├── usage.py             #   Usage/BudgetSnapshot
│   ├── keys.py              #   ключи идемпотентности (ADR-006 §3)
│   └── run_records.py       #   RunManifest/RunRecord, JSON/YAML round-trip
├── orchestration/           # межстадийный Flow: переходы, гейты, маршруты (T-004)
├── agents/                  # ролевые профили как сменные исполнители (T-011)
├── context/                 # ContextBundle: сбор и фиксация (T-012)
├── execution/               # провайдеры исполнения агентной работы (ADR-006)
├── quality/                 # детерминированные гейты качества (T-013)
├── ports/                   # Protocol-контракты внешних и внутренних портов (T-005)
├── adapters/                # реализации портов; импортируют только порты (ADR-015 §3)
│   ├── harness/             #   PydanticAI (T-010)
│   ├── scm/                 #   GitHub (T-030), GitLab (T-034)
│   ├── tracker/             #   Plane + NoOp (T-033)
│   └── telemetry/           #   OTLP (T-060)
├── api/                     # FastAPI: runs, evidence, approvals (T-050)
├── cli/                     # Factory Runner — точка входа стадии (T-004/T-005)
├── console/                 # React Console (T-051, ADR-014)
├── flows/                   # определения Flow-профилей (T-004)
├── rules/                   # правила/политики (merge policy и др.)
└── packs/                   # загрузчик манифестов FactoryPack (T-070)

migrations/                  # Alembic-миграции схемы состояния (T-006, ADR-004)
charts/dark-factory/         # Helm chart (T-042, ADR-010)
.github/workflows/           # CI-шаблоны провайдера репозитория (T-031, ADR-019)

tests/
├── unit/
├── contract/                # единая контрактная сюита портов: fake → GitHub → GitLab
└── integration/             # БД, outbox, reconciler, e2e-пилот (T-072)
```

**Structure Decision**: выбран **модульный монолит** (Option 1) с несколькими точками входа, потому что стек и границы модулей уже утверждены ADR-002 и ADR-015 §3. Модульный монолит даёт один релиз и lockfile, а границы (адаптеры → только порты; ядро не импортирует адаптеры) проверяются `tests/test_import_boundaries.py` (правила A/B), а не декларируются. Console (React) и Helm chart живут в том же репозитории, но отдельными деревьями (`console/`, `charts/`), как предусмотрено §6 и §16 HLD. Варианты «backend/frontend как отдельные сервисы» и «mobile+API» отклонены: MVP — один оператор, один пилот, локальный контур (ADR-010, ADR-015).

## Complexity Tracking

> Нарушений Constitution Check нет. Таблица фиксирует только отклонения, которые *могли бы* выглядеть как усложнение, и их обоснование.

| Отклонение | Почему нужно | Почему более простая альтернатива отклонена |
|---|---|---|
| PostgreSQL как state store (доп. инфраструктура) | Authoritative operational state, lease/fencing, outbox, effect ledger — конкурентность и идемпотентность между ephemeral jobs | Git-only/файловое состояние не даёт транзакционного outbox, lease и optimistic concurrency (`state_revision`) — ADR-004 |
| `pydantic-graph` внутри стадии + собственный workflow-core между стадиями | Локальная параллельность (branch/join ≤2) внутри стадии; детерминированная типизированная таблица переходов между стадиями | pydantic-graph на всём Flow не даёт меж-job durability и закрытости переходов; Temporal отклонён (ADR-003/005, Q-2/Q-4) |
| Каталог из 9 ролей (при ядре из 3) | Роли — версионированные профили, а не сервисы; расширение плагинами без правок ядра | Роли-сервисы и полный состав с первого дня — лишняя сложность; урезание до 3 ломает маршруты по риску (ADR-007) |

## Phases & Ordering (связь со spec.md и docs/plan.md)

Разбивка задач — `docs/plan.md`; здесь — соответствие user stories спецификации и этапам.

| Спецификация | Этап `docs/plan.md` | Ключевые задачи |
|---|---|---|
| US1 walking skeleton (P1) | Этап 0 — решения и каркас | T-001…T-006 (домен, Flow, порты, схема состояния) |
| US2 задача → согласованная спецификация (P2) | Этапы 1, 2 | T-011 (Product), T-012 (context), T-020/T-021/T-022 (SDD, spec gate) |
| US3 реализация + review + rework (P3) | Этап 1 | T-010 (harness), T-013 (Quality), T-014 (rework), T-015 (parallel), T-016 (Implementation Contract) |
| US4 автономный MR-контур и merge (P4) | Этап 3 | T-030/T-031/T-032 (GitHub, CI jobs, merge policy), T-063/T-064 (reconcile, outbox) |
| US5 релиз в dev через GitOps (P5) | Этап 4 | T-040…T-045 (K8s, runners, Helm, Argo CD, OCI, smoke) |
| US6 контекст, трекер, Console, наблюдаемость (P6) | Этапы 5, 6 | T-050/T-051 (API, Console), T-033 (tracker), T-060/T-061 (OTel, run records) |
| Метрики и пилот | Этапы 7, 8 | T-070…T-073 (пилот), T-080…T-091 (расширения) |

Порядок и критические пути — `docs/plan.md` §2/§4. Каждая задача валидируется DoD: `ruff` + `mypy --strict` + `pytest` зелёные локально и в CI.
