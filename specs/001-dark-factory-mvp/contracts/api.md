# Contract: API (FastAPI)

**Feature**: `001-dark-factory-mvp` | **Date**: 2026-09-13 | **Plan**: [`../plan.md`](../plan.md)

API — операторский контроль и точка данных для Console (FR-019, ADR-002, ADR-009 §7). Mutating-операции — token-authenticated. Согласования выполняются через внешнюю систему контроля версий либо проверенную операцию от имени авторизованного пользователя.

## Общие правила

- База: `/api/v1`.
- Аутентификация: `Authorization: Bearer <token>` для всех mutating-запросов; read-only — в локальном контуре допустимы без токена (ADR-009 §7).
- Идемпотентность: mutating-запросы принимают `Idempotency-Key: <key>`; повтор с тем же ключом не создаёт второй эффект (FR-017), ответ — тот же.
- Формат ошибок (RFC 7807-подобный):

```json
{ "type": "about:blank", "title": "Conflict", "status": 409, "detail": "state_revision mismatch" }
```

## Ресурсы

### Runs

| Метод | Путь | Назначение |
|---|---|---|
| `GET` | `/runs` | список запусков (фильтры: `change_id`, `status`, `stage`) |
| `GET` | `/runs/{run_id}` | состояние запуска: стадия, статус, гейты, вопросы/блокеры, стоимость |
| `GET` | `/runs/{run_id}/stage-results` | результат каждой стадии (`StageResult`) |
| `GET` | `/runs/{run_id}/trace` | трассировка: задача → спецификация (SHA) → план → код (SHA) → проверки → релиз |

### Changes

| Метод | Путь | Назначение |
|---|---|---|
| `POST` | `/changes` | intake: создать Change из снапшота задачи (FR-001) |
| `GET` | `/changes/{change_id}` | карточка изменения: scope, ограничения, статус, ссылки |
| `GET` | `/changes/{change_id}/trace` | полная трассируемая цепочка (SC-007) |

### Approvals

| Метод | Путь | Назначение |
|---|---|---|
| `GET` | `/changes/{change_id}/approvals` | список согласований/решений |
| `POST` | `/changes/{change_id}/approvals` | записать решение `{gate, outcome, subject_revision, comment}` |

`subject_revision` (hash/SHA) обязателен: изменение ревизии инвалидирует approval (FR-003, ADR-009 §7, ADR-018 §3).

### Evidence

| Метод | Путь | Назначение |
|---|---|---|
| `GET` | `/runs/{run_id}/evidence` | список evidence, включая `required`/`available` |
| `GET` | `/runs/{run_id}/evidence/{evidence_id}` | метаданные evidence (тяжёлые данные — по ссылке `ArtifactStorePort`) |

### Gates / Findings

| Метод | Путь | Назначение |
|---|---|---|
| `GET` | `/runs/{run_id}/gates` | результаты 7 гейтов MVP |
| `GET` | `/runs/{run_id}/findings` | замечания (agent/human/ci), фильтр по severity/status |

## Права

| Операция | Кто |
|---|---|
| Чтение runs/evidence/gates | оператор, Console |
| Intake change | оператор, трекер (через adapter) |
| Approval/decision | авторизованный оператор (не агент) |
| Merge/deploy | **вне API ядра** — доверенный финализатор / человек (FR-010, FR-023) |

Агентные задания не имеют прав merge/deploy и не могут менять собственные критерии приёмки (FR-004).

## Инварианты

- API не является источником истины: authoritative state — PostgreSQL (ADR-004); при рассинхроне приоритет у PostgreSQL после сверки.
- Потеря локального кэша Console не влияет на процесс (SC-008); API stateless относительно процесса.
- Human-текст трактуется как данные и не влияет на права/гейты (FR-007).
- `409 Conflict` при несовпадении `state_revision`/`expected_revision`; операция не выполняется.
