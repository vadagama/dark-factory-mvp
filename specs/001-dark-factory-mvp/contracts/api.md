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
| `GET` | `/changes/{change_id}/phase-gate` | прекондиции гейта фазы (T087): `available`, `reasons[{what, how}]`, `current_revision` (с M3 для фаз стадии `specification` — ревизия артефактов **самой фазы**, ADR-039), `approved`, `waived`, `approvals[{state: current|stale|unbound, phase}]`, счётчики вопросов/замечаний, состояние доработки; для `interface` — `ui_requirement{required, source: route|agent|operator|default, reason}` и `checks[{id: axe|visual_regression, status: planned|not_required|passed|failed, note}]` (T097: axe/visual regression никогда не зелёные до исполнения); `phase` — параметр, по умолчанию текущая фаза |
| `GET` | `/changes/{change_id}/phases` | проекция восьми фаз (T098, ADR-039): `current` и `phases[8]{phase, index, label, stage, gate, state: pending|active|needs_decision|approved|waived|not_required|stale|done|blocked, state_reason, revision, approved_revision, open_questions, blocking_questions, open_comments, iteration}`; левая колонка Console и `factory change phases` рендерят её, не вычисляя состояние сами |

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

### Decisions and UI spec (T093–T094, ADR-039)

Read-model'ы фаз «Архитектура» и «Интерфейс» над git (ADR-035 п.1): карточка решения читается из `design/decisions/ADR-NNN-<slug>.md`, UI-спека — из `design/ui/scenarios/SCN-NNN-<slug>.md` и `design/ui/screens/SCR-NNN-<slug>.md` на голове ветки изменения. Без сконфигурированного `RepositoryPort` эндпоинты отвечают 503. Статус карточки производный и в файл не пишется.

| Метод | Путь | Назначение |
|---|---|---|
| `GET` | `/changes/{change_id}/decisions` | карточки решений `DecisionsView{change_id, revision, approved, decisions[DecisionCard], errors[]}`; `revision` — ревизия артефактов фазы `architecture`, `approved` — фаза согласована на этой ревизии (stale не считается) |
| `POST` | `/changes/{change_id}/decisions/{decision_id}/alternative` | «Запросить альтернативу» `{instruction, comment_ids[]}` → `ReworkOrder` фазы `architecture` с `decision_ids: [decision_id]`; в той же транзакции — version-bound `rejected` по гейту фазы; 201, 200 replay по `Idempotency-Key`, 404 — `decision_id` не среди ADR ветки, 409 — открытое поручение фазы уже есть, 422 — пустая инструкция |
| `GET` | `/changes/{change_id}/ui` | UI-спека `UiSpecView{change_id, revision, dev_url, scenarios[], screens[], links[], components[], errors[]}`; `revision` — ревизия артефактов фазы `interface`, `dev_url` — из `.factory/product/factory.yaml` ветки изменения |

Формы (имена полей — pydantic-модели `context/decisions.py`, `context/ui_spec.py`, `orchestration/decisions.py`, `orchestration/ui_spec.py`):

