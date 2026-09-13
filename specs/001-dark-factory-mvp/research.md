# Research: Dark Factory MVP (Phase 0)

**Feature**: `001-dark-factory-mvp` | **Date**: 2026-09-13 | **Plan**: [`plan.md`](./plan.md)

Задача Phase 0 — снять неопределённости Technical Context. В этом проекте большинство решений уже приняты ADR (ADR-001…019) и сведены в `docs/hld.md`; исследование здесь не пересматривает их, а **фиксирует, каким ADR закрыт каждый вопрос**, и добивает то, что ADR не закрепляют (версии зависимостей, размер «типового изменения»).

Формат каждого пункта: **Decision / Rationale / Alternatives considered**.

---

## R-01. Язык и форма ядра

- **Decision**: Python 3.12, модульный монолит (`src/dark_factory`), один релиз и lockfile; Console — TypeScript/React.
- **Rationale**: ADR-002 (Python core stack); `.python-version` = 3.12 и `requires-python = ">=3.12"` уже закреплены в T-002. Монолит даёт единый релиз и проверяемые границы модулей (ADR-015 §3).
- **Alternatives considered**: полиглот-микросервисы (отклонено: цена координации при одном операторе); Go-ядро (отклонено: экосистема PydanticAI/pydantic-graph).

## R-02. Хранилище операционного состояния

- **Decision**: PostgreSQL — authoritative operational state: `execution`, `stage`, `attempt`, `execution_leases`, `outbox`, `event_delivery`, effect ledger, usage. Доступ — `SQLAlchemy 2.x` + `Alembic` + `psycopg` 3.
- **Rationale**: ADR-004; lease/fencing и optimistic concurrency (`state_revision`) требуют транзакционной БД (ADR-006 §4/§6). Миграции — Alembic, backward-compatible expand/contract (ADR-010 §6).
- **Alternatives considered**: Git/файлы как состояние (отклонено: нет транзакций и lease, ADR-004); БД продукта как state store (запрещено spec.md); встраиваемая БД (отклонено: конкурентность и outbox).

## R-03. Workflow engine

- **Decision**: Temporal в MVP не используется. Внутри стадии — `pydantic-graph` `TaskGraph` (branch/join ≤2, reducers); между стадиями — собственный лёгкий durable workflow-core: неизменяемая таблица переходов, типизированные `StageResult`/`NextAction`, `assert_never` + рантайм-сверка + exhaustive-тесты.
- **Rationale**: ADR-003, ADR-005 §1–2. Переходы уже материализованы в коде: `NextAction` — закрытое объединение из 8 вариантов (`src/dark_factory/changes/next_action.py`).
- **Alternatives considered**: Temporal (отклонён для MVP: ADR-003, Q-2); pydantic-graph на всём Flow (отклонён: нет меж-job durability, Q-4).

## R-04. Модель исполнения

- **Decision**: ephemeral job pod на стадию + Reconciler CronJob (iterations 2–5 мин, Kubernetes `concurrencyPolicy: Forbid` + PostgreSQL lease `global-reconciler` с монотонным `fencing_token`); Outbox Dispatcher CronJob для доставки событий. Нет постоянно живущих воркеров.
- **Rationale**: ADR-006 §1/§5–6/§12, ADR-016 §4. Долгое ожидание освобождает runner; продолжение — новым запуском по `StageResult`/`NextAction`.
- **Alternatives considered**: постоянный worker/reconciler-сервис (отклонён для MVP: ADR-006 §10, Q-5).

## R-05. SC/CI-провайдер

- **Decision**: мультипровайдерно за портами; провайдер выбирается на уровне репозитория (`provider: github | gitlab`). MVP стартует на **GitHub** (GitHub App с короткоживущими installation tokens, webhooks, Actions на self-hosted runners, Checks API, artifacts, rulesets); **GitLab** — второй провайдер (T-034) на той же контрактной сюите. PR/MR сведены к единому `ChangeRequestRef`; инвариант «один run — один провайдер».
- **Rationale**: ADR-019 §1–6; GitLab Premium как целевая редакция с CE-fallback (ADR-012).
- **Alternatives considered**: только GitHub / только GitLab (отклонено: привязка ядра к провайдеру); webhook как источник истины (отклонено: webhook — ускоритель, reconcile — истина).

## R-06. Трекер

- **Decision**: Plane (self-hosted, webhook + HMAC) за `TrackerPort`; до готовности — NoOp-заглушка. `get_change` / `publish_status` / `request_approval`. Отсутствие трекера не блокирует CLI/Console.
- **Rationale**: ADR-013 §6; замена изолирована портом.
- **Alternatives considered**: Linear (Q-12); жёсткая привязка к одному трекеру (отклонено плагинной моделью ADR-008).

