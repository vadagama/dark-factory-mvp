---

description: "Task list for Dark Factory MVP implementation"
---

# Tasks: Dark Factory MVP

**Feature**: `001-dark-factory-mvp` | **Spec**: [`spec.md`](./spec.md) | **Plan**: [`plan.md`](./plan.md)

**Input**: Design documents from `/specs/001-dark-factory-mvp/`

**Prerequisites**: `plan.md` (tech stack, structure), `spec.md` (user stories P1–P6), `research.md`, `data-model.md`, `contracts/`, `quickstart.md`, `.specify/memory/constitution.md` v3.0.0

**Тесты**: Отдельные тест-задачи не генерируются — спецификация не запрашивает TDD. Тесты входят в DoD каждой задачи (`ruff` + `mypy --strict` + `pytest`, см. `docs/plan.md` §6) и исполняются как часть реализации (unit, contract, integration, crash-тесты).

**Организация**: Задачи сгруппированы по user stories из `spec.md` для независимой реализации и проверки. Якорь `[docs T-0NN]` в описании — канонический реестр задачи в `docs/plan.md`; ID здесь (`T0NN`) — порядок исполнения в этом файле и не совпадает с `T-0NN` из реестра.

**Traceability**: `T0NN` (этот файл) ↔ `T-0NN` (`docs/plan.md`). Ни одна задача реестра P0–P3 не выпадает.

## Формат: `[ID] [P?] [Story] Описание`

- **[P]**: можно выполнять параллельно с другими задачами той же волны (разные файлы); перечисленные в задаче зависимости всё равно должны быть выполнены до её старта
- **[Story]**: `[US1]`…`[US6]` — принадлежность к user story `spec.md`
- Каждая задача содержит конкретный путь к файлу и ссылку на контракт/ADR

## Path Conventions

- Модульный монолит (src-layout, ADR-002): код — `src/dark_factory/<модуль>/`, тесты — `tests/{unit,contract,integration}/`.
- Миграции — `migrations/`; Helm — `charts/dark-factory/`; CI — `.github/workflows/`; деплой — `deploy/`; пакеты — `packs/`.
- Документы фичи — `specs/001-dark-factory-mvp/`.

**Состояние на старт**: `T001`–`T003` (`docs T-001`…`T-003`) уже выполнены (✅ 2026-09-13), остальные — в работе.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Решения, каркас репозитория и доменные контракты. Выполнено до этой итерации.

- [x] T001 [docs T-001] Принять и зафиксировать ADR-пакет по открытым архитектурным вопросам (ADR-002…ADR-019), включая стек, PostgreSQL state store, merge policy, трекер, UI pack, границы репозиториев — `docs/adr/`
- [x] T002 [docs T-002] Scaffolding репозитория и базовый CI: пакет `dark_factory` (src-layout, 8 модулей HLD), `pyproject.toml` (uv, ruff, mypy strict, pytest), тест границ импортов (ADR-015 §3), pipeline lint+typecheck+test — `pyproject.toml`, `src/dark_factory/`, `tests/`, `.github/workflows/ci.yml`
- [x] T003 [docs T-003] Доменная модель изменения: `Change`, `ChangeRun`, `StageRun`, immutable `StageResult`, `NextAction` (закрытое объединение из 8 вариантов), `ArtifactRef`, `Evidence`, `ChangeRequestRef`/`RepositoryRef`, `Decision`, `Finding`, `GateResult`, `Usage`, `BudgetSnapshot`, `RunManifest`/`RunRecord` + `schema_version`; таблицы переходов run/stage (`apply_status`), инварианты завершения (ADR-009 §9), key-функции ADR-006 §3, JSON/YAML round-trip — `src/dark_factory/changes/`

**Checkpoint**: Каркас и контракты готовы — можно начинать Foundational.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Общее ядро, блокирующее все user stories: схема состояния, детерминированный Flow, порты и фейки.

**⚠️ CRITICAL**: Ни одна user story не начинается до завершения этой фазы.