```ts
interface DecisionAlternative { title: string; summary: string | null; rejected_because: string | null }
interface DecisionCard {
  id: string;                       // frontmatter id (adr:<product>:NNNN) или имя файла без расширения
  path: string; title: string;
  status: "proposed" | "accepted" | "needs_revision" | "superseded";   // производный
  document_status: string | null;   // как написал агент
  revision: string | null;          // последний коммит, тронувший ADR
  proposal: string | null; rationale: string | null; consequences: string | null;
  alternatives: DecisionAlternative[]; impact: string[];
  pending_alternative: ReworkOrder | null;   // открытое поручение «Запросить альтернативу»
  affected_artifacts: string[];              // design/adr/ui/spec-файлы, изменённые после последнего выполненного поручения по решению
  errors: string[];                          // что не удалось разобрать
}
interface DecisionsView { change_id: string; revision: string | null; approved: boolean; decisions: DecisionCard[]; errors: string[] }

interface UiStep { id: string; text: string; screen: string | null }
interface UiScenario { id: string; path: string; title: string; summary: string | null; steps: UiStep[]; screens: string[] }
interface UiState { kind: "loading" | "empty" | "error" | "success" | "access"; description: string | null }
interface UiElement { id: string; kind: string | null; label: string | null; component: string | null }
interface UiScreen { id: string; path: string; title: string; purpose: string | null; route: string | null; preview_url: string | null;
                     states: UiState[]; elements: UiElement[]; components: string[] }
interface UiLink { id: string; from_screen: string; to_screen: string; trigger: string | null; condition: string | null }  // id `SCR-A->SCR-B`, при повторе пары — `#n`
interface UiComponentUse { name: string; screens: string[] }
interface UiSpecView { change_id: string; revision: string | null; dev_url: string | null;
                       scenarios: UiScenario[]; screens: UiScreen[]; links: UiLink[]; components: UiComponentUse[]; errors: string[] }
```

Правила: статус карточки — `superseded` из frontmatter; `needs_revision` — открытое (`pending`/`in_progress`) поручение с решением в `decision_ids` или устаревшее согласование архитектуры при ADR, изменённом после него; `accepted` — действующее согласование фазы `architecture`; иначе `proposed`. Необъявленное состояние экрана отсутствует в `states` (не подменяется); `preview_url` — абсолютный как есть, относительный — `dev_url` + путь, без `dev_url` — как написан. Расширения доменных моделей: `ReworkOrder.decision_ids: string[]` (payload, без миграции), `Decision.phase: Phase | null` (миграция `0007_phase_rounds`; `null` — решение до M3, читается как фаза своего гейта).

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
| `POST` | `/changes/{change_id}/approvals` | записать решение `{gate, outcome, subject_revision?, comment, phase?}`; `phase` (M3, ADR-039) различает решения на общем гейте `specification` (`requirements` / `architecture`), по умолчанию — фаза гейта, несогласие с `gate` — 422 |

`subject_revision` (hash/SHA) обязателен для `approved` и `rejected`: изменение ревизии инвалидирует approval (FR-003, ADR-009 §7, ADR-018 §3); для `waived` он необязателен — пропуск относится к фазе, а не к документу (backend-only изменение без UI-артефактов, ADR-039 п.6). С M3 «текущая ревизия» фазы стадии `specification` — ревизия её собственных артефактов, поэтому раунд архитектора не делает согласование требований неактуальным. С T087 `approved` проходит через прекондиции гейта фазы (`GET …/phase-gate`): 409 при закрытом гейте (блокирующие вопросы, ожидающая/идущая доработка, отсутствие ревизии артефактов) и при `subject_revision`, отличной от текущей головы ветки изменения; `waived` (пропуск фазы, ADR-032 §5) требует `comment` с основанием (422); `rejected` не блокируется — явный возврат делается через `POST …/rework-orders`.

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
| Ответ на вопрос, замечание, закрытие замечания, поручение на доработку (в т.ч. «Запросить альтернативу»), правка артефакта, черновик, «просмотрено» | авторизованный оператор: scope `changes:write` + роль `operator` |
| Bootstrap baseline продукта | авторизованный оператор: scope `products:write` + роль `operator` |
| Переключение этапов CI | авторизованный оператор (не агент): scope `ci:write` + роль `operator` |
| Merge/deploy | **вне API ядра** — доверенный финализатор / человек (FR-010, FR-023) |

Агентные задания не имеют прав merge/deploy и не могут менять собственные критерии приёмки (FR-004).

## Инварианты

- API не является источником истины: authoritative state — PostgreSQL (ADR-004); при рассинхроне приоритет у PostgreSQL после сверки.
- Потеря локального кэша Console не влияет на процесс (SC-008); API stateless относительно процесса.
- Human-текст трактуется как данные и не влияет на права/гейты (FR-007).
- `409 Conflict` при несовпадении `state_revision`/`expected_revision`; операция не выполняется.
