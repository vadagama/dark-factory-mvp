# Contract: Factory Runner CLI

**Feature**: `001-dark-factory-mvp` | **Date**: 2026-09-13 | **Plan**: [`../plan.md`](../plan.md)

Factory Runner — точка входа стадии фабрики (ADR-006 §1). Один и тот же релиз ядра запускается локально и в CI (FR-022); локальный результат сам по себе не авторитетен для merge. Агенту не нужен постоянно живущий сервис.

## Команды

```text
factory stage run    --change <path|ref> --stage <stage> [--route quick|standard]
                     [--input-revision <rev>] [--run-id <id>] [--json]
                     [--evidence-dir <dir>] [--non-interactive]
factory stage resume --run-id <id> --next-action <wa|ci|input> [--json]
factory run status   --run-id <id> [--json]
factory reconcile    [--json]           # один идемпотентный проход Reconciler
factory outbox dispatch [--once]        # доставка событий (ADR-016)
factory doctor       [--json]           # проверка окружения и конфигурации
```

- `--stage` принимает значение `Stage`: `specification | planning | construction | review_verification | release`.
- `--route` — `quick | standard` (ADR-005). При `quick` пропускаются UI/расширенные гейты, human-гейты сохраняются по ADR-018.
- `--input-revision` — входная ревизия стадии; задаёт `operation_key` (ADR-006 §3). Отсутствие — вычисляется из снапшота входа.
- Провайдер определяется репозиторием, не флагом; один run — один провайдер (ADR-019 §5).

## Вход и выход

**Вход**: снапшот изменения (идентификатор изменения, цель, scope, ограничения, repo) + входная ревизия. Снапшот фиксируется до любой агентной работы (FR-001).

**Выход (stdout, JSON при `--json`)**: сериализованный `StageResult` + запись запуска.

```json
{
  "schema_version": 1,
  "stage": "construction",
  "run_id": "run_01H...",
  "change_id": "chg_01H...",
  "attempt_number": 1,
  "input_revision": "a1b2c3d",
  "status": "succeeded",
  "next_action": { "type": "wait_for_ci", "reason": "...", "change_request": { "...": "..." } },
  "artifacts": [],
  "evidence": [ { "id": "ev_tests", "type": "test_results", "uri": "...", "required": true, "available": true } ],
  "gate_results": [ { "gate": "code", "status": "passed", "sha": "a1b2c3d" } ],
  "findings": [],
  "usage": { "prompt_tokens": 0, "completion_tokens": 0, "cost": "0.00" },
  "produced_at": "2026-09-13T10:00:00Z"
}
```

Контракт `StageResult` — [`../data-model.md`](../data-model.md) §1.3; закрытое объединение `NextAction` — §1.4.

## Exit codes

| Код | Значение | `StageResult.status` |
|---|---|---|
| 0 | стадия завершена успешно | `succeeded` |
| 10 | внешнее ожидание, результат сохранён | `waiting` |
| 20 | стадия остановлена лимитом/гейтом, нужно решение человека | `blocked` |
| 1 | исполнение завершилось с ошибкой | `failed` |
| 2 | невалидный вход/конфигурация (стадия не запускалась) | — |

`waiting` — не ошибка: результат персистится **до** внешнего ожидания (ADR-006 §8), job завершается, продолжение — новым запуском.

## Поведение и инварианты

- **Идемпотентность**: повтор `stage run` с той же `operation_key` не создаёт второй внешний эффект; перед созданием выполняется `find_existing` (FR-017).
- **Лимиты**: `rework` ограничен `BudgetSnapshot.max_rework_rounds` (default 3); исчерпание → `stop` со `outcome=blocked` + evidence (FR-008, SC-006). Повторный запуск не обнуляет расход (FR-016).
- **Гейты**: обязательные машинные проверки (стиль, типы, модульные тесты) исполняются на итоговом SHA; резюме агента их не заменяет (FR-009, SC-004).
- **Секреты**: только из env/Kubernetes Secret; в командах и логах не публикуются (ADR-009).

## Локальный запуск и CI

```bash
uv run factory stage run --change ./changes/chg_01H.yaml --stage construction --json
```

Тот же вход в CI-джобе даёт эквивалентный результат (US1 scenario 2). `--non-interactive` запрещает интерактивные запросы в CI; внешние ожидания выражаются `wait_for_*`, а не блокировкой раннера.