- [x] T004 [docs T-006] Схема состояния PostgreSQL и Alembic-миграции: таблицы `execution`, `stage`, `attempt` со `state_revision`; `execution_leases` (`resource_type`, `resource_id`, `owner_id`, `fencing_token` монотонный, `acquired_at`, `expires_at`, `heartbeat_at`); `outbox` (envelope: `eventId`, `eventType`, `eventVersion`, `occurredAt`, `changeId`, `runId`, `stage`, `aggregateId`, `aggregateVersion`, `correlationId`, `causationId`, `artifactRefs`, `payload`) и `event_delivery` (`event_id`, `consumer_id`, `status`, `attempts`, `next_attempt_at`, `last_error`); durable effect ledger (`effect_key` уникален, статусы `planned/in_progress/succeeded/unknown`, `external_ref`); `usage`-агрегаты. Инварианты покрыть тестами: уникальность `operation_key`/`effect_key`, уникальность пары (`event_id`, `consumer_id`), монотонность `fencing_token`, атомарность «изменение состояния + запись в outbox»; crash-тесты `crash-before-call` и `crash-after-call-before-commit` не создают второй внешний эффект (ADR-004, ADR-006 §3, ADR-016) — `migrations/`, `src/dark_factory/orchestration/state/`, `tests/integration/`
- [x] T005 [docs T-004] Factory Flow: стадии (`specification → planning → construction → review_verification → release`), 7 гейтов MVP (`specification`, `planning`, `code`, `ui`, `review`, `verification`, `release`), маршруты `quick`/`standard`, TaskGraph (pydantic-graph) только внутри стадии, меж-job продолжение через `StageResult`/`NextAction`, лимиты rework/бюджета на уровне Flow, стоп-условия → `Blocked`. DoD: таблица переходов покрыта целиком; обработчики всех 8 вариантов `NextAction` проходят exhaustiveness (`assert_never`); переход вне таблицы невозможен в рантайме; мёртвых рёбер нет (ADR-005) — `src/dark_factory/orchestration/`, `src/dark_factory/flows/`, `src/dark_factory/rules/`
- [x] T006 [docs T-005] Порты (`Protocol`) и in-memory фейки: `RepositoryPort`/`MergeRequestPort`/`PipelinePort` (единый `ChangeRequestRef`, ADR-019), `TrackerPort`, `HarnessPort`, `ArtifactStorePort`, `TelemetryPort`, `WorkflowEnginePort` (`start`/`resume`/`cancel`/`get_status` с `idempotency_key` и `expected_revision`), `ReconciliationService`, `EventPublisherPort`. Все mutating-методы принимают `idempotency_key`/`effect_key` и возвращают `external_ref`. DoD: contract-тесты каждого порта на фейках; ни один модуль ядра не импортирует внешний SDK (ADR-015 §3); `KnowledgePort`/`ExecutionPort` (T015), `CIPort` (T024), `SDDPort` (T016) вводятся в своих задачах — не авансом — `src/dark_factory/ports/`, `src/dark_factory/adapters/`, `tests/contract/`, `tests/test_import_boundaries.py`

**Checkpoint**: Схема, Flow и порты готовы — user stories можно начинать параллельно.

---

## Phase 3: User Story 1 - Исполнение одной стадии фабрики (walking skeleton) (Priority: P1) 🎯 MVP

**Goal**: Оператор запускает одну стадию (без LLM) локально через CLI и в CI, получает типизированный `StageResult` и запись запуска; процесс не требует постоянно живущего сервиса.

**Independent Test**: `uv run factory doctor --json` и `uv run factory stage run --change ./fixtures/chg_smoke.yaml --stage construction --json` (quickstart §2) дают валидный `StageResult` (`schema_version: 1`); повтор с той же `operation_key` не создаёт второй внешний эффект; отключённый LLM-эндпоинт не мешает детерминированным шагам.

### Реализация для User Story 1 (слой `cli/` поверх Foundational)

- [x] T007 [P] [US1] Factory Runner CLI: точка входа и разбор команд `stage run`, `stage resume`, `run status`, `reconcile`, `outbox dispatch`, `doctor` по контракту `contracts/cli.md` (опции `--change`, `--stage`, `--route`, `--input-revision`, `--run-id`, `--json`, `--evidence-dir`, `--non-interactive`) — `src/dark_factory/cli/__main__.py`, `src/dark_factory/cli/main.py`
- [x] T008 [US1] `factory doctor`: проверка окружения и конфигурации, отсутствие секретов в выводе, exit-код 2 при невалидной конфигурации — `src/dark_factory/cli/doctor.py`
- [x] T009 [US1] `factory stage run`: фиксация снапшота входа до агентной работы (FR-001), вычисление `input_revision` → `operation_key` (ADR-006 §3), исполнение стадии, сериализация immutable `StageResult` (`schema_version: Literal[1]`, `status` только `waiting|succeeded|failed|blocked`), маппинг exit-кодов 0/10/20/1/2 — `src/dark_factory/cli/stage.py`
- [x] T010 [US1] Детерминированные шаги стадии без LLM: сборка контекста, машинные проверки, агрегация, решение о доработке (детерминированный путь не обращается к harness) — `src/dark_factory/orchestration/stages/`
- [x] T011 [US1] Персистенция записи запуска: `RunRecord` (`schema_version` + `manifest` + `change` + `run` + `stage_results` + `decisions`) в `--evidence-dir`/артефакт; immutable refs, без `latest` (ADR-015 §5) — `src/dark_factory/cli/run_records.py`
- [x] T012 [US1] Идемпотентность запуска: повтор `stage run` с той же `operation_key` не создаёт второй внешний эффект; `find_existing` перед созданием, сверка эффекта перед повтором (FR-017, ADR-006 §3) — `src/dark_factory/orchestration/idempotency.py`, `tests/integration/`
- [x] T013 [US1] Fixture и паритет локально/CI: `fixtures/chg_smoke.yaml`; job, запускающий тот же релиз ядра тем же CLI (US1 scenario 2), `--non-interactive` в CI; `doctor` + `stage run` в pipeline — `fixtures/chg_smoke.yaml`, `.github/workflows/ci.yml`