## R-07. Console / UI

- **Decision**: React + TypeScript + Vite, Radix + shadcn, Storybook как исполняемая UI-спецификация; 5 экранов MVP (T-051). Согласования — через внешнюю систему контроля версий либо проверенную операцию от имени авторизованного пользователя; потеря локального кэша Console не влияет на процесс.
- **Rationale**: ADR-014; FR-019 и US6.
- **Alternatives considered**: Figma/Web Components UIKit (вне MVP, ADR-014/§18 HLD); серверный рендеринг без Storybook (отклонено: Storybook — UI-гейт).

## R-08. Delivery / окружение

- **Decision**: локальный Docker Desktop Kubernetes + Helm + Argo CD; поток GitOps через репозиторий `dark-factory-gitops` (immutable digests, без secrets). Профиль 10–12 ГБ, concurrency=1; ARM64-образы (multi-arch), digest закрепляется. Compose — деградированный режим без GitOps.
- **Rationale**: ADR-010 §1–3; rollback — revert GitOps-коммита, схема БД — expand/contract (§6).
- **Alternatives considered**: managed K8s / DC-контур (пост-MVP, T-091); Compose как основной (отклонён: нет GitOps/Argo).

## R-09. Событийная модель

- **Decision**: transactional outbox в PostgreSQL: изменение состояния и событие — в одной транзакции; at-least-once доставка, per-consumer `event_delivery`, порядок только в рамках `changeId`/`runId` по монотонному `sequence`, retry с backoff, dead-letter, cleanup после all-delivered + retention. Kafka в MVP не разворачивается.
- **Rationale**: ADR-016; `EventPublisherPort` развязывает домен и транспорт (§7).
- **Alternatives considered**: Kafka (отклонено для MVP: ADR-016 §10, Q-15); прямые вызовы без outbox (отклонено: потеря событий при crash).

## R-10. Каталог ролей

- **Decision**: 9 широких ролей (Product, Design, Architect, Develop, Quality, Security, Infrastructure, CI/CD, Operation); ядро MVP — Product/Develop/Quality, остальные — по маршруту/риску (T-081/T-082). Reviewer — режим профиля, не отдельная роль.
- **Rationale**: ADR-007; контракт `AgentProfile → TaskEnvelope → AgentResult`.
- **Alternatives considered**: 3 или 5 ролей (Q-6); роли-сервисы (отклонено: профили — манифесты).

## R-11. Плагины и расширяемость

- **Decision**: стабильное ядро + SDK + 8 типов плагинов + FactoryPack. Встроенные version-pinned плагины — in-process по allowlist; остальные — subprocess с лимитами и без наследования секретов. Поставка поэтапно: порты/адаптеры сразу (T-005), реестр/загрузчик — с T-070, marketplace — P3 (T-088).
- **Rationale**: ADR-008; границы доверия §4.
- **Alternatives considered**: монолит без плагинов (ломает расширяемость ролей/провайдеров); marketplace сразу (отклонено: нужна подпись/sandboxing).

## R-12. Наблюдаемость

- **Decision**: `TelemetryPort` + OTLP-адаптер; экспорт по умолчанию — job logs/artifacts, внешний backend опционален; сквозная корреляция `change → run → stage → agent → tool` с первого дня. AI-трассы — Pydantic Evals + OTel (usage/cost), Langfuse не обязателен. Долгоживущая evidence — в Git (`dark-factory-runs`), тяжёлая — CI artifacts.
- **Rationale**: ADR-009 §2/§9, ADR-015 §4.
- **Alternatives considered**: полный корпоративный observability-стек (вне MVP, ADR-009).

## R-13. Границы репозиториев и версионирование

- **Decision**: 4 системных репозитория (`dark-factory`, `dark-factory-gitops`, `dark-factory-runs`, `okf`) + динамическая группа `products/`. Связи — immutable идентификаторы (commit SHA, semver, artifact digest), без `latest`.
- **Rationale**: ADR-015; run воспроизводим по `RunManifest`, пример — §16 HLD.
- **Alternatives considered**: монорепо со всем (ломает границы доверия и CODEOWNERS); ветки вместо immutable refs (отклонено: невоспроизводимость run).

## R-14. SDD-формат

- **Decision** (обновлено 2026-09-14: ADR-020 заменяет ADR-017): канонический — Native SDD Core (`docs/sdd-native-core.md`): ChangeSet со стабильным ID и цепочкой `Intent → Spec → Design → Tasks → Verification → Evidence → Reconciliation`, дельта (`add/modify/supersede/retire`) относительно канонического Product Baseline `.factory/`, reconciliation после acceptance. Spec Kit — bootstrap до T-020 (артефакты `specs/<фича>/` сохраняются как historical evidence); OpenSpec — compatibility-адаптер, отдельный baseline в формате OpenSpec не поддерживается. Два параллельных SDD-процесса не допускаются; гейт T-021 работает поверх нормализованного контракта ChangeSet (GateDecision), а не силами SDD-инструмента.
- **Rationale**: ADR-020; ADR-017 — историческое решение (заменено); ADR-001 (bootstrap).
- **Alternatives considered**: остаться на Spec Kit (отклонено: Q-16 закрыт ADR-020); оба формата параллельно (запрещено конституцией).

