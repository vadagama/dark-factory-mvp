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
| `POST` | `/changes` | intake: создать Change из снапшота задачи (FR-001); с T071 — с `product_id`, брифом `IntakeBrief`, сценарием `specs_only|full` и лимитом `spend_limit` (USD) |
| `GET` | `/changes` | список изменений; фильтр `product_id` (T071) |
| `GET` | `/changes/{change_id}` | карточка изменения: scope, ограничения, статус, ссылки |
| `GET` | `/changes/{change_id}/trace` | полная трассируемая цепочка (SC-007) |
| `PUT` | `/changes/{change_id}/brief` | заменить бриф задачи (T071/T072); статус брифа выводится сервером |
| `GET` | `/changes/{change_id}/guidance` | «следующий шаг» задачи — серверный `Guidance` (T074, ADR-033) |

### Conversations (T086, ADR-034)

| Метод | Путь | Назначение |
|---|---|---|
| `GET` | `/changes/{change_id}/questions` | вопросы агента; фильтры `status` (`open|answered|resolved|stale`), `phase` |
| `POST` | `/changes/{change_id}/questions` | заявить вопрос (агент через service-токен или оператор); `{text, kind: choice|text|number, options[], anchor{artifact, anchor_id, revision}, blocking, phase?}`; replay по `Idempotency-Key` |
| `POST` | `/changes/{change_id}/questions/{question_id}/answer` | ответ оператора `{value, comment?}` — человеческое решение, не правка артефакта; 409 не `open`, 422 ответ не подходит к типу |
| `GET` | `/changes/{change_id}/comments` | замечания с состоянием якоря `anchor_state: attached|detached`; фильтры `status`, `phase`, `artifact` |
| `POST` | `/changes/{change_id}/comments` | замечание к фрагменту `{artifact, anchor_id?, revision?, body, phase?}`; ревизия по умолчанию — голова ветки изменения; замечание **не** запускает доработку |
| `POST` | `/changes/{change_id}/comments/{comment_id}/addressed` | отметка агента «исправлено» → `addressed` (готово к повторной проверке, не закрыто) |
| `POST` | `/changes/{change_id}/comments/{comment_id}/close` · `/reopen` | закрывает/переоткрывает только оператор |
| `GET` | `/changes/{change_id}/rework-orders` | поручения на доработку; фильтры `phase`, `status` |
| `POST` | `/changes/{change_id}/rework-orders` | «На доработку» `{phase?, comment_ids[], question_ids[], instruction?, expected_revision?}`: одно поручение на фазу одновременно (409), пустое — 422, неизвестный комментарий/вопрос — 404; в той же транзакции пишется version-bound решение `rejected` по гейту фазы |
| `GET` | `/changes/{change_id}/phase-gate` | прекондиции гейта фазы (T087): `available`, `reasons[{what, how}]`, `current_revision`, `approved`, `approvals[{state: current|stale|unbound}]`, счётчики вопросов/замечаний, состояние доработки; `phase` — параметр, по умолчанию текущая фаза |

### Artifacts (T082–T086, ADR-035)

Источник истины — git: ветка изменения `factory/<slug(change_id)>`, поддерево `.factory/changes/**`. Без сконфигурированного `RepositoryPort` эндпоинты отвечают 503. `{path}` содержит `/` и передаётся как есть (`{path:path}`).

| Метод | Путь | Назначение |
|---|---|---|
| `GET` | `/changes/{change_id}/artifacts` | дерево артефактов на голове ветки: `revision` (null — ветки ещё нет), `nodes[{path, kind: spec|design|adr|ui|plan|other, revision}]`, `drafts[]` |
| `GET` | `/changes/{change_id}/artifacts/{path}` | документ на ревизии (`?revision=`, по умолчанию голова): `content`, `properties{values, protected}`, `body`, `anchors[]`, `frontmatter_error`, `draft`, `viewed`, `open_comments`, `open_questions` |
| `PUT` | `/changes/{change_id}/artifacts/{path}` | write-through: `{content, base_revision, message?, properties?}` → один коммит в ветку изменения (идемпотентно по `Idempotency-Key`); 409 — файл изменился после `base_revision` (конфликт не разрешается молча); 422 — защищённое свойство (`schema, id, type, product, change`); ответ `{revision, previous_revision, created_commit, stale_questions[], detached_comments[]}` |
| `GET` | `/changes/{change_id}/artifact-versions/{path}` | ревизии документа (коммиты пути), новые первыми |
| `GET` | `/changes/{change_id}/artifact-diff/{path}?from_revision=&to_revision=` | unified diff между двумя ревизиями |
| `GET` · `PUT` · `DELETE` | `/changes/{change_id}/artifact-drafts/{path}` | черновик автосейва вне git (`{content, base_revision}` → `{…, stale}`); 404 без черновика; явное «сохранить» — это `PUT …/artifacts/{path}` |
| `POST` | `/changes/{change_id}/artifact-views/{path}` | «просмотрено» для ревизии `{revision}` — не согласование |