**Checkpoint**: US1 полностью функциональна и проверяется независимо (quickstart §2).

---

## Phase 4: User Story 2 - От задачи к согласованной спецификации (Priority: P2)

**Goal**: Задача нормализуется в снапшот, Product публикует MR с proposal'ом изменения; реализация разрешена только для согласованной ревизии спецификации.

**Independent Test**: Подать задачу → получить MR с ChangeSet'ом (`.factory/changes/<year>/<id>/`) → согласовать конкретную ревизию (SHA) → убедиться, что `construction` без approval не стартует, а смена ревизии инвалидирует approval.

- [x] T014 [US2] [docs T-011] Профили ядра Product/Develop/Quality и минимальные skills (`intake`, `requirements-refinement`, `spec-авторинг`, `implementation`, `implementation-rework`, `code-review`, `acceptance-verification`, `change-request`); версионированные манифесты (входы/выходы, tools, ограничения, stop-conditions) + контракт `AgentProfile → TaskEnvelope → AgentResult` (ADR-007) — `src/dark_factory/agents/profiles/`, `src/dark_factory/agents/skills/`. Зависит от T015 (docs T-012) и T019 (docs T-010 — harness, US3, interleaved: T019 до T014).
- [x] T015 [P] [US2] [docs T-012] `ContextBundle`: сбор источников (`repo`, `specs/` до T-020 → `.factory/` после, конституция, ADR, engineering pack) с provenance, revision/hash; версионирование набора на запуск; изолированный worktree. Ввести `KnowledgePort` и `ExecutionPort` (contract-тесты на фейках); bundle воспроизводим — одинаковый hash на одинаковых входах — `src/dark_factory/context/`, `src/dark_factory/ports/`
- [ ] T016 [US2] [docs T-020] Контрольная точка Native SDD Core (ADR-020, `docs/sdd-native-core.md`): Product Baseline `.factory/` (`product/` + `changes/`), ChangeSet с манифестом `change.yaml` (workflow profile, risk_class) и дельтой `spec/delta.yaml` (`add/modify/supersede/retire`), reconciliation к baseline; `SDDPort` с `NativeChangeSetAdapter` (основной), `SpecKitAdapter` (bootstrap-импорт legacy `specs/`) и `OpenSpecAdapter` (compatibility, ADR-020 п.8); строгость по типу задачи (fix — без SDD-артефактов); `.specify/` и `specs/` — read-only bootstrap evidence. Зависит от T005, T014 — `src/dark_factory/context/sdd/`, `.factory/`
- [ ] T017 [US2] [docs T-021] Specification gate: детерминированные проверки (наличие AC, трассировка AC → сценарий → задача, противоречия scope/out-of-scope, связность с конституцией), blocking/non-blocking; результат фиксируется как `GateDecision` (policy и версия, revision ChangeSet, результат, evidence, объяснение, агент/человек, разрешённый override — ADR-020); поле `risk_class` из Implementation Contract (T023) в нормализованном контракте ChangeSet. Зависит от T016 (T-020), T023 (T-016, US3 — interleaved) — `src/dark_factory/quality/gates/`
- [ ] T018 [US2] [docs T-022] Product Baseline/шаблоны ChangeSet для продуктовых репозиториев (`.factory/`: `factory.yaml`, `product/`, `changes/`; frontmatter-профили артефактов): правила минимальных изменений и evidence-based validation; проверка через `NativeChangeSetAdapter` (ADR-020 п.8). Зависит от T016 (T-020), T041 (T-070) — `packs/`

**Checkpoint**: US2 проверяется независимо — реализация блокируется без version-bound approval.

---

## Phase 5: User Story 3 - Агентная реализация с независимым review и ограниченным rework (Priority: P3)

**Goal**: На согласованной спецификации — реализация в изолированном workspace, независимый review отдельным контекстом, ограниченный rework в том же MR, машинные проверки на итоговом SHA.

**Independent Test**: На согласованной спецификации одного небольшого изменения пройти цикл реализация → review → rework → зелёный CI; проверить лимит раундов (≤3) и параллелизм ≤2.

