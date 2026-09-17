# Runtime — `runtime/`: composition root [T-092 S2]

## 1. Назначение

`runtime/` отвечает на один вопрос: **чем собран работающий процесс фабрики** — какие реализации портов берутся и из какой конфигурации.

До T-092 такого места не было, и это не «недостающая деталь», а разрыв (ADR-024, контекст): ядро (всё вне `dark_factory.adapters`) не имеет права импортировать адаптеры (правило A теста границ), а адаптеры — импортировать ядро за пределами `dark_factory.ports` (правило B). Значит, ни один модуль не мог соединить сервисы ядра с реализациями портов, и harness/SCM не были подключены к рабочему пути ни в одной задаче реестра.

`dark_factory.runtime` — **один именованный слой-исключение**: только ему правило A разрешает импорт `dark_factory.adapters` (allowlist в `tests/test_import_boundaries.py`, ADR-024 п.5). Обратного ребра нет: правило D того же теста запрещает любому модулю вне `dark_factory.runtime` — ядру и соседям по имени вроде `dark_factory.runtimes` — импортировать `runtime` или его подпакеты, а `adapters → runtime` ловит правило B. Сам `runtime` и его подпакеты себя импортировать могут. Запрет действительно проверяется тестом границ, а не остаётся декларацией.

## 2. Границы слоя

Слой тонкий по требованию ADR-024 п.5: **связывание и конфигурация, без доменной логики**.

| Входит | Не входит |
|---|---|
| Чтение конфигураций адаптеров (`HarnessConfig`, `GitHubConfig`, `TelemetryConfig`, `WorktreeExecutionConfig`) | Решения о переходах, статусах, гейтах, риске |
| Сборка адаптеров (`PydanticAIHarness`, `GitHubAdapter`, `OtlpTelemetryAdapter`, `WorktreeExecution`) | Драйвер запуска (`orchestration/runner`) — он зависит только от портов |
| Привязка инструментов роли к harness (`Runtime.harness_of`) | Реализации инструментов (`orchestration/stages/tools`) — это ядро |
| Выдача швов рабочего пути: `agent_stage_executor()`, `revision_of()`, `facts_provider()` | Персистентность, идемпотентность, транзакции — это `orchestration/state` |
| Точка входа процесса `factory` (`runtime.entrypoint:main`, ADR-025): ленивая сборка под команду и передача швов в CLI | Логика команд, разбор аргументов, коды выхода — это `cli/` |

Если `runtime` начнёт принимать решения о том, *что делать*, а не о том, *чем это делать*, — слой вышел за границу.

## 3. `build_runtime` и `Runtime` (`composition.py`)

`build_runtime(*, env=None, execution=None, token_provider=None, transport=None, base_ref, branch_prefix) -> Runtime` — одна функция сборки. Неполная конфигурация даёт **отсутствующий** адаптер, а не fallback:

| Пропущено | Следствие |
|---|---|
| `DARK_FACTORY_LLM_*` | `harness_config` пуст; `harness_of` бросает `RuntimeNotConfiguredError` с подсказкой |
| `DARK_FACTORY_GITHUB_*` | Нет `GitHubAdapter` → `repository`/`merge_requests`/`revision_of()`/`facts_provider()` — `None` |
| `DARK_FACTORY_WORKSPACE_ROOT` / `DARK_FACTORY_WORKSPACE_MIRROR_ROOT` | Нет `WorktreeExecution` (TD-022) → `agent_stage_executor()` — `None` |

Исключение — telemetry: её конфигурация **fail-closed** (`TelemetryConfig.from_env` бросает `ValueError` на неизвестный exporter или `file` без пути), потому что тихая подмена скрыла бы опечатку; адаптер присутствует всегда (в MVP — console). `WorktreeExecutionConfig` fail-closed в той же степени, в какой это возможно: незаданные переменные — отсутствующий адаптер, а заданные криво (относительный путь, нечисловой таймаут) — `ValueError`, не тихое игнорирование.

`Runtime` — frozen dataclass и пассивный держатель адаптеров. Его методы — только связывание:

