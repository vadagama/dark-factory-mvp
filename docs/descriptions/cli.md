# CLI — `cli/`

**Исходники:** [`src/dark_factory/cli/`](../../src/dark_factory/cli/)

**Главный потребитель:** человек и CI (factory CI job, T-025: [factory-stage.yml](../../.github/workflows/factory-stage.yml)).

## 1. Назначение

`cli/` — точка входа «фабричного раннера» (T007): console-script `factory` (`pyproject [project.scripts]`: `factory = "dark_factory.cli.main:main"`) и `python -m dark_factory.cli`. Одна и та же команда работает локально и в CI (FR-022, ADR-006) — постоянно живой сервис не нужен.

Сценарии, которые закрывает модуль:

| Сценарий | Команда |
|---|---|
| Локальный/CI-прогон одной стадии | `stage run` |
| Продолжение ожидающего запуска | `stage resume` — заглушка, exit 2 |
| Инспекция запуска | `run status` (T-092) |
| Продвижение запуска ровно на одну стадию | `run advance` (T-092) |
| Публикация индекса run-записи в `dark-factory-runs` | `run publish` (T-061) |
| Диагностика окружения и конфигурации | `doctor` |
| Один идемпотентный проход Reconciler | `reconcile` |
| Доставка, ручной replay и skip outbox-событий | `outbox dispatch` / `replay` / `skip` |
| Локальный REST API | `api serve` |
| Верификация релиза: digest (FR-011), Argo-статусы, smoke (FR-013) | `release verify` (T034) |
| Job-скрипт GitHub Actions | `python -m dark_factory.cli.ci_job` (отдельная точка входа) |

Каждая команда — тонкая обвязка над ядром: парсинг аргументов, коды выхода, печать отчёта. Бизнес-логика живёт в `changes/`, `orchestration/` и `api/`.

## 2. От точки входа до данных

```mermaid
flowchart TD
    ENTRY["factory\npyproject scripts +\npython -m dark_factory.cli"] --> P["cli/main.py\nbuild_parser + parse_command"]
    P --> D["dispatch\nисчерпывающий match\nпо типизированным args"]
    D --> SR["stage run\ncli/stage.py"]
    D --> STUB["stage resume\nnot_implemented, exit 2"]
    D --> ADV["run advance, run status\ncli/runner.py"]
    D --> PUB["run publish\ncli/runs.py"]
    D --> REC["reconcile\ncli/reconcile.py"]
    D --> OUT["outbox dispatch/replay/skip\ncli/outbox.py"]
    D --> DOC["doctor\ncli/doctor.py"]
    D --> APIS["api serve\ncli/api.py"]
    D --> REL["release verify\ncli/release.py"]
    SR --> EX["orchestration/stages/\nrun_deterministic_stage\nбез harness и LLM"]
    SR --> EV["evidence-dir:\nchange_snapshot.yaml\nstage_result.json\nrun_record.json"]
    EV -->|"stage_result.json"| CIJ["ci_job (T025)\noutputs + код выхода"]
    REC --> SVC["GlobalReconciler\norchestration/reconcile/"]
    OUT --> DISP["OutboxDispatcher +\nDeliveryRepository\norchestration/events/"]
    APIS --> APP["dark_factory.api.create_app\n+ uvicorn"]
    REL --> QREL["quality/release/\nevaluate_release + probes\nбез БД"]
    PUB --> RUNS["execution/runs/\nRunRecordStore:\nразметка, идемпотентность,\nimmutability, screening"]
    RUNS --> RUNREPO["checkout dark-factory-runs\nruns/YYYY/MM/change/run/"]
    SVC --> PG["orchestration/state/engine.py\nPostgreSQL (ADR-004)"]
    DISP --> PG
    APP --> PG
    ADV --> RUNNER["orchestration/runner.py\nadvance_run: lease, Flow, persist"]
    RUNNER --> RSTORE["orchestration/state/run_store.py\nexecution + stage + attempt\n+ stage_result + outbox"]
    RUNNER --> FLOWD["orchestration/flow.py\napply_result — единственный переход"]
    RSTORE --> PG
    DOC --> ENV["переменные окружения\nофлайн, без БД"]
```

Команды CLI **не используют** порты (`ports/`) и адаптеры (`adapters/`): ни один файл `cli/` не импортирует `dark_factory.ports` или `dark_factory.adapters`. Fakes, PydanticAI-адаптер `HarnessPort` и GitHub-адаптер к CLI не подключены — детерминированный путь `stage run` не вызывает harness/LLM (ADR-003), а машинное исполнение гейтов живёт в CI (FR-009).

| Команда | Доступ к данным |
|---|---|
| `stage run` | файл change-снапшота + `--evidence-dir`; PostgreSQL state store не используется (подключение — «durable state-store wiring», ещё не сделано) |
| `run advance`, `run status` | PostgreSQL через `orchestration/state/run_store.py`: change из intake и run-стейт (execution/stage/attempt/stage_result/outbox); `DATABASE_URL`, иначе `DEFAULT_DATABASE_URL` |
| `reconcile`, `outbox *`, `api serve` | PostgreSQL через `orchestration/state/engine.py`: `DATABASE_URL`, иначе `DEFAULT_DATABASE_URL` = `postgresql+psycopg://dark_factory:dark_factory@localhost:5432/dark_factory` |
| `doctor` | только переменные окружения; соединение с БД не открывается (офлайн-проверки) |
| `ci_job` | файл `stage_result.json` + `$GITHUB_OUTPUT` |
| `release verify` | флаги значений наблюдаемого состояния + артефакт `image-digest.json` (T033); опционально change-снапшот + `--evidence-dir` для run record; PostgreSQL не используется |
| `run publish` | файл `run_record.json` из `--evidence-dir` (`stage run`/`release verify`) + `--runs-root`/`DARK_FACTORY_RUNS_ROOT`; PostgreSQL не используется |

