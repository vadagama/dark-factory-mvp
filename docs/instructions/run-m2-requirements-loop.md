# Приёмка M2: вопрос → ответ → правка → сводка → согласовать / на доработку

**Кому**: оператору фабрики.
**Что проверяем**: веху M2 плана ChangeSet (`docs/plan-changeset-workspace-mvp.md`, T078–T091): цикл обсуждения фазы «Требования» проходится из CLI и Console; правка артефакта становится коммитом в ветку изменения; после правки прежние согласования и проверки помечены неактуальными; «На доработку» тратит раунд бюджета и даёт сводку агента.
**Живой прогон**: 2026-09-19, ветка `feat/m2-conversations-documents`, продукт `prd-calc` → `vadagama/dark-factory-calc` (пустой репозиторий), задача `chg_calc_m1_001` из приёмки M1. Результаты и **блокер** — в разделе «Журнал прогона».

## Что понадобится

- Всё из `run-m1-product-intake.md` (кластер с PostgreSQL, port-forward `55432`, `.env` с `DATABASE_URL`, `DARK_FACTORY_GITHUB_*`, `DARK_FACTORY_LLM_*`, Node 22).
- **GitHub App фабрики должен быть установлен на репозиторий продукта с правами `Contents: Read & write` и `Pull requests: Read & write`.** Проверка: `Settings → Installed GitHub Apps → dark-factory-local-1 → Repository access` содержит продуктовый репозиторий. Без этого `factory product bootstrap` и `run advance` получают 403 на push — фабрика честно отказывает, ничего не записывает.
- Схема БД на `head`: `alembic upgrade head` с локальным `DATABASE_URL` (миграция `0006_conversations` добавляет `question`, `comment`, `rework_order`, `artifact_draft`, `artifact_view`).

## Окружение локального контура

```sh
set -a; . ./.env; set +a
export DATABASE_URL="$(printf '%s' "$DATABASE_URL" | sed -E 's/@[^/@]+:[0-9]+\//@127.0.0.1:55432\//')"
export DARK_FACTORY_WORKSPACE_ROOT="$HOME/Documents/GitHub/df-workspaces"
export DARK_FACTORY_WORKSPACE_MIRROR_ROOT="$HOME/Documents/GitHub/df-mirrors"
export DARK_FACTORY_PACKS_ROOT="$PWD/packs"
```

Ловушки M1 (`DARK_FACTORY_API_TOKENS` без кавычек, устаревшие скоупы токена кластера) остаются в силе — см. `run-m1-product-intake.md`.

## Шаги (CLI)

1. **Baseline в пустой репозиторий.**
   ```sh
   factory product bootstrap --id prd-calc --json
   ```
   Ожидание: один коммит `bootstrap: apply product-baseline@…` в `main`; `result.revision`, `applied_packs`. Повтор — тот же `revision` (replay). `403`/отказ push — exit 1 с фиксированным текстом: проверьте установку App (см. выше); локальное зеркало при этом держит коммит и следующий вызов допушит его.
2. **Фаза «Требования».**
   ```sh
   factory change status --id chg_calc_m1_001          # gate requirements: closed — «Артефактов фазы ещё нет»
   factory run advance --change-id chg_calc_m1_001     # агент product пишет .factory/changes/…/spec/*, открывает CR
   factory change status --id chg_calc_m1_001
   ```
   Ожидание после advance: `waiting`; в статусе — вопросы агента (`questions open: N`, с вариантами и якорями `REQ-…#AC-…`), строка гейта `closed` с причиной «Блокирующих вопросов без ответа», «Следующий шаг» → **Ответить на вопросы**.
3. **Ответ и замечание.**
   ```sh
   factory change answer --id chg_calc_m1_001 --question <q_id> --value "<вариант>"
   factory change artifacts list --id chg_calc_m1_001
   factory change artifacts show --id chg_calc_m1_001 --path <spec/requirements/REQ-001-….md>
   factory change comment --id chg_calc_m1_001 --artifact <path> --anchor AC-2 --body "Критерий не проверяем: уточнить округление"
   ```
   Ожидание: ответ вне вариантов — exit 2; после ответа гейт `available`; замечание привязано к текущей ревизии и **не** запускает доработку (гейт остаётся доступным).
4. **На доработку.**
   ```sh
   factory change rework --id chg_calc_m1_001 --phase requirements --comment <cmt_id> --question <q_id> --instruction "Сделать AC-2 проверяемым"
   factory run advance --change-id chg_calc_m1_001     # раунд 1: агент правит спеку, оставляет rework-summary
   factory change status --id chg_calc_m1_001
   ```
   Ожидание: поручение `pending` → после advance `in_progress` (раунд 1) → после следующего результата стадии `done` со сводкой «что изменил / что осталось»; замечание из сводки — `addressed` (не закрыто); ответы — `resolved`; `rework=1/3`; второе `rework` при ожидающем — exit 2.