- [x] T019 [P] [US3] [docs T-010] `HarnessPort` + PydanticAI-адаптер: model/tools/structured output, учёт usage (токены, стоимость), ограничения инструментов по профилю, LLM endpoint через существующий LiteLLM (ADR-002 §2, R-17) — `src/dark_factory/adapters/harness/`
- [ ] T020 [P] [US3] [docs T-013] Quality — независимая приёмка: контекст строится заново из закреплённой спецификации, актуального diff и evidence (не наследует Develop); `Finding` (severity/category/evidence/confidence); `GateResult` pass/fail/blocked для конкретного SHA; blocking/non-blocking; блокирующий finding останавливает merge; человеческие замечания MR включаются в общий набор `Finding` как данные, без влияния на права и гейты (FR-007) — `src/dark_factory/quality/`
- [ ] T021 [US3] [docs T-014] Bounded rework loop: `review → rework → re-review` с лимитом 3; новый attempt не перезаписывает прежнее состояние; новый SHA аннулирует прежний допуск; исчерпание лимита/повторяющаяся ошибка/конфликт требований → `Blocked` с диагностикой (условия эскалации — подмножество ADR-018 §5). Зависит от T005, T020 — `src/dark_factory/orchestration/`
- [ ] T022 [US3] [docs T-015] TaskGraph внутри стадии: branch/join для ≤2 независимых подзадач, агрегация через reducers, конкурентная запись в state через типизированные outputs. Зависит от T005 — `src/dark_factory/orchestration/`
- [ ] T023 [US3] [docs T-016] Implementation Contract и границы автономной реализации (ADR-018): (1) схема контракта (scope, acceptance criteria, архитектурные ограничения, UI evidence, риск-класс R0–R4, бюджет, правила эскалации); (2) классификатор Known/Bounded choice/New path; (3) машинно проверяемые условия эскалации (публичный API, схема данных, IAM, архитектурная граница, превышение бюджета, необратимая операция); (4) режимы участия человека по фазам → `human_gates` flow-профиля; (5) минимальный контракт риск-класса (LLM может повысить, понизить — только политика). DoD: изменение без утверждённого контракта не проходит в реализацию. Зависит от T005, T014 — `src/dark_factory/orchestration/policy/`, `src/dark_factory/agents/profiles/`

**Checkpoint**: US3 проверяется независимо — машинные проверки зелёные на итоговом SHA, лимиты срабатывают детерминированно.

---

## Phase 6: User Story 4 - Автономный MR-контур и merge по правилам (Priority: P4)

**Goal**: При включённом расписании сверки фабрика сама продолжает активные инициативы (CI/MR/замечания), запускает разрешённые попытки; merge — доверенный финализатор по правилам.

**Independent Test**: Включить reconcile и merge policy; провести инициативу от реализации до merge без ручного запуска промежуточных шагов; изменения вне разрешённого класса требуют человека; повтор webhook не создаёт дубль MR.

- [ ] T024 [US4] [docs T-030] GitHub-адаптер (первый провайдер MVP): `GitHubAdapter` реализует `RepositoryPort`/`MergeRequestPort`/`PipelinePort` и `CIPort`; ветки/worktree, push, CR (`ChangeRequestRef`), diff, комментарии/review, Checks API, rebase, merge через finalizer; GitHub App с короткоживущими installation tokens; webhooks — ускоритель. DoD: единая контрактная сюита зелёная + интеграционный тест против тестового репозитория (ADR-019). Зависит от T006 — `src/dark_factory/adapters/scm/github/`
- [ ] T025 [P] [US4] [docs T-031] CI-шаблоны фабрики (agent jobs): один job = одна стадия Flow, временный pod, сохранение `StageResult`/`NextAction`/usage в artifacts + run record, детерминированные jobs (build/test/lint/security), повторный проход гейтов на итоговом SHA. Зависит от T005, T024, T030 (T-041, US5 — interleaved) — `.github/workflows/`
- [ ] T026 [US4] [docs T-032] Merge policy MVP: protected branch, обязательные human approvals, обязательные checks на итоговом SHA, squash merge, запрет merge при непройденных гейтах; проверка неизменности ожидаемого SHA перед merge (FR-011); publisher/finalizer отделены от sandbox-исполнения агентов. Зависит от T024, T020 — `src/dark_factory/orchestration/policy/`, `src/dark_factory/rules/`
- [ ] T027 [US4] [docs T-063] Reconcile и recovery: идемпотентный CronJob (2–5 мин, `concurrencyPolicy: Forbid`), восстановление по PostgreSQL/Git/MR/StageResult, правила (истёкшая блокировка, дубликаты запусков, merged MR без run, approved+passed без merge, branch behind, повторяющаяся ошибка → эскалация); webhook — только ускоритель; разрешение рассинхрона «CI ↔ PostgreSQL» в пользу PostgreSQL после сверки. Зависит от T025, T024 — `src/dark_factory/orchestration/reconcile/`
- [ ] T028 [US4] [docs T-064] Outbox Dispatcher CronJob: единственный владелец доставки; резервирование через `FOR UPDATE SKIP LOCKED`/короткий lease; запуск внутренних обработчиков или CI pipeline; фиксация в `event_delivery`, exponential backoff, `dead` при исчерпании; порядок только в рамках `aggregateId`/`changeId`/`runId` по `sequence`; очистка outbox — только после терминального статуса всех обязательных потребителей и retention. DoD: at-least-once тест (повтор `eventId` не создаёт второй эффект), dead-letter и ручной replay. Зависит от T004 (T-006), T029 (T-040, US5 — interleaved) — `src/dark_factory/orchestration/events/`, `deploy/`