Что пока **не подключено** к production-обвязке (явные заглушки и упрощения):

- `stage resume` — парсится, но всегда возвращает exit 2; его будущий читатель `load_run_record` уже есть;
- `run advance` исполняет ровно одну стадию детерминированным исполнителем (`orchestration/stages/`), поэтому в S1 он честно останавливается на `waiting`/`blocked` и никогда не доходит до `succeeded` (FR-009); harness-исполнитель стадии — срез S2;
- `stage run` не пишет в PostgreSQL state store — персистентность только через `--evidence-dir`; run-стейт подключится вместе с durable state-store wiring;
- бюджет стадии — дефолтный `BudgetSnapshot` без накопленного usage, `attempt_number` всегда 1, `usage = None` в StageResult;
- `pack_name`/`pack_version`/`blueprint_version`/`gitops_commit`/`okf_revision` в манифесте остаются незаполненными;
- ни один порт (`HarnessPort`, `SDDPort` и др.) и ни один адаптер (fakes, PydanticAI, GitHub) не вызывается из CLI.

## 3. Команды и коды выхода

Коды выхода всех команд (contract cli.md): `0` — успех, `1` — ошибка исполнения, `2` — неверный ввод/конфигурация, `10` — waiting, `20` — blocked.

| Команда | Подкоманды и флаги | Что делает | Коды |
|---|---|---|---|
| `stage run` | `--change`*, `--stage`*, `--route`, `--input-revision`, `--run-id`, `--json`, `--evidence-dir`, `--non-interactive` | Детерминированный прогон одной стадии | 0/10/20/1/2 |
| `stage resume` | `--run-id`*, `--next-action`* (`wa`/`ci`/`input`), `--json` | Заглушка not_implemented | 2 |
| `run status` | `--run-id`*, `--json` | Запуск из PostgreSQL: статус и стадии | 0/2 |
| `run advance` | `--change-id` XOR `--run-id` (обязательна ровно одна), `--json` | Ровно одна стадия запуска durable-раннером | 0/10/20/1/2 |
| `reconcile` | `--json` | Один проход Reconciler | 0/2 |
| `outbox dispatch` | `--once`, `--json`, `--limit`, `--cleanup` | Одна пачка доставки outbox | 0/2 |
| `outbox replay` | `--event-id`*, `--consumer`, `--json` | dead/failed → pending | 0/1/2 |
| `outbox skip` | `--event-id`*, `--consumer`*, `--json` | waived (решение оператора) | 0/1/2 |
| `doctor` | `--json` | 3 офлайн-проверки окружения | 0/2 |
| `api serve` | `--host` (по умолчанию `127.0.0.1`), `--port` (по умолчанию `8000`) | Поднять REST API локально | 0/2 |
| `release verify` | `--expected-digest` XOR `--digest-json`, `--application`, `--observed-digest`, `--argo-sync`, `--argo-health`, `--smoke-url` (+ `--smoke-digest-url`, `--smoke-digest-header`), `--evidence-dir`+`--change` (парой), `--run-id`, `--json` | Верификация деплоя: digest, Argo, smoke → evidence и rollback-сигнал | 0/1/2 |
| `run publish` | `--record`*, `--runs-root` (или `DARK_FACTORY_RUNS_ROOT`), `--json` | Публикация индекса run-записи в `dark-factory-runs` | 0/1/2 |

`*` — обязательный флаг. `--stage` принимает `specification`, `planning`, `construction`, `review_verification`, `release`; `--route` — `quick`, `standard` (недопустимые значения отсекает argparse, exit 2).

### 3.1. `stage run` (T009/T011/T012)

```mermaid
flowchart TD
    CMD["factory stage run"] --> LOAD["load_change_snapshot\nbytes -> YAML -> Change"]
    LOAD -->|"нет файла / не YAML /\nне соответствует схеме"| X2["exit 2\nстадия не стартует"]
    LOAD --> REV["input_revision\nsha256 сырых байтов снапшота\nили --input-revision"]
    REV --> RID["run_id\n--run-id или run_ + uuid4 hex"]
    RID --> KEY["operation_key\nrun_id:stage:input_revision"]
    KEY --> EVQ{"--evidence-dir\nзадан?"}
    EVQ -->|"нет"| EXEC
    EVQ -->|"да"| REPLAY{"committed result\nsucceeded или waiting?"}
    REPLAY -->|"да"| EMIT["выдать результат как есть\nбез повторного исполнения"]
    REPLAY -->|"нет, failed или blocked"| SNAP["change_snapshot.yaml\n+ collect_run_manifest"]
    SNAP -->|"коммиты не восстановлены"| X2
    SNAP --> EXEC["execute_stage\nbuild_context +\nrun_deterministic_stage"]
    EXEC --> PERSIST["stage_result.json\n+ run_record.json"]
    PERSIST -->|"запись не удалась"| E1["exit 1\nexecution_error"]
    PERSIST --> EMIT
    EMIT --> CODE["0 succeeded, 10 waiting,\n20 blocked, 1 failed"]
```

