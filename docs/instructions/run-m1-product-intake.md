# Приёмка M1: продукт → задача с брифом → один «следующий шаг» в CLI и Console

**Кому**: оператору фабрики.
**Что проверяем**: веху M1 плана ChangeSet (`docs/plan-changeset-workspace-mvp.md`, T065–T077): на **новом чистом репозитории** калькулятора из интерфейса создаётся продукт со статусом `ready` (с evidence провижининга) и задача с брифом, сценарием и лимитом; на каждом шаге CLI и Console показывают **один и тот же** следующий шаг (`Guidance`, ADR-033).
**Живой прогон**: 2026-09-19, ветка `feat/m1-changeset-intake`, репозиторий `vadagama/dark-factory-calc` (пустой, unborn HEAD), контур — кластер Docker Desktop (`factory-postgres`), GitHub App и LLM из `.env`. Результаты — в разделе «Журнал прогона».

## Что понадобится

- Клон `dark-factory-mvp`, Python-окружение (`.venv`), Node 22 для Console.
- Кластер с PostgreSQL фабрики (`docs/instructions/docker-desktop-cluster-containers.md`) и port-forward к нему: `kubectl port-forward -n factory svc/factory-postgres 55432:5432`.
- `.env` в корне репозитория: `DATABASE_URL`, `DARK_FACTORY_GITHUB_APP_ID`, `DARK_FACTORY_GITHUB_APP_PRIVATE_KEY`, `DARK_FACTORY_GITHUB_INSTALLATION_ID`, `DARK_FACTORY_LLM_*`. GitHub App должен быть установлен на продуктовый репозиторий.
- Пустой репозиторий продукта на провайдере (для прогона — `vadagama/dark-factory-calc`).

## Окружение локального контура

```sh
set -a; . ./.env; set +a
export DATABASE_URL="$(printf '%s' "$DATABASE_URL" | sed -E 's/@[^/@]+:[0-9]+\//@127.0.0.1:55432\//')"
export DARK_FACTORY_WORKSPACE_ROOT="$HOME/Documents/GitHub/df-workspaces"
export DARK_FACTORY_WORKSPACE_MIRROR_ROOT="$HOME/Documents/GitHub/df-mirrors"   # → ProviderClone (клон на installation-токене)
export DARK_FACTORY_PACKS_ROOT="$PWD/packs"                                        # паки baseline (T069)
```

Две ловушки, пойманные прогоном:

- Значение `DARK_FACTORY_API_TOKENS` в `.env` — JSON без кавычек снаружи; `. ./.env` съедает внутренние кавычки, и `factory api serve` падает на разборе. Берите токены из Secret кластера (`kubectl get secret factory-api-tokens -n factory -o jsonpath='{.data.DARK_FACTORY_API_TOKENS}' | base64 -d`) или заключите значение в одинарные кавычки.
- Токен оператора кластера выдан до T064/T066 и не имеет скоупов `products:write`/`runs:write`. Для локального API добавьте в JSON токенов запись оператора с полным набором скоупов (`changes:write approvals:write ci:write runs:write products:write`); Secret кластера при этом не трогается.

Схема БД должна быть на `head` (миграция `0005_products`): `alembic upgrade head` с тем же `DATABASE_URL`.

## Шаги (CLI)

1. **Продукт.**
   ```sh
   factory product add --id prd-calc --name "Calculator" --provider github \
     --repository vadagama/dark-factory-calc --repository-url https://github.com/vadagama/dark-factory-calc \
     --baseline-ref .factory/product --json
   factory product validate --id prd-calc --json
   ```
   Ожидание: `validate` наблюдает репозиторий через `ProviderClone` (клон на installation-токене) и записывает `created → validating → ready`; evidence — `validation.state` (`empty` для чистого репозитория, `default_branch`, `head_revision`), `state_revision` растёт на 2. `UNAVAILABLE` → статус `error` с причиной и exit 1: проверьте установку App на репозиторий.
2. **Задача.**
   ```sh
   factory change create --product prd-calc --title "Percent button for the calculator" \
     --problem "…" --goal "…" --constraint "…" --out-of-scope "…" \
     --scenario specs_only --limit-usd 20 --id chg_calc_m1_001 --json
   factory change status --id chg_calc_m1_001
   ```
   Ожидание: задача создана с брифом `complete`, сценарием и лимитом; обе команды заканчиваются блоком «Следующий шаг» с ровно одним primary-действием («Запустить фазу «Требования»» — `factory run advance --change-id …`).
3. **Один и тот же шаг в API.**
   ```sh
   factory api serve --host 127.0.0.1 --port 8010    # отдельный терминал
   curl -s http://127.0.0.1:8010/api/v1/changes/chg_calc_m1_001/guidance
   curl -s http://127.0.0.1:8010/api/v1/products/prd-calc/guidance
   ```
   Ожидание: JSON `guidance` из `factory change status --json` **равен** ответу `GET /changes/{id}/guidance` — это один объект, вычисленный ядром (`orchestration/state/guidance.py`).