## R-15. Модель участия человека

- **Decision**: по фазам — human-in-the-loop на требованиях, UX/UI, архитектуре, merge (MVP) и prod; human-on-the-loop на планировании и проверках качества; human-off-the-loop на написании кода и dev-деплое. Автономный цикл ограничен утверждённым Implementation Contract; эскалация по границам ADR-018.
- **Rationale**: ADR-018 §1/§3–5, §8.3 HLD.
- **Alternatives considered**: полная автономность (отклонено: риск-модель MVP); полный ручной контроль (теряется ценность фабрики).

## R-16. Merge и release policy

- **Decision**: merge в MVP — человек; low-risk R0/R1 автономно доводится до готового к merge; после ручного merge dev-деплой — trusted finalizer (GitOps-MR с immutable digest). Auto-merge R0/R1 — policy-controlled, T-085 (после статистики пилота). Prod — вручную, пост-MVP (T-091). Риск-классы R0–R4 (T-080): LLM может повысить класс, понизить — только формальная политика.
- **Rationale**: ADR-011 §5, ADR-018.
- **Alternatives considered**: авто-merge сразу (отклонено: нет статистики); merge агентом (запрещено FR-004/FR-010).

## R-17. Harness и LLM

- **Decision**: PydanticAI за `HarnessPort`; LLM — внешний endpoint (в §4 HLD — LiteLLM), локальные модели не используются. Второй harness — T-089. Детерминированные шаги стадии исполняются без LLM.
- **Rationale**: ADR-002 §2, ADR-008; US1 scenario 3.
- **Alternatives considered**: прямой вызов SDK LLM из ядра (запрещено границами портов); локальные модели (вне MVP, spec.md).

## R-18. Версии зависимостей

- **Decision**: фиксированные в ADR/T-002 зависимости сохраняются; новые вводятся при старте соответствующей задачи и попадают в `uv.lock`: `sqlalchemy>=2.0`, `alembic>=1.13`, `psycopg[binary]>=3.2` (T-006); `pydantic-ai` и `pydantic-graph` (T-004/T-010); `fastapi`/`uvicorn` (T-050); `opentelemetry-sdk`/OTLP exporter (T-060). Точные минорные версии закрепляет lock при добавлении; апгрейды — отдельными задачами.
- **Rationale**: единый lockfile — часть модульного монолита (ADR-002); T-003 уже добавил `pydantic>=2.9`, `pyyaml>=6.0.2`, плагин `pydantic.mypy` и версии отразил в `uv.lock`.
- **Alternatives considered**: пины всех версий заранее (устаревают до старта задач); диапазоны без lock (невоспроизводимость).

## R-19. Размер «типового небольшого изменения» (SC-002)

- **Decision**: «типовое небольшое изменение» = feature/bugfix, не выходящий за границы эскалации ADR-018 (публичный API, схема данных, IAM, migrations) **и** дифф ≤ 10 файлов и ≤ 200 строк. Оба условия обязательны.
- **Rationale**: Clarifications spec.md (Session 2026-09-13); порог измерим и проверяется на пилоте (T-072).
- **Alternatives considered**: только природа изменения без размера (неизмеримо); только размер (пропускает рискованные мелкие диффы).

## R-20. Стратегия тестирования

- **Decision**: единая контрактная тест-сюита портов исполняется против фейка, затем GitHub и GitLab (ADR-019 §6); unit — на домене и переходах; exhaustive-тесты таблицы переходов; integration — БД/outbox/reconciler/effect ledger (crash-тесты T-006); e2e — T-072 (10 задач через фабрику). Гейты задачи: `ruff` + `mypy --strict` + `pytest`.
- **Rationale**: ADR-015 §3 (границы — тестом), ADR-006 §3 (crash-тесты), ADR-019 §6.
- **Alternatives considered**: только unit-тесты (не поймает границы/идемпотентность); только e2e (поздно и дорого).

---

**Итог Phase 0**: все неопределённости Technical Context закрыты; открытых NEEDS CLARIFICATION нет. Вопросы, которые ADR намеренно оставляют открытыми (Q-1…Q-16 `docs/plan.md`), либо имеют принятый ADR-ответ, либо привязаны к конкретной задаче и пересматриваются её ADR-триггером.
