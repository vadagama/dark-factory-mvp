# Quickstart Validation: Dark Factory MVP (Phase 1)

**Feature**: `001-dark-factory-mvp` | **Date**: 2026-09-13 | **Plan**: [`plan.md`](./plan.md) | **Spec**: [`spec.md`](./spec.md)

Руководство по проверке, что фича работает end-to-end. Детали контрактов — [`contracts/`](./contracts/), модель — [`data-model.md`](./data-model.md); здесь — воспроизводимые сценарии и ожидаемый результат. Реализация шагов — в задачах `specs/001-dark-factory-mvp/tasks.md`, не здесь.

## Предпосылки

- macOS (Apple Silicon), Python 3.12, `uv`; Docker Desktop с Kubernetes (для этапов 4) — 10–12 ГБ под Docker (ADR-010).
- Фабрика запускается как CLI локально и как CI-job GitHub Actions (ADR-006, ADR-019).
- Секреты — только через env/Kubernetes Secret; в git и командах их нет.

## 1. Базовые гейты

```bash
uv sync --all-groups
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -q
```

**Ожидаемо**: все команды зелёные. `pytest` включает `tests/test_import_boundaries.py` — правило A (ядро не импортирует адаптеры) и B (адаптеры импортируют только порты) — границы ADR-015 §3.

Проверка доменных контрактов (T-003) отдельно:

```bash
uv run pytest tests/test_changes_models.py tests/test_changes_next_action.py tests/test_changes_serialization.py -q
```

**Ожидаемо**: round-trip JSON/YAML сохраняет `Decimal`/`datetime`; переход вне таблицы поднимает `InvalidStatusTransition`; `StageResult` неизменяем (frozen); закрытое объединение `NextAction` из 8 вариантов.

## 2. Walking skeleton: одна стадия без LLM (US1)

Предпосылки: T-004 (Flow/CLI), T-005 (порты + фейки). Вход — снапшот изменения.

```bash
uv run factory doctor --json
uv run factory stage run --change ./fixtures/chg_smoke.yaml --stage construction --json
```

**Ожидаемо**:
- `doctor` подтверждает окружение; секретов в выводе нет.
- stdout — валидный `StageResult` (`schema_version: 1`) со `status` и `next_action` одного из 8 типов; запись запуска сохранена.
- С LLM-эндпоинтом, выключенным, детерминированные шаги стадии всё равно исполняются и публикуют результат (US1 scenario 3).

Повтор с той же `operation_key`:

```bash
uv run factory stage run --change ./fixtures/chg_smoke.yaml --stage construction --json
```

**Ожидаемо**: второй внешний эффект не создаётся (effect ledger; `status: succeeded/unknown` + `external_ref`) — FR-017.

## 3. Intake → согласованная спецификация (US2)

Предпосылки: T-011 (Product), T-012 (context), T-020/T-021 (SDD, spec-gate).

1. Подать задачу из CLI/Console/трекера → создать Change (FR-001).
2. Product публикует MR с ChangeSet'ом; `.factory/changes/<year>/<id>/` содержит артефакты контракта [`changeset.md`](./contracts/changeset.md).
3. До согласования реализация заблокирована гейтом; согласование связано с ревизией (SHA) спецификации (FR-003).

**Ожидаемо**: при отсутствии approval стадия `construction` не стартует; после смены ревизии спецификации прежнее approval не действует.

## 4. Реализация → review → rework → merge (US3)

Предпосылки: T-010 (harness), T-013/T-014 (Quality, rework), T-030/T-031/T-032 (GitHub, CI jobs, merge policy).

1. На согласованной ревизии запустить реализацию → commit + MR с ссылками на спецификацию и evidence.
2. Независимое review (отдельный контекст) → формализованные `Finding` (id, origin, severity, file/line, reviewed SHA, action, status).
3. Блокеры → rework в тот же MR; новый SHA устаревает прежние проверки, обязательные проверки повторяются.
4. Исчерпание лимита раундов (≤3) или бюджета → попытка `Blocked` с причиной и evidence.

**Ожидаемо**: машинные проверки (стиль, типы, тесты) зелёные **на итоговом SHA**; резюме агента их не заменяет (SC-004). Параллельно — не более двух агентов (US3 scenario 6).

## 5. Reconcile, идемпотентность, восстановление (US4, SC-005)

```bash
uv run factory reconcile --json
uv run factory outbox dispatch --once
```

**Ожидаемо**: повторный reconcile не запускает вторую попытку для уже обработанного события; повторный webhook не создаёт дубликат MR (поиск по `change_id`); «зависший» job подхватывается с учётом lease/fencing.

Проверка crash-инвариантов (T-006): миграции применяются и откатываются на чистой БД; `crash-before-call` и `crash-after-call-before-commit` не создают второй идентичный внешний эффект.

## 6. Релиз в dev через GitOps (US5)

Предпосылки: T-040…T-045. После ручного merge:

1. Post-merge pipeline собирает образ с immutable digest и готовит GitOps-MR.
2. Argo CD применяет версию в dev.
3. Smoke-проверка проходит → задача «выпущено»; провал → статуса «выпущено» нет, есть диагностика и решение человека.

**Ожидаемо**: 100% изменений со статусом «выпущено» имеют успешный smoke на зафиксированном digest; rollback-концепция — revert GitOps-коммита (схема БД — expand/contract).

## 7. Контекст, трекер, Console (US6)

1. Собрать `ContextBundle` из закреплённых источников → зафиксированы версии/ревизии и provenance (FR-021).
2. Синхронизировать статусы с Plane → без дублирования задач; недоступность трекера не останавливает конвейер (FR-020).
3. Открыть Console: стадии, вопросы/блокеры, стоимость, гейты, ссылки на MR/проверки/evidence; потеря локального кэша Console не влияет на процесс (SC-008).

## 8. Сквозной пилот (SC-001, SC-002, SC-007)

Прогнать ≥10 задач через фабрику (T-072). Для каждой инициативы проверить:

- **SC-001**: цикл «задача → dev» пройден; ручные действия — только на определённых гейтах.
- **SC-002**: типовое изменение (diff ≤10 файлов и ≤200 строк, без эскалации ADR-018) — от согласованной спецификации до принятого MR ≤ 1 рабочего дня.
- **SC-003**: отчёт с измеримыми метриками (стоимость с учётом попыток, раунды, время, ручные вмешательства).
- **SC-007**: трассировка «задача → спецификация (SHA) → план → код (SHA) → проверки → релиз» восстанавливается.

## Границы проверки

- Production-релиз, auto-merge R0/R1, GitLab-адаптер — вне этого прогона (T-034, T-085, T-091).
- Локальный результат не авторитетен для merge сам по себе; авторитетность даёт CI-прогон того же релиза ядра (FR-022).

## Дальше

Разделы 1–2 проверяются локально без БД и LLM; разделы 5–7 требуют окружения (PostgreSQL, GitHub-контур, локальный кластер — `deploy/bootstrap/`). Оставшиеся работы — e2e-пилот (T043) и финальная приёмка (T056): статусы задач — в `specs/001-dark-factory-mvp/tasks.md`.