4. **Агент «Помоги сформулировать».**
   ```sh
   curl -s -X POST http://127.0.0.1:8010/api/v1/briefs/formulate \
     -H "Authorization: Bearer $OPERATOR_TOKEN" -H "Content-Type: application/json" \
     -d '{"source_text": "Хочу, чтобы калькулятор умел считать проценты …"}'
   ```
   Ожидание: `status: complete`, четыре поля заполнены, `formulated_by: agent`, исходный текст сохранён. Без `DARK_FACTORY_LLM_*` — тот же 200, но `status: draft` и `error` с причиной (форма не ломается).

## Шаги (Console)

```sh
cd console && VITE_API_TARGET=http://127.0.0.1:8010 npm run dev     # http://localhost:5173
```

1. «Служебное → Настройки»: ввести токен оператора.
2. «Продукты»: список содержит `prd-calc` со статусом `ready`; страница продукта показывает блок «Следующий шаг» с тем же `headline`/`primary`, что и `GET /products/prd-calc/guidance`.
3. «Новая фича»: форма intake — свободный текст → «Помоги сформулировать» заполняет проблему/цель/ограничения/вне скоупа (или показывает «Бриф остался черновиком: <причина>»), сценарий, лимит и честный прогноз («появится после первого прогона»), «Создать задачу».
4. Страница задачи: блок «Следующий шаг» совпадает с выводом `factory change status --id <id>`.

## Журнал прогона (2026-09-19)

| Шаг | Результат |
|---|---|
| `alembic upgrade head` на боевой БД кластера | `0004_runner_state → 0005_products` (таблица `product`, `change.product_id`) |
| `factory product add --id prd-calc …` | `outcome=created`, статус `created`, `state_revision=1` |
| `factory product validate --id prd-calc` | exit 0; `validation.state=empty`, `default_branch=main`, `head_revision=null`; статус `ready`, `state_revision=3` — репозиторий наблюдён клоном на installation-токене GitHub App |
| `factory change create … --scenario specs_only --limit-usd 20 --id chg_calc_m1_001` | `outcome=created`, бриф `complete` (оператор), лимит `20 USD`; «Следующий шаг»: `Задача готова к фазе «Требования»` → `factory run advance --change-id chg_calc_m1_001` |
| `factory change status --id chg_calc_m1_001 --json` vs `GET /api/v1/changes/chg_calc_m1_001/guidance` | **равны** (сравнение JSON-объектов) |
| `GET /api/v1/products/prd-calc/guidance` | `Продукт готов к работе` → «Новая фича»; «задач у продукта: 1» |
| `POST /api/v1/briefs/formulate` (реальная LLM из `.env`) | HTTP 200 за 3,5 с; `status=complete`, `formulated_by=agent`, ограничения и «вне скоупа» распознаны, исходный текст сохранён |
| Console (`npm run dev`, `VITE_API_TARGET=http://127.0.0.1:8010`, токен оператора в localStorage) | «Продукты»: `prd-calc` со статусом `ready`; страница продукта — «Следующий шаг: Продукт готов к работе» → «Новая фича» (тот же `headline`/`primary`, что `GET /products/prd-calc/guidance`) |
| Console → «Новая фича» → «Помоги сформулировать» (реальная LLM) | четыре поля заполнены агентом (`Бриф: complete · сформулировал: агент`), сценарий «Только спецификации», лимит 15 USD, прогноз честно «появится после первого прогона» → «Создать задачу» → `chg_ffa6c0cdf464` (`source=console`, бриф `complete`/`agent`, `source_text` сохранён) |
| Страница задачи `chg_ffa6c0cdf464` vs `factory change status --id chg_ffa6c0cdf464` | один и тот же следующий шаг: «Задача готова к фазе «Требования»» · «Бриф сформулирован агентом; сценарий specs-only …; лимит 15 USD» · primary «Запустить фазу «Требования»» (в Console — CLI-команда `factory run advance --change-id …`, API-эквивалента до M4 нет) · также «Изменить бриф» · после «Агент подготовит дельту требований и вопросы…» |
| Заглушки и служебная область | `/attention` — честный empty state «появится в M5 (T116)»; `/budgets` → редирект `/service/budgets` |

Скриншоты прогона (продукты, страница продукта, intake после «Помоги сформулировать», страница задачи) сняты Playwright-скриптом, который читает токен из файла сам и в репозиторий не входят.

## Что осталось за рамками M1

- Запуск фазы «Требования» из Console — `factory run advance` (управление исполнением из API — M4, T103).
- Inbox «Требует внимания» — T116; лента активности — вне объёма MVP.
- Bootstrap baseline (`.factory/`) в пустой репозиторий выполняется адаптером (T069), но не имеет операторской команды/кнопки — появится вместе с фазой «Требования» (M2).
