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
factory run advance  --change-id <id> | --run-id <id> [--json]  # одна стадия запуска (T-092)
                     [--contract-json <path|->] [--approve-contract]  # контракт (T-016)
                     [--expected-digest <digest> | --digest-json <path>]  # релиз (T-092 S4)
                     [--observed-digest <d>] [--argo-sync <s>] [--argo-health <h>]
                     [--smoke-url <url>] [--smoke-digest-url <url>] [--smoke-digest-header <h>]
                     [--application <ns/name>] [--runs-root <dir>]
factory run publish  --record <path> [--runs-root <dir>] [--json]  # публикация run-записи (T-061)
factory run withdraw --run-id <id> [--reason <text>] [--json]  # операторское снятие паркованного run (T064)
factory product add  --id <id> --name <name> --provider github|gitlab --repository <owner/name>
                     [--description <text>] [--repository-url <url>] [--baseline-ref <ref>]
                     [--dev-env-ref <ref>] [--json]  # регистрация продукта (T070, ADR-030)
factory product validate --id <id> [--json]   # наблюдение репозитория и запись готовности (ADR-031)
factory product list     [--limit <1..200>] [--offset <n>] [--json]
factory product show     --id <id> [--json]
factory change create    --product <id> --title <t> --limit-usd <amount>
                         [--problem <p> --goal <g> [--constraint <c>]... [--out-of-scope <o>]... | --brief-json <path|->]
                         [--scenario specs_only|full] [--token-limit <n>] [--risk-class R0..R4]
                         [--description <text>] [--id <chg_id>] [--json]   # intake задачи (T073, T071)
factory change status    --id <chg_id> [--json]    # задача, последний run и «Следующий шаг» (Guidance, T074)
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

`factory run publish` (T-061, ADR-015 §4) не исполняет стадию и не производит `StageResult`: он публикует уже сохранённый `run_record.json` в checkout репозитория `dark-factory-runs` (`--runs-root` или переменная `DARK_FACTORY_RUNS_ROOT`). Коды выхода: `0` — запись создана или уже была опубликована без изменений; `1` — запись отклонена (секрет, превышение размера, неполная цепочка evidence, конфликт immutability); `2` — неверный ввод/конфигурация (нечитаемая запись, несоответствие схеме, не задан runs-root), при этом ничего не записано.

`factory run advance` (T-092, ADR-024) исполняет **ровно одну** стадию запуска и завершается, не удерживая процесс: требуется ровно один из `--change-id` / `--run-id`; с `--change-id` запуск создаётся при отсутствии (идемпотентно по снапшоту изменения) и продолжается существующий при наличии. Решение применяет `flow.apply_result`, а драйвер пишет его целиком в одной транзакции (`stage_result`, статусы `stage`/`attempt`/`run`, строки созданных решением стадий, событие outbox). Повтор того же логического действия **инертен** — результат возвращается как `replayed` и не пишется ничего, включая строку lease; committed `failed`/`blocked` требует новой попытки (ADR-006 §7, срез S2). Коды выхода — общая таблица выше (`waiting` — не ошибка, результат персистентен до ожидания). Ограничения среза S1: входная ревизия стадии выводится из снапшота изменения, а не из commit SHA продукта (цель ADR-006 §4 — срез S2); статус run пишется через `ExecutionRepository.update_status`, который проверяет `state_revision` и `fencing_token`, но не таблицу `RUN_STATUS_TRANSITIONS`.

**Release-опции (T-092 S4, ADR-024 §7)** — все необязательны, без них поведение прежнее:

- `--expected-digest` / `--digest-json` (ровно один источник, иначе exit 2) — ожидаемый immutable digest: свежая release-стадия промоутит его в GitOps-репозиторий (блок `DARK_FACTORY_GITOPS_*`, все переменные обязательны при задании любой); без digest свежая попытка честно `blocked` до внешних эффектов.
- `--observed-digest`, `--argo-sync`, `--argo-health` — наблюдаемое состояние развёртывания; при задании любого из них waiting-чекпоинт release-стадии разрешается release-фактами той же попытки (digest → Argo → smoke, порядок фиксирован, недостающие данные — fail-closed).
- `--smoke-url`, `--smoke-digest-url`, `--smoke-digest-header` — smoke-пробы (FR-013); запускаются только когда проверки digest/Argo прошли (FR-011: неверный digest не стреляет пробами); без smoke-опций — `smoke was not run`, релиз не проходит.
- `--application` — `namespace/name` Argo Application, попадает в release evidence.
- `--runs-root` (или `DARK_FACTORY_RUNS_ROOT`) — после терминального advance (`completed`/`failed`) run-запись публикуется в checkout `dark-factory-runs` идемпотентно и best-effort: сбой — предупреждение на stderr, код выхода не меняется; корень не задан — публикация пропускается.

**Contract-опции (T-016, ADR-018 p.3)** — все необязательны, без них поведение прежнее:

- `--contract-json <path|->` — Implementation Contract запуска (T-016): читается (`-` — stdin), валидируется pydantic-схемой и прикрепляется к резолвнутому запуску **в той же транзакции**, что и решение стадии. Идемпотентно и без подмены: запуск без контракта записывает переданный; идентичный — no-op; **другой** контракт — отказ store (`ContractConflictError`) → exit 2, ничего не записано (утверждённая граница работающего изменения не подменяется, ADR-018 p.3). Битый файл/JSON/схема — exit 2 до обращения к store.
- `--approve-contract` — проставляет human-утверждение (`approved_by=Role.PRODUCT`, `decided_at=now(UTC)`) на загруженный контракт (решение оператора, ADR-011); флаг без `--contract-json` — exit 2; контракт, уже несущий approval, вместе с флагом — двусмысленность, exit 2. Утверждённый контракт снимает блокировку входа в construction (T-016); неутверждённый прикрепляется и честно блокирует вход (`blocked`).

`factory product *` (T070, ADR-030/ADR-031) — операторская сторона реестра продуктов; та же модель и то же правило готовности, что у `/products` (T066), поэтому CLI и Console показывают один статус. `add` идемпотентен по `--id` (повтор — replay, ничего не пишется) и оставляет audit-запись `product.add`; `validate` наблюдает репозиторий через `RepositoryProvisioningPort`, который связывает composition root, и записывает `validating → ready | error` в одной транзакции. Коды выхода: `0` — `add` зарегистрировал или replay'нул, `validate` дал `ready`, `list`/`show` напечатали; `1` — `validate`: репозиторий недоступен (`error` записан с причиной) или само наблюдение упало (ничего не записано); `2` — неверный ввод, неизвестный продукт, порт провижининга не сконфигурирован (статус не меняется), недоступный store. Секреты не эхоятся: тексты исключений адаптера и URL store в вывод не попадают (ADR-009).

`factory change *` (T073, T071, T074, ADR-033) — intake задачи для зарегистрированного продукта: репозиторий берётся из реестра, бриф — из флагов или JSON той же формы, что ответ `POST /briefs/formulate`; бриф без проблемы/цели сохраняется черновиком. Лимит — деньги в USD (копируется в `BudgetSnapshot.cost_budget` run). Обе команды завершаются блоком «Следующий шаг» — рендером `Guidance`, вычисленного ядром и отдаваемого `GET /changes/{id}/guidance`: CLI и Console показывают один и тот же шаг по построению. Коды выхода: `0` — создано/replay/показано; `2` — неверный ввод (лимит, бриф), неизвестный продукт или задача, недоступный store.

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