- **Снапшот (FR-001)**: файл читается как bytes (YAML-декодер сам обрабатывает UTF-8/UTF-16), парсится `yaml.safe_load`, валидируется `Change.model_validate`. Ошибка чтения, YAML или схемы → exit 2, стадия не стартует (evidence-dir даже не создаётся). С `--evidence-dir` сырой файл копируется байт-в-байт в `<evidence-dir>/change_snapshot.yaml` до начала исполнения.
- **`input_revision`**: `--input-revision`, а если не задан — SHA-256 hex **сырых байтов** файла снапшота (`compute_input_revision`), не парсенной модели. Пустое значение (включая пробелы) → exit 2.
- **`run_id`**: `--run-id` или сгенерированный `run_<uuid4 hex>`. Пустое значение → exit 2.
- **`operation_key`** (ADR-006 p.3): `run_id:stage:input_revision` — строка из `changes.keys.operation_key`. Печатается в текстовой сводке; в режиме `--json` уходит строкой `operation_key=...` в **stderr**, чтобы stdout остался ровно одним JSON-документом StageResult.
- **Идемпотентный replay (T012, FR-017, ADR-006 p.3)**: только с `--evidence-dir`. `EvidenceOperationStore` ищет committed `stage_result.json` той же операции: `succeeded`/`waiting` возвращается как есть — без второго исполнения, evidence не трогается, манифест не собирается; `failed`/`blocked` или отсутствие записи → свежее исполнение (новая попытка той же логической операции, внешние эффекты дедуплицирует effect ledger). Без `--evidence-dir` команда всегда исполняет.
- **Маршрут**: `--route` не задан → `standard` (консервативный полный набор гейтов; `quick` убирает UI-гейт на construction, ADR-005).
- **Манифест до старта (ADR-015 p.5)**: `collect_run_manifest` резолвит точные коммиты: `DARK_FACTORY_COMMIT` / `DARK_FACTORY_PRODUCT_COMMIT`, затем fallback `GITHUB_SHA`, затем git HEAD текущего чекаута. Коммит не определим → exit 2, стадия не стартует; ссылок вида `latest` не бывает. `factory_version` — из метаданных дистрибутива `dark-factory` (без метаданных — `0.0.0`); pack/blueprint/gitops/okf пока остаются незаполненными.
- **Исполнение**: `orchestration.stages.build_context` (дефолтный `BudgetSnapshot`, `attempt_number = 1`) + `run_deterministic_stage`. Детерминированный путь не вызывает harness/LLM (ADR-003) и не вычисляет гейты по product SHA (FR-009): required gates возвращаются `pending`, поэтому попытка заканчивается `waiting` или `blocked` и **никогда** `succeeded` (FR-009, SC-004). `--non-interactive` принимается для CI-прогонов.
- **Persist до wait (ADR-006 p.8)**: StageResult атомарно пишется в `<evidence-dir>/stage_result.json` (через `.tmp` + replace) до выхода с кодом 10. Сбой записи → exit 1: код 10 заявлял бы сохранённый результат, которого нет.
- **Run record (T011, ADR-015 p.4/p.5)**: компактный неизменяемый evidence-индекс пишется в `<evidence-dir>/run_record.json`. Маппинг статусов: `waiting/blocked/failed` → те же статусы run; `succeeded` стадии **не** завершает run — run остаётся `running`. `StageRun.id` = `<run_id>:<stage>:1`, `decisions` пока пустой.
- **Вывод**: текст — строка `stage <stage>: <status> (run_id=…, change_id=…, operation_key=…, next_action=…)` плюс `reason: …` для `wait_for_input`/`stop`; `--json` — ровно один JSON StageResult на stdout (поля: `schema_version`, `stage`, `run_id`, `change_id`, `attempt_number`, `input_revision`, `status`, `next_action`, `artifacts`, `evidence`, `gate_results`, `findings`, `escalations`, `usage`, `produced_at`), `operation_key=…` уходит в stderr.

Состав evidence-dir одной операции:

| Файл | Содержимое | Когда пишется |
|---|---|---|
| `change_snapshot.yaml` | байт-в-байт копия входного снапшота | до исполнения (FR-001) |
| `stage_result.json` | сериализованный StageResult — committed-артефакт операции | после исполнения, до выхода с 10 (ADR-006 p.8) |
| `run_record.json` | RunRecord: манифест + Change + ChangeRun/StageRun + StageResult | после persist результата (T011) |

Каждый файл несёт полную операционную идентичность (`run_id`, `stage`, `input_revision`), поэтому replay проверяет идентичность перекомпоновкой ключа из сохранённого результата: чужая, битая или частичная запись считается «нет committed-результата» и ведёт к свежему исполнению под защитой effect ledger.

### 3.2. `run advance` и `run status` (T-092) — durable-раннер

`run advance` — первый CLI-потребитель durable state store в роли *driver*: ровно одна стадия одного запуска за транзакцию. Обязательна ровно одна из `--change-id`/`--run-id` (mutually exclusive group, иначе argparse → exit 2).