**Checkpoint**: US4 проверяется независимо — дубликаты не создаются, merge вне разрешённого класса требует человека.

---

## Phase 7: User Story 5 - Релиз в dev через GitOps (Priority: P5)

**Goal**: После merge — immutable digest, GitOps-изменение, применение в dev, smoke; статус «выпущено» только при успешном smoke.

**Independent Test**: После merge пилотного изменения проверить цепочку сборка → GitOps MR → deploy → smoke → «выпущено»; провал smoke не даёт статус «выпущено».

- [ ] T029 [P] [US5] [docs T-040] Bootstrap локального Kubernetes: Docker Desktop K8s, namespaces (`argocd`, `factory`, `ci`, `factory-runs`, `apps-dev`), quotas, service accounts, baseline NetworkPolicy deny-by-default + негативные egress-тесты; PostgreSQL фабрики (PVC, backup, автоматизированный restore-тест, MVP RPO/RTO) вместе с БД Plane логически раздельно; проверка доступности и учётных данных GitHub App; capacity smoke полного контура (idle + один e2e job). DoD: скрипт воспроизводим на чистом Docker Desktop (ADR-010) — `deploy/bootstrap/`
- [ ] T030 [P] [US5] [docs T-041] CI-раннеры (K8s executor) + безопасность job pods: self-hosted GitHub Actions runners в `ci`, pods только в `factory-runs` (non-root, read-only rootfs, без privileged/Docker socket/hostPath, `automountServiceAccountToken=false`, лимиты, active deadline, TTL cleanup, отдельные SA, закреплённые digests). DoD: pod создаётся/удаляется по TTL; agent job не имеет merge/deploy credentials. Зависит от T029 — `deploy/ci/`
- [ ] T031 [US5] [docs T-042] Helm chart dark-factory: Console/API с зависимостью PostgreSQL фабрики, values-local, ресурсы по профилю MacBook, probes, локальный ingress. DoD: helm lint + тестовый deploy в namespace `factory`. Зависит от T029 (T-040), T035 (T-050, US6 — interleaved) — `charts/dark-factory/`
- [ ] T032 [US5] [docs T-043] Argo CD + GitOps-репозиторий: установка Argo CD (Helm), read-only GitOps-репо, Applications для `factory` и `apps-dev`; поток merge → GitOps MR с immutable digest → sync. DoD: GitOps MR меняет digest, Argo разворачивает в `apps-dev`; откат — revert коммита. Зависит от T029 — `deploy/argocd/`, `dark-factory-gitops`
- [ ] T033 [US5] [docs T-044] OCI build job (immutable digest): доверенный build для итогового SHA без privileged DinD, SBOM, SAST/SCA/secrets/image scan, публикация immutable digest, запрет `latest` для promotion. DoD: образ по digest; повторная сборка того же SHA даёт тот же digest (или задокументированное отклонение). Зависит от T025 — `.github/workflows/`, `deploy/ci/`
- [ ] T034 [US5] [docs T-045] Smoke + release evidence: проверка неизменности ожидаемого digest перед деплоем (FR-011); health + smoke после Argo sync; release evidence (digest, Argo status, smoke result) в run record; неуспешный smoke не переводит изменение в Released. DoD: e2e — успешный и неуспешный smoke дают корректные статусы и evidence. Зависит от T032, T033 — `src/dark_factory/quality/release/`

**Checkpoint**: US5 проверяется независимо — 100% «выпущено» имеют успешный smoke на зафиксированном digest.

---

## Phase 8: User Story 6 - Контекст, знания, трекер и наблюдаемость (Priority: P6)

**Goal**: Контекст из закреплённых источников с фиксацией версий/provenance; синхронизация с трекером через сменный адаптер; Console показывает стадии, блокеры, стоимость, гейты, ссылки на evidence; полная трассируемость.

**Independent Test**: Для одной инициативы собрать `ContextBundle` (проверить фиксацию версий), синхронизировать статусы с Plane (без дублей), открыть Console (стадия, блокеры, стоимость, гейты, ссылки); отключение трекера не останавливает конвейер.

