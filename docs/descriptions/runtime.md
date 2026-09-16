# Runtime — `runtime/`: composition root [T-092 S2]

## 1. Назначение

`runtime/` отвечает на один вопрос: **чем собран работающий процесс фабрики** — какие реализации портов берутся и из какой конфигурации.

До T-092 такого места не было, и это не «недостающая деталь», а разрыв (ADR-024, контекст): ядро (всё вне `dark_factory.adapters`) не имеет права импортировать адаптеры (правило A теста границ), а адаптеры — импортировать ядро за пределами `dark_factory.ports` (правило B). Значит, ни один модуль не мог соединить сервисы ядра с реализациями портов, и harness/SCM не были подключены к рабочему пути ни в одной задаче реестра.

`dark_factory.runtime` — **один именованный слой-исключение**: только ему правило A разрешает импорт `dark_factory.adapters` (allowlist в `tests/test_import_boundaries.py`, ADR-024 п.5). Обратного ребра нет: ни `adapters/`, ни core не импортируют `runtime` — это проверяется тем же тестом.

## 2. Границы слоя

Слой тонкий по требованию ADR-024 п.5: **связывание и конфигурация, без доменной логики**.

| Входит | Не входит |
|---|---|
| Чтение конфигураций адаптеров (`HarnessConfig`, `GitHubConfig`, `TelemetryConfig`) | Решения о переходах, статусах, гейтах, риске |
| Сборка адаптеров (`PydanticAIHarness`, `GitHubAdapter`, `OtlpTelemetryAdapter`) | Драйвер запуска (`orchestration/runner`) — он зависит только от портов |
| Привязка инструментов роли к harness (`Runtime.harness_of`) | Реализации инструментов (`orchestration/stages/tools`) — это ядро |
| Выдача швов рабочего пути: `agent_stage_executor()`, `revision_of()` | Персистентность, идемпотентность, транзакции — это `orchestration/state` |

Если `runtime` начнёт принимать решения о том, *что делать*, а не о том, *чем это делать*, — слой вышел за границу.

## 3. `build_runtime` и `Runtime` (`composition.py`)

`build_runtime(*, env=None, execution=None, token_provider=None, transport=None, base_ref, branch_prefix) -> Runtime` — одна функция сборки. Неполная конфигурация даёт **отсутствующий** адаптер, а не fallback:

| Пропущено | Следствие |
|---|---|
| `DARK_FACTORY_LLM_*` | `harness_config` пуст; `harness_of` бросает `RuntimeNotConfiguredError` с подсказкой |
| `DARK_FACTORY_GITHUB_*` | Нет `GitHubAdapter` → `repository`/`merge_requests`/`revision_of()` — `None` |
| `ExecutionPort` (реализации пока нет, TD-022) | `agent_stage_executor()` — `None` |

Исключение — telemetry: её конфигурация **fail-closed** (`TelemetryConfig.from_env` бросает `ValueError` на неизвестный exporter или `file` без пути), потому что тихая подмена скрыла бы опечатку; адаптер присутствует всегда (в MVP — console).

`Runtime` — frozen dataclass и пассивный держатель адаптеров. Его методы — только связывание:

- `harness_of(profile, tools)` — строит harness стадии с инструментами роли. Именно здесь встречаются декларативные имена из `AgentProfile.tools` (разрешённые в callables над workspace) и адаптер, который их исполняет: `HarnessPort.run_stage` принимает конверт, а PydanticAI-адаптер берёт набор инструментов при конструировании (`role_tools`), поэтому harness собирается на стадию.
- `agent_stage_executor()` — агентный `StageExecutor` или `None`, если не хватает harness, провайдера или execution-порта.
- `revision_of()` — SCM-derived резолвер ревизии (`ScmRevision`, ADR-006 p.4).
- `aclose()` — освобождение ресурсов (HTTP-пул GitHub-адаптера, tracer provider).

## 4. Как собранное связывание попадает в рабочий путь

Ядро не импортирует `runtime`, поэтому привязка приходит **аргументом**:

```text
dark_factory.runtime (build_runtime)          dark_factory.cli / orchestration
        │                                              │
        │  executor, revision_of                       │
        └──────────────► run_advance_command ──► advance_run ──► StageExecutor
```

`advance_run` и `cli.runner.run_advance_command` имеют необязательные швы `executor` и `revision_of`; без них работает детерминированный путь (`waiting`/`blocked`) и digest снапшота как ревизия — поведение среза S1 не меняется. Кто именно вызывает `run_advance_command` с собранными швами (консольный entry point) — открытый вопрос TD-023: ре-поинт `[project.scripts]` это структурное решение.

## 5. Граничные случаи

- Пустое окружение — валидный `Runtime`: telemetry есть, остального нет, сборки исполнителя нет.
- `harness_of` без конфигурации — `RuntimeNotConfiguredError`, а не `None`-harness: запрос на несуществующее обязан быть громким.
- Сломанная telemetry-конфигурация — `ValueError` из `build_runtime`, не «telemetry отключена».
- Инструменты не собираются без workspace: `Runtime.harness_of` принимает уже разрешённые callables, а не имена.

## 6. Где искать проверки

- [`test_runtime_composition.py`](../../tests/test_runtime_composition.py) — пустая и полная конфигурация, сборка исполнителя и её отсутствие без `ExecutionPort`, привязка инструментов роли к harness, громкий отказ `harness_of`, fail-closed telemetry, `aclose`;
- [`test_import_boundaries.py`](../../tests/test_import_boundaries.py) — allowlist `runtime` в правиле A, запрет обратного ребра (правило B) и запрет соседям (`dark_factory.cli` и т.п.) импортировать адаптеры;
- [`test_orchestration_agent_stage.py`](../../tests/test_orchestration_agent_stage.py) — агентный исполнитель и инструменты, которые `runtime` связывает.

## 7. Связанные решения

- [ADR-024](../adr/ADR-024-durable-run-driver-and-composition-root.md) — durable-драйвер и composition root (п.5 — границы слоя, allowlist, запрет обратного ребра);
- [ADR-015](../adr/ADR-015-repository-boundaries.md) п.3 — правила границ импортов и versioned-контракты;
- [ADR-007](../adr/ADR-007-nine-role-catalog.md) п.3 — контракт `AgentProfile → TaskEnvelope → AgentResult` и привязка инструментов;
- [ADR-006](../adr/ADR-006-ephemeral-job-pods-reconciler-cronjob.md) п.4 — SCM-derived ревизия операции;
- [ADR-009](../adr/ADR-009-minimal-bootstrap-otel.md) — гигиена секретов и наблюдаемость.