- **`--change-id`** читает `Change` из intake (`ChangeRepository`) и резолвит запуск из снапшота: id детерминированный (`state.run_store.generated_run_id`), поэтому повторный вызов для того же снапшота продолжает тот же run, а не создаёт второй; неизвестный change → exit 2. Маршрут — консервативный `standard` (ADR-005), provider — из `change.product.provider` (ADR-019 p.5), бюджет — дефолтный `BudgetSnapshot`, контракт не утверждён (вход в construction тогда блокирует policy, T-016).
- **`--run-id`** продвигает существующий run, снапшот change берётся из intake.
- **Replay-first (ADR-006 p.3)**: до любой записи (включая lease) ищется committed-результат той же операции (`run_store.committed_result`: run + stage + input_revision + attempt). Коммитнутый `succeeded`/`waiting` возвращается как есть — outcome `replayed`, в БД не пишется ничего (то же правило, что у replay `stage run`); коммитнутый `failed`/`blocked` требует **новой попытки** той же операции (ADR-006 p.7 — протокол retry/resume среза S2), поэтому команда отказывает с exit 2, не трогая финализированный attempt.
- **Порядок одного прохода** (`orchestration/runner.py`): lease запуска с fencing token (ADR-006 p.6) → `running` через доменную таблицу (`ChangeRun.apply_status`) → `get_or_create_stage` + `append_attempt` (финализированный attempt не открывается заново: его `status` и `finished_at` не сбрасываются) → `build_context` с бюджетом run → инжектированный `StageExecutor` (в S1 — детерминированный `run_deterministic_stage`) → `apply_result` → атомарный persist → release lease.
- **Решение и запись — не одно и то же**: `apply_result` решает переход *в памяти* и остаётся единственным источником решений FSM; durable-writer затем зеркалит это решение по тем же доменным таблицам переходов (`state/run_store.py`: `advance_stage` для стадии, финализация attempt). **Известное ограничение**: статус run идёт через `ExecutionRepository.update_status`, который сегодня проверяет только `state_revision` + `fencing_token`, а не таблицу `RUN_STATUS_TRANSITIONS` — валидность перехода гарантирует `apply_result`, а не запись.
- **Персистится всё решение, а не только стадия**: immutable `stage_result` (FR-014), `pending`-строки стадий, которые решение создало и которых ещё нет в store (successor-стадия после `execute_stage`/`merge` — без неё run не продолжился бы на следующем вызове), статус и `state_revision` стадии, финализация attempt, статус run под `state_revision` + fencing token и событие `run.stage_completed` в outbox — всё в одной транзакции (ADR-006 p.8, ADR-016 p.1/p.5). Коммит делает вызывающий (`session_scope`), ровно один.
- **Коды выхода**: `0` — стадия завершена (`advanced`/`completed`), `10` — `waiting` (результат уже сохранён, ADR-006 p.8), `20` — `blocked`, `1` — `failed`, `2` — неверный ввод, неизвестный change/run, недоступный store, отказ store (конфликт/потерянный lease) или run, который нельзя продвинуть (терминальный, без активной стадии либо стадия с коммитнутым `failed`/`blocked` — протокол retry среза S2). Replay отдаёт код по статусу committed-результата (`succeeded` → 0, `waiting` → 10) — как `stage run`. В S1 детерминированный исполнитель не вычисляет гейты по product SHA (FR-009), поэтому честный исход — `10` или `20`.
- **Вывод**: текст — `run <id>: <outcome> (stage=…, stage_status=…, run_status=…, next_action=…, next_stage=…)` плюс `reason: …` для wait/stop; при replay — `run <id>: replayed (stage=…, result_status=…, next_action=…; nothing was written)`. `--json` — один объект с фиксированным набором ключей (`run_id`, `change_id`, `outcome`, `persisted`, `stage`, `result_status`, `next_action`, `attempt_number`, `stage_status`, `run_status`, `next_stage`, `reason`); при replay `persisted=false` и `null` в четырёх полях решения — flow не спрашивали, писать было нечего.

`run status` читает запуск из state store и печатает статус и стадии: текст — заголовок `run <id>: <status> (change_id=…, route=…, provider=…, state_revision=…)` и по строке на стадию (`stage: status (attempt=…, input_revision=…, state_revision=…)`); `--json` — документ доменной `ChangeRun` (`changes/run.py`). Команда только читает: exit `0` или `2` (неизвестный run / недоступный store).

`stage resume` остаётся заглушкой: парсится полностью, но обращается к `_not_implemented` — на stderr `factory stage resume: not implemented yet (planned in the durable state-store wiring)`, в `--json` на stdout `{"error": "not_implemented", "command": "…"}`; exit 2.

### 3.3. `reconcile` (T-063)

CLI-лицо запланированного Reconciler CronJob (ADR-006 p.5/p.9, интервал 2–5 минут, `concurrencyPolicy: Forbid`, `activeDeadlineSeconds: 300`): ровно один идемпотентный проход `GlobalReconciler` над PostgreSQL. owner для lease: переменная `DARK_FACTORY_RECONCILER_OWNER_ID` или уникальный на процесс `factory-reconcile-<pid>-<uuid4hex>` — параллельные проходы никогда не делят lease. Проход не зависит от вебхуков (ADR-019): вебхуки — только ускоритель, повторные запуски при неизменных данных ничего не меняют. Reconciler не трогает вебхуки и не исполняет задачи агентов — только state-level восстановление.

`schema_version` обоих отчётов — `1`. Отчёт: заголовок `factory reconcile: owner=… lease_acquired=… scanned=…` и по строке на run: `<run_id> [<desired_status>]: <action.kind> (<action.anomaly>) applied=<bool>` либо `no action`. `--json` отдаёт модель `ReconcileReport` как один JSON-объект (`owner_id`, `lease_acquired`, `scanned`, `entries`; в записи — `desired_status`, `desired_revision`, `in_sync`, `applied`, опциональный `note`).