- `harness_of(profile, tools)` — строит harness стадии с инструментами роли. Именно здесь встречаются декларативные имена из `AgentProfile.tools` (разрешённые в callables над workspace) и адаптер, который их исполняет: `HarnessPort.run_stage` принимает конверт, а PydanticAI-адаптер берёт набор инструментов при конструировании (`role_tools`), поэтому harness собирается на стадию.
- `agent_stage_executor()` — агентный `StageExecutor` или `None`, если не хватает harness, провайдера или execution-порта.
- `revision_of()` — SCM-derived резолвер ревизии (`ScmRevision`, ADR-006 p.4).
- `facts_provider()` — `ScmFactsProvider` (`runtime/facts.py`, срез S3) или `None` без `merge_requests`: синхронный `FactsProvider` для драйвера, наблюдающий гейт-факты через `MergeRequestPort.observe` (статус CR, head/merged SHA) и `PipelinePort.status` на head SHA; ревью с привязкой к head конвертируются в version-bound `Decision`, непривязанные отбрасываются (ничего не авторизуют). CR берётся из снапшота запуска с fallback на cold lookup по FR-011; неизвестный CR — `None` (легитимный случай), а прочие ошибки провайдера не маскируются — advance честно падает, ничего не записав, по дисциплине `ScmRevision`.
- `aclose()` — освобождение ресурсов (HTTP-пул GitHub-адаптера, tracer provider).

## 4. Как собранное связывание попадает в рабочий путь

Ядро не импортирует `runtime`, поэтому привязка приходит **аргументом**, а не импортом:

```text
dark_factory.runtime.entrypoint:main          dark_factory.cli / orchestration
        │  build_runtime()                             │
        │  executor, revision_of, gate_facts               │
        └─────► cli.main.main(argv, …) ──► run_advance_command ──► advance_run ──► StageExecutor
```

Точка входа процесса — `dark_factory.runtime.entrypoint:main` (console-script `factory` в `pyproject [project.scripts]`, ADR-025). Модуль — **связывание, а не логика** (ADR-024 п.5): он разбирает команду через `cli.main.parse_command` и собирает runtime из окружения процесса (`build_runtime()`) только для команд, которым нужны биндинги: `run advance` (исполнитель стадии, резолвер ревизии и провайдер гейт-фактов, T-092 S3) и `api serve` (переключатели этапов CI, T059), после чего вызывает `cli.main.main(argv, …)` с соответствующими биндингами. Швы протащены значениями: `cli.main.main` → `dispatch` → `_advance_run` → `cli.runner.run_advance_command`. Типы `StageExecutor`/`RevisionResolver` живут в `orchestration.runner` и подключены в CLI под `TYPE_CHECKING`: модуль остаётся core и не тянет драйвер в свой импорт.

Композиция **ленивая по команде**:

- `run advance` — рабочий путь потребляет швы `executor`/`revision_of`/`gate_facts`. Runtime собирается, швы передаются, а собранные адаптеры освобождаются в `finally` (`asyncio.run(runtime.aclose())`) — в том числе когда команда завершилась ошибкой;
- `api serve` — собирает runtime ради переключателей этапов CI (T059, ADR-027): биндинги `ci_toggles`/`ci_repository` передаются в CLI аргументом (значения, не импорты), runtime освобождается в `finally`; без `DARK_FACTORY_GITHUB_*` и `DARK_FACTORY_GITHUB_REPOSITORY_SLUG` адаптер не строится, и `/ci/*` честно отвечают «не сконфигурировано»;
- `doctor`, `stage run`, `run status`, `reconcile`, outbox-команды, `release verify` — зависят только от core: runtime не собирается, окружение сверх нужного самой команде не читается, поведение — ровно как у CLI;
- `python -m dark_factory.cli` — **явный core-путь**: та же команда без сборки процесса (`executor`/`revision_of`/`gate_facts` = `None`, детерминированный исполнитель).

`advance_run` и `cli.runner.run_advance_command` имеют необязательные швы `executor`, `revision_of` и `gate_facts`; без них работает детерминированный путь (`waiting`/`blocked`), digest снапшота как ревизия, а внешние ожидания не разрешаются — поведение среза S1 не меняется, и `run_advance_command` остаётся вызываемым напрямую.

## 5. Граничные случаи