### Briefs

| Метод | Путь | Назначение |
|---|---|---|
| `POST` | `/briefs/formulate` | «Помоги сформулировать»: `{source_text}` → `IntakeBrief` через harness (T072); без harness или при отказе — 200 с `status=draft` и `error` |

### Products

| Метод | Путь | Назначение |
|---|---|---|
| `GET` | `/products` | реестр продуктов (T066, ADR-030) |
| `POST` | `/products` | регистрация продукта; дедуп по `id` |
| `GET` | `/products/{product_id}` | продукт и его готовность |
| `POST` | `/products/{product_id}/validate` | наблюдение репозитория и запись готовности `validating → ready|error` (ADR-031); 503 без порта провижининга |
| `POST` | `/products/{product_id}/bootstrap` | применить baseline-паки к репозиторию продукта (`{packs?}`, по умолчанию `product-baseline`; T069/ADR-031 п.3); replay по `Idempotency-Key`; 503 без порта, 409 если адаптер не умеет bootstrap |
| `GET` | `/products/{product_id}/guidance` | «следующий шаг» продукта — `Guidance` (T074) |

### Approvals

| Метод | Путь | Назначение |
|---|---|---|
| `GET` | `/changes/{change_id}/approvals` | список согласований/решений |
| `POST` | `/changes/{change_id}/approvals` | записать решение `{gate, outcome, subject_revision, comment}` |

`subject_revision` (hash/SHA) обязателен: изменение ревизии инвалидирует approval (FR-003, ADR-009 §7, ADR-018 §3). С T087 `approved` проходит через прекондиции гейта фазы (`GET …/phase-gate`): 409 при закрытом гейте (блокирующие вопросы, ожидающая/идущая доработка, отсутствие ревизии артефактов) и при `subject_revision`, отличной от текущей головы ветки изменения; `waived` (пропуск фазы, ADR-032 §5) требует `comment` с основанием (422); `rejected` не блокируется — явный возврат делается через `POST …/rework-orders`.

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

### CI stages

| Метод | Путь | Назначение |
|---|---|---|
| `GET` | `/ci/stages` | каталог этапов CI фабрики и текущее состояние их переключателей (`available=false` + `reason`, когда контур не сконфигурирован) |
| `PUT` | `/ci/stages/{job}` | включить/выключить этап: тело `{enabled}` (строгий bool), ответ — обновлённый этап; 404 неизвестный этап, 502 ошибка провайдера, 503 не сконфигурировано |

Этап — job `.github/workflows/ci.yml`; переключатель — `CI_SKIP_<JOB>` в repository variables GitHub (ADR-026). Каталог этапов — код (`dark_factory.orchestration.ci`), он же выводит имя переменной, поэтому запрос не может назвать произвольную переменную репозитория (T059, ADR-027).

## Права

| Операция | Кто |
|---|---|
| Чтение runs/evidence/gates | оператор, Console |
| Intake change, бриф, «Помоги сформулировать» | оператор, трекер (через adapter): scope `changes:write` |
| Регистрация и валидация продукта | авторизованный оператор: scope `products:write` + роль `operator` |
| Чтение `Guidance` | открыто (read-model, ничего не исполняет — ADR-033 p.2) |
| Approval/decision | авторизованный оператор (не агент) |
| Заявить вопрос, отметить замечание «исправлено» | агент (service) или оператор: scope `changes:write` |
| Ответ на вопрос, замечание, закрытие замечания, поручение на доработку, правка артефакта, черновик, «просмотрено» | авторизованный оператор: scope `changes:write` + роль `operator` |
| Bootstrap baseline продукта | авторизованный оператор: scope `products:write` + роль `operator` |
| Переключение этапов CI | авторизованный оператор (не агент): scope `ci:write` + роль `operator` |
| Merge/deploy | **вне API ядра** — доверенный финализатор / человек (FR-010, FR-023) |

Агентные задания не имеют прав merge/deploy и не могут менять собственные критерии приёмки (FR-004).

## Инварианты

- API не является источником истины: authoritative state — PostgreSQL (ADR-004); при рассинхроне приоритет у PostgreSQL после сверки.
- Потеря локального кэша Console не влияет на процесс (SC-008); API stateless относительно процесса.
- Human-текст трактуется как данные и не влияет на права/гейты (FR-007).
- `409 Conflict` при несовпадении `state_revision`/`expected_revision`; операция не выполняется.