- **Аномалии** (`orchestration/reconcile/models.py`, таблица «первое совпадение» в `reconcile.rules`): `repeated_error`, `expired_lease`, `duplicate_runs`, `merged_cr_without_run`, `approved_passed_without_merge`, `branch_behind`.
- **Действия**: `lease_takeover`, `supersede`, `record_merge`, `plan_merge`, `wait_for_human`, `update_branch`, `escalate`.
- **Коды**: любой завершённый проход — exit 0, включая `lease_acquired=False` (lease у другого reconciler'а — норма); недоступный/некорректный state store — exit 2 (liveness-проба соединением).

### 3.4. `outbox` (T028, ADR-016)

```mermaid
flowchart TD
    D1["outbox dispatch\nодна пачка: --limit\nили 100"] --> R["reserve_batch\nFOR UPDATE SKIP LOCKED"]
    R --> OG{"ordering gate:\nв потоке нет незакрытой\nдоставки с меньшим sequence?"}
    OG -->|"нет (gap)"| DEF["deferred\nстрока не тронута"]
    OG -->|"да"| L["lease\nnext_attempt_at = now + 120s"]
    L --> H["handler вне транзакции"]
    H --> OC["commit_outcome\nguard: status in pending/failed"]
    OC --> OK["delivered"]
    OC --> F["failed\nbackoff, next_attempt_at"]
    F -->|"исчерпан бюджет попыток"| DD["dead"]
    DD --> RP["outbox replay\n-> pending, attempts=0"]
    DD --> SK["outbox skip\n-> waived"]
```

- **`outbox dispatch`** — ровно один проход `OutboxDispatcher`, тот же, что выполняет CronJob `deploy/events/outbox-dispatcher-cronjob.yaml` (расписание `*/2 * * * *`, `Forbid`, `activeDeadlineSeconds: 300`). `--once` принимается для совместимости с контрактом, но проход всегда один — расписание принадлежит CronJob, а не циклу CLI. `--limit` ограничивает резервируемую пачку (по умолчанию `DISPATCH_BATCH_SIZE = 100`); значение < 1 → exit 2. `--cleanup` добавляет к проходу удаление событий за пределами retention (ADR-016 p.9); `cleaned` — единственный event-level исход, у него `consumer_id = None`. Отчёт: заголовок `factory outbox dispatch: reserved=N delivered=… failed=… deferred=… cleaned=…` и по строке на исход (`evt-1/tracker: delivered (attempts=1)`); `--json` — один объект `DispatchReport`. Виды исходов (`DeliveryOutcomeKind`): `delivered`, `failed`, `deferred`, `dead`, `replayed`, `skipped`, `cleaned`.
- **Механика прохода**: резервирование `SKIP LOCKED` в короткой транзакции, lease через `next_attempt_at = now + 120 c` (`LEASE_SECONDS`); вызов handler'а — строго вне транзакции; ordering gate откладывает кандидата с пробелом в потоке (`deferred`), оставляя его строку нетронутой — заблокированный поток занимает слоты пачки, пока оператор не сделает replay/skip. `commit_outcome` защищён guard'ом `status IN (pending, failed)` и не перезапишет появившийся гонкой `dead`/`waived`.
- **`outbox replay`** — ручной replay (ADR-016 p.5): доставки события в статусах `dead`/`failed` сбрасываются в `pending` с обнулением `attempts`, `next_attempt_at` и `last_error`; `--consumer` сужает до одного потребителя. Ничего не совпало → сообщение на stderr и exit 1. Совпадения печатаются парами `<consumer> <previous> -> pending (attempts reset)`; `--json` — `{event_id, replayed: [{consumer_id, previous_status}]}`.
- **`outbox skip`** — административный отказ оператора (ADR-016 p.6): одна доставка (`--event-id` + обязательный `--consumer`) в статусе `pending`/`failed`/`dead` переводится в `waived` — терминальный принятый статус, разблокирующий поток. Ничего не совпало → exit 1.
- **Коды**: 0 — команда завершена (включая проход без работы), 1 — цель replay/skip не найдена или не в подходящем статусе, 2 — state store недоступен или конфигурация неверна (`--limit` < 1). URL и сырой текст исключений не печатаются (ADR-009).

### 3.5. `doctor` (T008)

Офлайн-проверки того, от чего зависит стадия на старте; соединение с БД не открывается — недоступная база не ошибка этой команды (YAGNI US1), ошибкой считается только невалидная конфигурация.

| Проверка | ok | warn | error |
|---|---|---|---|
| `python_runtime` | `sys.version_info[:2] >= (3, 12)` (зеркало `requires-python`) | — | интерпретатор старше 3.12 |
| `package` | `dark_factory` импортируется и метаданные `dark-factory` читаются | пакет импортируется, метаданных нет | пакет не импортируется |
| `state_store_config` | `DATABASE_URL` — валидный postgres-URL | `DATABASE_URL` не задан или пустой | URL не парсится или схема не postgres-семейства (`postgresql*` / legacy `postgres`) |

Валидный URL показывается маскированно — `scheme://host:port/database` пользователь, пароль и query отбрасываются, текст исключения о непарсируемом URL не выводится (ADR-009). Отчёт: `render_text` (строка на проверку + `summary: status=… ok=… warn=… error=…`) или `render_json` (`{checks: [{name, status, detail}], summary}`). Итоговый статус — худший из проверок; exit 0 при отсутствии `error`, иначе 2 (`warn` не влияет).

### 3.6. `api serve` (T035)

Поднимает REST API контракта api.md локально: `create_app(create_session_factory(engine))` из `dark_factory.api` под `uvicorn.run(app, host, port)`. `DATABASE_URL` обязателен: не задан → exit 2; liveness-проба соединением не прошла → exit 2 (URL и текст исключения не печатаются). Хранилище токенов берётся из `DARK_FACTORY_API_TOKENS` (`ApiTokenStore.from_env`): без переменной store пуст и все мутации fail-closed (ADR-009 p.7). После остановки сервера — exit 0; `engine.dispose()` в `finally`.

### 3.7. `ci_job` — job-скрипт CI (T025)

Отдельная точка входа: `python -m dark_factory.cli.ci_job <stage_result.json> [--outputs-file "$GITHUB_OUTPUT"]`. Вторая половина контракта `stage run`: один job = одна стадия Flow в `.github/workflows/factory-stage.yml` (шаг `stage run … --json --non-interactive --evidence-dir factory-evidence > stage_result.json` идёт первым, затем `doctor`).

- **Валидация** (`parse_stage_result`): JSON-объект, `schema_version == 1`, `status` ∈ {`waiting`, `succeeded`, `failed`, `blocked`}, `next_action` — объект с непустым `type`, `stage`/`run_id`/`change_id` — непустые строки; неизвестные поля допускаются. Нарушение → `StageJobError`, аннотация `::error::invalid stage result: …`, exit 2; сырой документ не эхо-ится.
- **Outputs** (`render_outputs`): строки `status`, `next_action` (тип действия), `run_id`, `change_id`, `usage_total_tokens`, `usage_cost` — в `--outputs-file` и на stdout. Пустые значения печатаются, а не пропускаются. `usage_total_tokens` = `total_tokens` или сумма `prompt_tokens + completion_tokens`; оба usage-поля пусты при `usage=None` (детерминированный путь всегда так).
- **Коды выхода**: `succeeded` и `waiting` → 0 — `waiting` не ошибка (ADR-006 p.8): результат сохранён до внешнего ожидания, продолжение — новый `stage run`; `blocked` → 20, `failed` → 1 с аннотацией `::error::stage <stage> <status>: <reason>`; `invalid_input` → 2. Причина берётся из `next_action.reason` (fallback — тип действия), схлопывается в одну строку и обрезается до 280 символов с `...`.
- Outputs рендерятся **до** аварийного выхода: читатели job outputs с `if: always()` видят решение стадии.
- `classify_exit_code` кодирует таблицу кодов `stage run` для тестов и программных вызовов: 0→`ok`, 10→`waiting`, 20→`blocked`, 1→`failed`, 2→`invalid_input`; неизвестный код → `ValueError`.

### 3.8. `release verify` (T034, docs T-045)

CLI-лицо верификации релиза (US5): проверка неизменности digest деплоя (FR-011), статусов Argo Application (ADR-010) и smoke-пробы (FR-013) — затем детерминированное решение `quality/release/`: статус `released` только когда прошли все проверки; каждая неудача несёт rollback-сигнал для Operation (revert GitOps-коммита с digest — Argo auto-sync вернёт прежний, ADR-011 п.6). БД не касается.

- **Входы (валидация до любых проб, fail fast)**: ожидаемый digest — `--expected-digest` XOR `--digest-json` (артефакт `image-digest.json` T033, schema v1 — читается только поле `digest`); цель — `--application` (`namespace/name`, попадает в evidence); наблюдаемое состояние — значения `--observed-digest`/`--argo-sync`/`--argo-health` (живого Argo/K8s-ридера нет — осознанный YAGNI, читатель состояния за тем же швом — отдельная задача).
- **Smoke**: `--smoke-url` (HTTP health-проба, 2xx = pass) + опционально `--smoke-digest-url`/`--smoke-digest-header` (digest-проба связывает работающий под с promoted digest, FR-011/T033). Без smoke-опций вердикт «smoke was not run» — fail-closed (FR-013). Пробы идут за швом `SmokeProbe` (`quality/release/probes.py`); URL и значения состояния не эхоятся (ADR-009).
- **Короткое замыкание**: при провале digest/Argo-проверок (`pre_smoke_failure`) пробы не отправляются — чужой деплой не зондировать (FR-011).
- **Evidence**: `--evidence-dir` + `--change` — только парой (снапшот фиксирует, что проверялось): run record с секцией `release` (`RunRecord.release` — `ReleaseEvidence`: digest, сырые Argo-статусы, результаты проб, решение, rollback-сигнал; `schema_version` остался 1 — расширение аддитивно).
- **Коды выхода**: `0` = released; `1` = release_failed — CI краснеет, при настроенном evidence run record всё равно персистится (ничего не теряется молча); `2` = invalid input (ничего не верифицировано).

### 3.9. `run publish` (T-061, ADR-015 p.4)

CLI-лицо протокола run-записи: берёт `run_record.json`, который уже создали `stage run` (T011) или `release verify` (T034) в `--evidence-dir`, проверяет его и раскладывает компактный индекс в checkout репозитория `dark-factory-runs` (ADR-015 п.4). Отдельная команда выбрана намеренно: стадии идут в sandboxed подах (ADR-018), а запись в аудит-репозиторий — действие доверенного публикатора (то же разделение, что у merge-политики, T-026). БД не касается.

- **Корень репозитория**: `--runs-root` или переменная `DARK_FACTORY_RUNS_ROOT`; не задан ни один → exit 2, ничего не записано.
- **Валидация входа**: файл читается и парсится в `RunRecord` до любых записей — нечитаемый файл или несоответствие схеме → exit 2, частичного дерева не остаётся.
- **Публикация** (`execution/runs/RunRecordStore`): детерминированный partitioning `runs/<YYYY>/<MM>/<change>/<run>`, идемпотентность (повтор той же записи → `unchanged`), immutability (иное содержимое по тому же адресу → ошибка), атомарная запись через staging-каталог; подробности — [execution.md](execution.md).
- **Отказ публикации**: секрет или превышение размера, неполная цепочка evidence, конфликт immutability → exit 1; диагностика называет JSON-путь и вид шаблона, но не значение (ADR-009).
- **Вывод**: текстовая строка или `--json` `{"outcome", "change_id", "run_id", "path"}`.
- **Коды выхода**: `0` — created или unchanged; `1` — запись отклонена; `2` — невалидный вход/конфигурация.

## 4. Парсинг и обработка ошибок (`main.py`)

- **Дерево команд**: `build_parser()` строит `argparse` с `prog="factory"` и обязательными subparsers (`metavar="command"`) на каждом уровне: `stage {run,resume}`, `run {status,publish}`, `reconcile`, `outbox {dispatch,replay,skip}`, `doctor`, `api {serve}`, `release {verify}`. Голое `factory`, `factory stage` или `factory outbox` → ошибка argparse, exit 2.
- **Выборы значений**: `--stage`, `--route`, `--next-action` ограничены `choices` из StrEnum (`Stage`, `Route`, `ResumeNextAction`) — недопустимое значение отсекается argparse'ом с exit 2, совпадающим с контрактом. `--help` на любом уровне → exit 0.
- **Типизированные аргументы**: `parse_command(argv)` = `build_command_args(build_parser().parse_args(argv))`. Плоский namespace превращается в один из одиннадцати frozen-датаклассов union `CommandArgs` (`StageRunArgs`, `StageResumeArgs`, `RunStatusArgs`, `RunPublishArgs`, `ReconcileArgs`, `OutboxDispatchArgs`, `OutboxReplayArgs`, `OutboxSkipArgs`, `DoctorArgs`, `ApiServeArgs`, `ReleaseVerifyArgs`). Чтение полей идёт через `_option_str`/`_option_int`/`_required_str`/`_flag`: нарушение типа после argparse — программистская ошибка, `AssertionError`.
- **Диспетчеризация**: `dispatch(command)` — исчерпывающий `match` по всем вариантам `CommandArgs`; ветка по умолчанию — `assert_never`, поэтому новая команда требует нового кейса на этапе компиляции.
- **Заглушки**: `_not_implemented(command, planned_task)` печатает диагностику (текст — stderr, JSON — stdout) и возвращает exit 2.
- **Циклические импорты**: `cli.stage`, `cli.reconcile`, `cli.outbox`, `cli.api`, `cli.runs` импортируют args-датаклассы и коды выхода из `main`, поэтому `main` импортирует их лениво — внутри обработчиков.
- **`main(argv)`** возвращает `int` — код процесса; `SystemExit` поднимает обёртка: console-script из `pyproject [project.scripts]` или `__main__.py` (`raise SystemExit(main())`).
- **Гигиена вывода (ADR-009)**: секреты не попадают в отчёты — `doctor` маскирует URL до `scheme://host:port/database`, outbox/reconcile/api не печатают URL и сырой текст исключений, `stage run` в ошибках валидации показывает первые 3 pydantic-ошибки без эхо-входа, аннотации `ci_job` несут только доменные поля.

## 5. Граничные случаи

| Случай | Поведение |
|---|---|
| Команда или подкоманда не указана (`factory`, `stage`, `outbox`) | argparse: exit 2 |
| Неизвестное значение `--stage` / `--route` / `--next-action` | argparse choices: exit 2 |
| Пустые `--run-id` / `--input-revision` (`""`, пробелы) | exit 2, тег `invalid_input` |
| Снапшот: нет файла, не YAML, не соответствует схеме `Change` | exit 2; стадия и evidence-dir не создаются |
| На месте `--evidence-dir` обычный файл | exit 2 «cannot fix the input snapshot» до исполнения |
| Коммиты не восстанавливаются (env пуст, git HEAD недоступен) | exit 2 до старта стадии; `stage_result.json` не пишется |
| Запись `stage_result.json` / `run_record.json` не удалась | exit 1 `execution_error` (не 10/20 — сохранения не существует) |
| Повтор `stage run` с тем же `operation_key`, committed `succeeded`/`waiting` | replay: результат возвращается как есть, исполнение не повторяется |
| Committed `failed`/`blocked` или запись отсутствует | свежее исполнение — новая попытка той же операции |
| `--json` у `stage run` | `operation_key=…` в stderr; stdout — ровно один JSON |
| `doctor`: `DATABASE_URL` не задан или пустой | `warn`, exit 0; ошибка схеме URL — `error`, exit 2 |
| `ci_job`: `status = waiting` | exit 0, без аннотации — waiting не ошибка |
| `ci_job`: `blocked`/`failed` | outputs пишутся до выхода; `::error::` с reason ≤ 280 символов, одна строка |
| `outbox dispatch --limit 0` | exit 2 до обращения к БД |
| `outbox replay`/`skip`: цель не найдена или не в подходящем статусе | exit 1 |
| `reconcile`: `lease_acquired = False` | exit 0 — нормальный исход при конкурентном CronJob |
| `api serve`: `DATABASE_URL` не задан или store недоступен | exit 2 |
| `release verify`: не указан ожидаемый digest (ни `--expected-digest`, ни `--digest-json`) или указаны оба | exit 2 до любых проб |
| `release verify`: `--evidence-dir` без `--change` (или наоборот) | exit 2 — run record индексирует change |
| `release verify`: `--smoke-digest-url` без `--smoke-url` | exit 2 — health-проба основа набора |
| `release verify`: digest/Argo-проверка провалена | пробы не отправляются, exit 1, rollback-сигнал в отчёте и evidence |
| `release verify`: smoke не пройден или не запускался | `released` невозможен (FR-013, fail-closed), exit 1 |
| `run publish`: не задан `--runs-root` и пуст `DARK_FACTORY_RUNS_ROOT` | exit 2, тег `invalid_input`, ничего не записано |
| `run publish`: записи нет или она не соответствует схеме `RunRecord` | exit 2; каталоги runs-репозитория не создаются |
| `run publish`: секрет, превышение размера или неполная цепочка evidence | exit 1 `run_record_rejected`; значение не эхоится (ADR-009) |
| `run publish`: по адресу уже лежит другая запись | exit 1 — immutability (ADR-015 п.4), перезаписи нет |
| `run publish`: та же запись уже опубликована | exit 0, `unchanged`, файлы не переписываются |

## 6. Где искать проверки

- [test_cli_parser.py](../../tests/test_cli_parser.py) — дерево команд, дефолты, choices, exit-коды argparse, заглушки not_implemented, декларация console-script и `python -m`;
- [test_cli_stage.py](../../tests/test_cli_stage.py) — контракт StageResult, детерминизм `input_revision`, evidence-dir, коды выхода, обработка повреждений evidence;
- [test_cli_doctor.py](../../tests/test_cli_doctor.py) — статусы трёх проверок, маскирование секретов, коды выхода;
- [test_cli_ci_job.py](../../tests/test_cli_ci_job.py) — классификация кодов, валидация StageResult, outputs, `::error::`-аннотации;
- [test_cli_outbox.py](../../tests/test_cli_outbox.py) — рендер отчёта dispatch, конфигурационные ошибки (exit 2), диспетчеризация команд;
- [test_cli_reconcile.py](../../tests/test_cli_reconcile.py) — рендер отчёта, уникальный owner_id, ошибки конфигурации;
- [test_cli_run_records.py](../../tests/test_cli_run_records.py) — резолв коммитов манифеста (env → `GITHUB_SHA` → git HEAD), сборка записи, маппинг статусов run/stage, round-trip release-секции;
- [test_cli_release.py](../../tests/test_cli_release.py) — exit-коды, invalid-ветки, e2e DoD (успешный и неуспешный smoke → статусы + evidence), rollback-сигнал, короткое замыкание проб, отсутствие эха секретов;
- [test_cli_runs.py](../../tests/test_cli_runs.py) — `run publish`: exit-коды, форма `--json`, fallback на `DARK_FACTORY_RUNS_ROOT`, отсутствие эха секрета, созданные файлы записи;
- [test_cli_parser.py](../../tests/test_cli_parser.py) — дополнительно парсинг `release verify` и `run publish`, их флагов и отсутствующих обязательных опций.

Интеграционные проги dispatch/reconcile над реальной БД живут в отдельных integration-тестах (unit-слои этих файлов БД не касаются).

## 7. Связанные решения

- [ADR-004](../adr/ADR-004-postgresql-factory-state.md) — PostgreSQL state store: `DATABASE_URL`, локальный дефолт, liveness-проба с exit 2;
- [ADR-005](../adr/ADR-005-stage-scoped-graphs-light-workflow-core.md) — маршруты и применимость гейтов: дефолт `standard` для `stage run`;
- [ADR-006](../adr/ADR-006-ephemeral-job-pods-reconciler-cronjob.md) — ephemeral job pods: одна стадия = один job, `operation_key` и идемпотентный replay, persist-before-wait (p.8), Reconciler CronJob (p.5);
- [ADR-015](../adr/ADR-015-repository-boundaries.md) — run record как evidence-индекс, точные коммиты вместо `latest` (p.4/p.5);
- [ADR-011](../adr/ADR-011-risk-based-merge-release-policy.md) — merge/release policy по классам риска: статус «выпущено» только при успешном smoke (п.6), rollback-сигнал;
- [ADR-010](../adr/ADR-010-local-k8s-helm-argocd.md) — GitOps-деплой: Argo sync/health в верификации релиза;
- [ADR-016](../adr/ADR-016-postgresql-outbox.md) — transactional outbox: dispatch-пачка с lease (p.4), manual replay (p.5), operator skip (p.6), retention cleanup (p.9);
- [ADR-019](../adr/ADR-019-multi-provider-sc-ci-github-first.md) — GitHub-first CI: job-скрипт `ci_job` и workflow `factory-stage.yml`, reconcile без вебхуков (p.5).

## 8. Связь с другими модулями

- [orchestration-execution.md](orchestration-execution.md) — детерминированные стадии, которые исполняет `stage run`;
- [orchestration-operations.md](orchestration-operations.md) — Reconciler и outbox, к которым CLI даёт ручной доступ;
- [orchestration-flow-and-state.md](orchestration-flow-and-state.md) — межстадийный FSM, state store, идемпотентность и lease, на которые опираются `reconcile`/`outbox`/`api serve`;
- [api.md](api.md) — REST API, который обслуживает `api serve`;
- [execution.md](execution.md) — `RunRecordStore`, в который пишет `run publish`.