- Пустое окружение — валидный `Runtime`: telemetry есть, остального нет, сборки исполнителя нет.
- `harness_of` без конфигурации — `RuntimeNotConfiguredError`, а не `None`-harness: запрос на несуществующее обязан быть громким.
- Сломанная telemetry-конфигурация — `ValueError` из `build_runtime`, не «telemetry отключена».
- Инструменты не собираются без workspace: `Runtime.harness_of` принимает уже разрешённые callables, а не имена.
- Команда, которой связывание не нужно (`doctor`, `stage run`, `run status`, …), — runtime не собирается, `build_runtime` не вызывается.
- Ошибка разбора команды (`factory bogus`, недопустимое значение, `--help`) — argparse выходит с кодом 2/0 **до** решения о сборке: runtime не собирается.
- Сбой внутри `run advance` (исключение из CLI) — runtime всё равно закрыт (`finally`), ресурсы адаптеров не утекают.
- `run advance` без сконфигурированного окружения — runtime собирается, но честно отдаёт `executor`/`revision_of`/`gate_facts` = `None`, и команда идёт детерминированным путём. Агентный путь активируется, когда полный набор (`DARK_FACTORY_LLM_*`, `DARK_FACTORY_GITHUB_*`, `DARK_FACTORY_WORKSPACE_ROOT` + `DARK_FACTORY_WORKSPACE_MIRROR_ROOT`) задан; работа в worktree идёт от локального зеркала, подготовленного оператором (TD-022), живой прогон на пилотном репозитории — T-072. Разрешение внешних ожиданий (гейты/merge, S3) активируется вместе с `DARK_FACTORY_GITHUB_*`: без facts-провайдера waiting-стадия остаётся в ожидании.

## 6. Где искать проверки

- [`test_runtime_entrypoint.py`](../../tests/test_runtime_entrypoint.py) — точка входа процесса: `run advance` и `api serve` собирают runtime и передают свои биндинги в CLI, runtime закрывается (в том числе при ошибке команды), команда без связывания runtime не собирает и биндингов не передаёт, ошибка разбора не собирает runtime, `cli.main` доносит швы до `runner.run_advance_command`, а биндинги этапов CI — до `cli.api.run_api_serve_command`, и без швов ведёт себя как раньше;
- [`test_runtime_composition.py`](../../tests/test_runtime_composition.py) — пустая и полная конфигурация, сборка исполнителя и её отсутствие без `ExecutionPort`, привязка инструментов роли к harness, громкий отказ `harness_of`, fail-closed telemetry, отсутствие `facts_provider()` без GitHub-конфига, `aclose`;
- [`test_runtime_facts.py`](../../tests/test_runtime_facts.py) — `ScmFactsProvider`: факты и вердикты на head SHA, merged, ревью → version-bound `Decision` (непривязанные отброшены), headless CR без pipeline-факта (FR-009), cold lookup и неизвестный CR → `None`;
- [`test_import_boundaries.py`](../../tests/test_import_boundaries.py) — allowlist `runtime` в правиле A, запрет обратного ребра (правило D: ядро вне `runtime` — `dark_factory.cli`, `dark_factory.orchestration` и т.п. — не импортирует `runtime` и его подпакеты; `adapters → runtime` — правило B) и запрет соседям (`dark_factory.cli` и т.п.) импортировать адаптеры;
- [`test_orchestration_agent_stage.py`](../../tests/test_orchestration_agent_stage.py) — агентный исполнитель и инструменты, которые `runtime` связывает.

## 7. Связанные решения

- [ADR-025](../adr/ADR-025-process-entry-point-and-lazy-composition.md) — точка входа процесса и ленивая композиция: `factory` → `runtime.entrypoint:main`, сборка только для `run advance`, `python -m dark_factory.cli` как core-путь;
- [ADR-024](../adr/ADR-024-durable-run-driver-and-composition-root.md) — durable-драйвер и composition root (п.5 — границы слоя, allowlist, запрет обратного ребра);
- [ADR-015](../adr/ADR-015-repository-boundaries.md) п.3 — правила границ импортов и versioned-контракты;
- [ADR-007](../adr/ADR-007-nine-role-catalog.md) п.3 — контракт `AgentProfile → TaskEnvelope → AgentResult` и привязка инструментов;
- [ADR-006](../adr/ADR-006-ephemeral-job-pods-reconciler-cronjob.md) п.4 — SCM-derived ревизия операции;
- [ADR-009](../adr/ADR-009-minimal-bootstrap-otel.md) — гигиена секретов и наблюдаемость.