- [ ] T035 [P] [US6] [docs T-050] FastAPI API: `GET /runs`, `/runs/{id}`, `/runs/{id}/stage-results`, `/runs/{id}/trace`, `/changes`, `/changes/{id}`, `/changes/{id}/trace`, `/changes/{id}/approvals`, `/runs/{id}/evidence`, `/runs/{id}/gates`, `/runs/{id}/findings`; token AuthN (единый оператор + service-токены CI/CLI), version-bound approval `{actor, role, contract_hash/commit_sha, timestamp, decision}`, audit mutating-операций, `Idempotency-Key`, `409` при `state_revision` mismatch, RFC 7807-подобные ошибки. DoD: негативные AuthN-тесты, OpenAPI-схема. Зависит от T003, T006 — `src/dark_factory/api/`
- [ ] T036 [US6] [docs T-051] Console MVP (5 экранов): список изменений, карточка изменения с evidence и цепочкой стадий, гейты/approvals, бюджеты/лимиты, настройки/профили; режимы «с согласованиями» и «автономно до MR». DoD: vitest + Playwright smoke 5 сценариев; сборка chart'ом (T031). Зависит от T035 — `src/dark_factory/console/`
- [ ] T037 [P] [US6] [docs T-033] `TrackerPort` + адаптер Plane (self-hosted, webhook+HMAC): получение задачи, публикация статусов/ссылок, запрос уточнений; webhook — ускоритель, подпись + timestamp window, дедупликация по event ID, ротация секрета; до готовности — NoOp-заглушка; недоступность трекера не блокирует CLI/Console (FR-020). DoD: e2e создание задачи → Change → статус отражён. Зависит от T006 — `src/dark_factory/adapters/tracker/`
- [ ] T038 [P] [US6] [docs T-060] `TelemetryPort` + OpenTelemetry-корреляция: трейсы `change → run → stage → agent call → tool call → CI job → deployment`, атрибуты usage/cost, экспорт в job logs/artifacts (внешний backend опционально). DoD: по trace восстанавливается полная цепочка запуска. Зависит от T006 — `src/dark_factory/adapters/telemetry/`
- [ ] T039 [US6] [docs T-061] Run records в `dark-factory-runs` — **P0-минимум** (блокирует T043): `RunManifest` + итоговый `StageResult` + evidence-ссылки с первого e2e-прогона; запись идемпотентна (lookup по `change_id`). Полный протокол (partitioning, single-writer, sanitization, подпись manifest), retention и аналитика — P1. Зависит от T003, T024 — `dark-factory-runs`
- [ ] T040 [US6] [docs T-062] Бюджет-координатор и allowance: лимиты токенов/времени/стоимости на запуск и роль, allowance-политики, исчерпание → Awaiting Decision с диагностикой, совокупный бюджет в `RunSnapshot`. Зависит от T005, T019 — `src/dark_factory/orchestration/budget/`

**Checkpoint**: US6 проверяется независимо — трекер отключаем, Console показывает полную картину, trace восстанавливается.

---

## Phase 9: Pilot & Metrics (Cross-Story Validation)

**Purpose**: Пилотный продукт и сквозная валидация P0-цепочки (SC-001…SC-008).

- [ ] T041 [P] [docs T-070] Engineering pack пилотного продукта: blueprint (React + Small UIKit + FastAPI + PostgreSQL), CI-конфигурация продукта, Helm chart приложения, тестовые конвенции; pack версионируется. DoD: blueprint разворачивается в `apps-dev` через фабрику. Зависит от T032 (T-043) — `packs/web-app/`
- [ ] T042 [P] [docs T-071] Small UIKit минимум + UI-гейты: токены (DTCG), 10–12 компонентов + 5–7 паттернов, Storybook как живая спека; гейты UIKit-policy (ESLint), stylelint, axe/WCAG 2.2 AA, visual regression (Playwright); запрет прямых импортов вне `@small/ui`. Зависит от T041 — `packs/ui/`
- [ ] T043 [docs T-072] E2E-пилот: 10 реальных задач (5 quick, 5 standard) по сквозному сценарию intake → SDD → реализация → MR → CI/review → merge → image → GitOps → dev → smoke; сбор метрик vision §7 и метрик FR-024 на уровне инициативы (стоимость с учётом попыток, принятые с первого прохода, число раундов rework, время до принятого MR, число ручных вмешательств), журнал отклонений. DoD: ≥7/10 e2e без ручных правок артефактов агента; отчёт по SC-001…SC-008. Зависит от T023, T025, T026, T031, T033, T034, T035, T039, T041 — `dark-factory-runs`, `specs/001-dark-factory-mvp/quickstart.md`
- [ ] T044 [docs T-073] Отклонения пилота → backlog и eval-кейсы: каждый сбой — eval-case (вход/ожидание/факт) и элемент backlog; базовый eval-набор для промптов и skills; прогон локально и в CI, отчёт по регрессиям. Зависит от T043 — `evals/`

---