5. **Правка из интерфейса = коммит; staleness.**
   ```sh
   factory change approve --id chg_calc_m1_001 --phase requirements --comment "ok"   # decision @ <head>
   factory change artifacts edit --id chg_calc_m1_001 --path <path> --file ./req.md --base-revision <head>
   factory change status --id chg_calc_m1_001          # approved=no; stale approvals: dec_…
   factory change artifacts versions --id chg_calc_m1_001 --path <path>
   factory change artifacts diff --id chg_calc_m1_001 --path <path> --from <head> --to <new>
   ```
   Ожидание: правка — новый коммит в `factory/chg_calc_m1_001`; прежнее согласование в гейте `state=stale`, `approved=no`; правка с устаревшей `--base-revision` при изменившемся файле — exit 1 (конфликт не сливается молча).

## Шаги (API)

```sh
factory api serve --host 127.0.0.1 --port 8010    # отдельный терминал
curl -s http://127.0.0.1:8010/api/v1/changes/chg_calc_m1_001/phase-gate?phase=requirements
curl -s http://127.0.0.1:8010/api/v1/changes/chg_calc_m1_001/questions
curl -s http://127.0.0.1:8010/api/v1/changes/chg_calc_m1_001/artifacts
```

Ожидание: `phase-gate` и `questions` совпадают с выводом `factory change status --json` (одни репозитории, одна проекция); `PUT …/artifacts/{path}` с `base_revision` = голова даёт `created_commit: true`, повтор с тем же `Idempotency-Key` — тот же `revision`.

## Шаги (Console)

```sh
cd console && VITE_API_TARGET=http://127.0.0.1:8010 npm run dev     # http://localhost:5173
```

1. Страница задачи `/changes/chg_calc_m1_001` — экран ChangeSet: верхняя панель (фаза, состояние, бюджет, блокеры), слева фазы Ф0–Ф8 с числом вопросов/замечаний и номером итерации, центр «Результат · Изменения · Проверки · История», справа контекст, внизу — одна CTA из `Guidance`. Процента готовности нет.
2. Фаза «Требования»: карточки вопросов — ответ в один клик; замечание к фрагменту (якорь из документа); «На доработку» со сводкой после раунда; `detached`-якорь показан явно; «Просмотрено» не делает согласование.
3. Редактор: режимы Документ / Markdown / Чтение не меняют содержимое; автосохранение черновика с видимым состоянием; «Сохранить в git» → коммит; конфликт (409) показан текстом сервера; история версий и diff.
4. Тот же следующий шаг, что и в `factory change status`.

## Журнал прогона (2026-09-19)

| Шаг | Результат |
|---|---|
| Hermetic-проверки ветки | Python: 2270 unit + 152 integration (scratch-БД `dark_factory_test`, миграция `0006` обратима); ruff/mypy чисто. Console — см. `console/README.md` и отчёт в MR |
| `alembic upgrade head` на боевой БД кластера | `0005_products → 0006_conversations` |
| `factory product validate --id prd-calc` | `ready`, `validation.state=empty`, `default_branch=main` — репозиторий читается (публичный клон) |
| `factory product bootstrap --id prd-calc` | **exit 1** — push baseline отказан: `Permission to vadagama/dark-factory-calc.git denied to dark-factory-local-1[bot]` (403). Диагностика read-only через `GET /installation/repositories`: установка App покрывает `dark-factory-mvp`, `dark-factory-runs`, `dark-factory-gitops`, `dark-factory-product-1`, **но не `dark-factory-calc`**. Локальное зеркало держит неотправленный коммит `bootstrap: apply product-baseline@0.1.0`; после добавления репозитория в установку App повтор команды допушит его без ручной чистки |
| Шаги 2–5 CLI, API, Console на живом контуре | **не выполнены** — заблокированы предыдущим шагом. Цикл целиком покрыт интеграционными тестами на реальных коммитах фейкового репозитория (`tests/integration/test_api_conversations.py`, `tests/integration/test_cli_changes.py`) и e2e-сценариями Console на фикстурах |

**Как снять блокер и продолжить**: в GitHub добавить `vadagama/dark-factory-calc` в repository access установки `dark-factory-local-1` (права App: Contents и Pull requests — Read & write), затем повторить с шага 1 «Baseline в пустой репозиторий». T091 закрывается после прохождения шагов 2–5 из CLI и Console и обновления этого журнала.

## Что осталось за рамками M2

- Разделение архитектуры и интерфейса внутри `specification` (гейт `solution`) — M3 (T098); в M2 «Согласовать требования» — единственный гейт фазы `specification`.
- Запуск `run advance` из Console — M4 (T103); в M2 Console показывает CLI-команду из `Guidance`.
- Inbox «Требует внимания» — T116.