## Phase 10: Polish & Cross-Cutting Concerns (Extensions P1–P3)

**Purpose**: Расширения после стабилизации пилота (не блокируют MVP); финальная синхронизация документации.

- [ ] T045 [P] [docs T-080] Риск-классы R0–R4 и точки контроля: классификация, обязательные гейты и участие человека по классу, 4 точки контроля, «LLM повышает, понижает — только политика», маршруты architecture/foundation; машинное условие эскалации R2+. DoD: маршрутизация детерминирована, опасные изменения не проходят по короткому пути. Зависит от T005, T023, T040 — `src/dark_factory/orchestration/policy/`
- [ ] T046 [P] [docs T-081] Роли Design и Architect + гейты UI/Planning: UX-flow/состояния/UIKit/accessibility и impact/контракты/данные/NFR/ADR-proposal. Зависит от T014, T042 — `src/dark_factory/agents/profiles/`
- [ ] T047 [P] [docs T-082] Роли Infrastructure, Security, CI/CD, Operation: инфраструктурные изменения (Helm/K8s), security-анализ (IAM/secrets/зависимости), pipeline/поставка, эксплуатация (smoke, rollback-анализ), release-гейт с Operation-вердиктом. Зависит от T014, T034 — `src/dark_factory/agents/profiles/`
- [ ] T048 [P] [docs T-084] Learning loop (минимальный контур): сигнал → RuleProposal/SkillProposal → проверка на eval-наборе (Pydantic Evals) → MR с независимым review; защита holdout-набора, сравнение с baseline. Зависит от T044, T039 — `src/dark_factory/learning/`
- [ ] T049 [P] [docs T-085] Auto-merge низкого риска (policy-controlled): по статистике T043 — политика для R0/R1 при зелёных гейтах, audit-log оснований, лёгкое отключение. Зависит от T045, T043 — `src/dark_factory/orchestration/policy/`
- [ ] T050 [P] [docs T-086] Preview namespace на MR: детерминированное создание/удаление (TTL, cleanup после merge/close). Зависит от T032 — `deploy/preview/`
- [ ] T051 [P] [docs T-090] Event-driven fast jobs (`@factory review` / `fix-ci`): короткие агентные операции по CI-событиям без полного Change Flow, те же политики безопасности и лимиты. Зависит от T025, T020 — `.github/workflows/`
- [ ] T052 [P] [docs T-087] Foundation: каталог блоков (owner, maturity, зависимости) и scorecards, управляемые обновления через migration MR. Зависит от T042, T048 — `src/dark_factory/foundation/`
- [ ] T053 [P] [docs T-088] Marketplace packs: формат CapabilityPack/FactoryPack (skills+agents+gates+policies+evaluations), SemVer, CI-валидация манифестов, публикация в registry, установка в проект. Зависит от T052 — `packs/`
- [ ] T054 [P] [docs T-089] Второй harness-адаптер: внешний CLI-агент как `HarnessPort`-адаптер; проверка смены исполнителя без изменения Flow. Зависит от T019 — `src/dark_factory/adapters/harness_alt/`
- [ ] T055 [P] [docs T-091] Миграция в shared/DC-контур: TLS/OIDC, Vault/external secrets, устойчивое хранилище артефактов, retention/backup, повышение concurrency после capacity-теста, разделение prod/non-prod Argo CD; пересмотр Temporal только при подтверждённых требованиях. Зависит от T031, T032, T043 — `charts/dark-factory/` (values-dc)
- [ ] T056 Синхронизация документации и финальная приёмка: обновить `docs/plan.md` (статусы), `docs/hld.md`/ADR при значимых решениях, проверить `quickstart.md` end-to-end, зафиксировать DoD-отчёт — `docs/`, `specs/001-dark-factory-mvp/quickstart.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: без зависимостей — выполнено.
- **Foundational (Phase 2)**: зависит от Setup — **БЛОКИРУЕТ все user stories**. Порядок внутри: T004 → T005 → T006.
- **US1 (Phase 3)**: зависит от Foundational (T005, T006).
- **US2–US6 (Phases 4–8)**: зависят от Foundational; внутри каждой — свой порядок.
- **Pilot (Phase 9)**: зависит от P0-цепочки US3–US6.
- **Polish/Extensions (Phase 10)**: зависит от соответствующих ранних задач; не блокирует MVP.

### User Story Dependencies

- **US1 (P1)** — MVP, не зависит от других историй.
- **US2 (P2)** — независима; использует T005/T006.
- **US3 (P3)** — независима; использует T005, T014 (US2).
- **US4 (P4)** — зависит от Foundational; использует `ChangeRequestRef`/порты (T006).
- **US5 (P5)** — зависит от Foundational и результата T025 (CI-шаблоны, US4). Фазовая зависимость «US5 после US4» не применяется: T025 и US5 связаны interleaved-цепочкой T029 → T030 → T025 → остальная US5.
- **US6 (P6)** — зависит от Foundational и артефактов US4 (T024) для трассировки.

### Критические межстадийные зависимости (interleaved)

Порядок внутри каждой истории корректен, но `docs/plan.md` задаёт несколько рёбер между историями — их нужно учитывать при планировании прогона:

- `T017 [US2 / T-021]` требует `T023 [US3 / T-016]` → T023 выполняется до T017.
- `T014 [US2 / T-011]` требует `T015 [US2 / T-012]` и `T019 [US3 / T-010]` → T019 выполняется до T014.
- `T018 [US2 / T-022]` требует `T041 [Pilot / T-070]`.
- `T025 [US4 / T-031]` требует `T030 [US5 / T-041]` (реестр: docs T-031 зависит от T-041, не T-040).
- `T028 [US4 / T-064]` требует `T029 [US5 / T-040]`.
- `T031 [US5 / T-042]` требует `T035 [US6 / T-050]`.
- `T039 [US6 / T-061]` требует `T024 [US4 / T-030]`.
- `T043 [Pilot / T-072]` требует всей P0-цепочки: T023, T025, T026, T031, T033, T034, T035, T039, T041.

### Внутри каждой задачи

- Миграции/схема → Flow → порты (Foundational).
- Модели/контракты → сервис → endpoint/CLI.
- Реализация → интеграция.
- DoD (`ruff` + `mypy --strict` + `pytest`) зелёные до перехода к следующей задаче.

### Parallel Opportunities

- Foundational: после T004 задачи T005 и T006 можно вести параллельно (разные файлы).
- US2: T015 → T014 (реестр: docs T-011 зависит от T-012); затем T016 → T017.
- US3: T019 и T020 параллельны; затем T021/T022/T023.
- US5: T029 и T030 параллельны.
- US6: T035, T037, T038 параллельны.
- Polish: T045–T055 в основном параллельны (разные модули), при наличии зависимостей из Phase 9.

---

## Sequential Example: User Story 2

```bash
# Последовательно (реестр: docs T-011 зависит от T-012):
Task: "T015 [US2] ContextBundle + KnowledgePort/ExecutionPort — src/dark_factory/context/"
Task: "T014 [US2] Профили ядра Product/Develop/Quality + skills — src/dark_factory/agents/"
Task: "T016 [US2] Native SDD Core + SDDPort — .factory/"
Task: "T017 [US2] Specification gate — src/dark_factory/quality/gates/"
```

## Parallel Example: User Story 3

```bash
Task: "T019 [US3] HarnessPort + PydanticAI-адаптер — src/dark_factory/adapters/harness/"
Task: "T020 [US3] Quality — независимая приёмка — src/dark_factory/quality/"
```

---

## Implementation Strategy

### MVP First (User Story 1)

1. Phase 1 Setup — выполнено (T001–T003).
2. Phase 2 Foundational — критично, блокирует всё (T004–T006).
3. Phase 3 US1 — walking skeleton (T007–T013).
4. **STOP и VALIDATE**: quickstart §1–§2 — CLI-стадия локально и в CI без LLM, идемпотентный повтор.

### Incremental Delivery

1. Foundational → US1 → demo (MVP: одна стадия без LLM).
2. US2 → согласованная спецификация с version-bound approval.
3. US3 → агентная реализация + review + rework.
4. US4 → автономный MR-контур и merge.
5. US5 → релиз в dev через GitOps + smoke.
6. US6 → контекст, трекер, Console, наблюдаемость.
7. Pilot → 10 задач e2e (SC-001…SC-008), затем P1–P3 расширения.

### Parallel Team Strategy

1. Foundational — вместе.
2. Далее потоки: Core/Agents (US2+US3) ∥ Delivery (US4+US5) ∥ Console/API (US6), с учётом interleaved-рёбер из раздела выше.

---

## Notes

- `[P]` — разные файлы, допускает параллельный запуск с другими задачами волны; не отменяет перечисленные зависимости задачи.
- `[Story]` — трассировка к user story `spec.md`; `[docs T-0NN]` — к реестру `docs/plan.md`. Якорь отсутствует у T007–T013 (implementation slice US1) и T056 (финальная синхронизация) — у них нет отдельных задач в реестре.
- Каждая задача независимо завершаема и проверяема; договорённости — в `contracts/` и `data-model.md`.
- Тесты — часть DoD задачи (не отдельные задачи): unit + contract + integration, включая crash-тесты effect ledger и exhaustiveness таблицы переходов.
- Коммиты — Conventional Commits на английском, только по явной просьбе пользователя (конституция V).
- Останавливаться на checkpoint'ах для независимой проверки истории.
- Избегать: расплывчатых задач, конфликтов по одному файлу, межстадийных зависимостей, ломающих независимость (кроме явно перечисленных interleaved-рёбер).
