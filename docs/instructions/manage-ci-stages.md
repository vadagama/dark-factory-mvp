# Как управлять этапами CI: включить и выключить стадию (T058, ADR-026)

Пошаговая инструкция для человека: как на время разработки выключить отдельные этапы проверок в `.github/workflows/ci.yml`, чтобы не ждать тяжёлых сборок на каждом MR, и как вернуть полный набор обратно. В конце — описание каждого этапа: что он проверяет, чем выключается и как прогнать то же самое локально.

Правила и обоснование решения — в [ADR-026](../adr/ADR-026-parameterizable-ci-stages.md).

## Простыми словами: что мы сейчас сделаем

У пайплайна CI двадцать этапов-проверок (job'ов). У каждого этапа есть выключатель — **переменная репозитория** `CI_SKIP_<ИМЯ_ЭТАПА>`. Переключатель меняется из терминала или веб-интерфейса GitHub, без правок кода: поставили `true` — этап пропускается, удалили переменную — этап снова работает. По умолчанию (переменных нет) включены **все** этапы, поэтому «потерять» проверку случайно нельзя: выключение всегда явное.

Типовой сценарий, ради которого это сделано: идёт итерация по Python-коду, а прогон каждый раз тратит минуты на multi-arch сборку образов и Storybook. Выключаем пять тяжёлых этапов, быстро гоняем остальные, перед merge возвращаем всё назад.

## Что понадобится

- **Права администратора репозитория**: переменные Actions меняет владелец/админ (`Settings` репозитория).
- **`gh` CLI** (необязательно — то же самое есть в веб-UI): `gh auth status` должен показывать вход в аккаунт с доступом к репозиторию.
- Понимание, **что** выключать: список всех этапов и их переменных — в разделе «Этапы поимённо».

## Быстрый старт

### 1. Посмотреть текущее состояние

```sh
gh variable list
```

Пусто — выключенных этапов нет, работает полный набор. Иначе — список ваших переменных `CI_SKIP_*`.

### 2. Выключить тяжёлые этапы (пресет «быстрая итерация»)

```sh
gh variable set CI_SKIP_FACTORY_IMAGE   --body true
gh variable set CI_SKIP_CONSOLE_IMAGE   --body true
gh variable set CI_SKIP_UIKIT_STORYBOOK --body true
gh variable set CI_SKIP_UIKIT_VISUAL    --body true
gh variable set CI_SKIP_CONSOLE_E2E     --body true
```

Это пять самых долгих этапов: multi-arch сборки образов со сканами, сборка Storybook, визуальная регрессия и e2e с установкой браузера. Остальные пятнадцать продолжат проверять код.

### 3. Выключить один конкретный этап

Имя переменной — `CI_SKIP_` плюс имя этапа заглавными буквами, где дефисы заменены на подчёркивания:

```sh
gh variable set CI_SKIP_UIKIT_VISUAL --body true
```

### 4. Включить этап обратно

```sh
gh variable delete CI_SKIP_UIKIT_VISUAL
```

### 5. Вернуть полный набор (обязательно перед merge)

```sh
gh variable list --json name --jq '.[].name' | grep '^CI_SKIP_' | while read -r name; do gh variable delete "$name"; done
```

### То же самое в веб-интерфейсе

`Settings` → `Secrets and variables` → `Actions` → вкладка `Variables` → `New repository variable` / `Edit` / `Delete`. Имя — из таблиц этапов ниже, значение — `true`.

### То же самое в консоли фабрики

В консоли есть экран **«Этапы CI»** (маршрут `/ci`): те же двадцать этапов с выключателями, описанием, весом и локальной командой ([ADR-027](../adr/ADR-027-console-ci-stage-toggles.md)).

- Экран работает, когда API сконфигурирован GitHub App с правом **Variables: read/write** (в chart — `ciToggles.existingSecret` и `ciToggles.repositorySlug`); иначе он честно показывает «не сконфигурировано» и выключает контролы (fail-closed) — никакого фиктивного выключателя.
- Переключение требует operator-токена со scope `ci:write`: токен вводится на экране «Настройки» (если у вашего токена уже есть этот scope, он подойдёт).
- Кнопка «Включить все этапы» возвращает полный набор одной операцией — это самый быстрый способ выполнить правило 2 из раздела выше.
- Экран не отменяет ничего из «Правил безопасности»: merge — по зелёным гейтам ([ADR-011](../adr/ADR-011-risk-based-merge-release-policy.md)).

### Чтобы изменение подействовало

Переменные читаются при запуске прогона, поэтому нужен новый прогон: новый коммит (push) или `Re-run all jobs` (`gh run rerun <run-id>`). Уже запущенные этапы задним числом не отменяются.

## Как это работает

- **Имя**: `CI_SKIP_<JOB>`, где `<JOB>` — идентификатор этапа в `ci.yml` (`factory-us1-parity` → `CI_SKIP_FACTORY_US1_PARITY`).
- **Значение `true`** — этап пропускается. GitHub не различает регистр при сравнении строк в выражениях, поэтому `true`, `True` и `TRUE` работают одинаково (консоль и `gh`-команды ниже пишут строчными).
- **Любое другое значение** (пусто, `false`, `yes`, `1`, опечатка) — этап выполняется. Это сделано намеренно (fail-safe): опечатка или забытая переменная не может тихо убрать проверку из пайплайна.
- **Пробелы значимы**: значение ` true` (с пробелом) не равно `true`, и этап останется включённым.
- **Нет переменной — этап включён.** Выключение всегда явное.
- **Изоляция**: выключение одного этапа не мешает остальным. Исключение — две сборки образов (`factory-image`, `console-image`): они запускаются после включённых гейтов и не запускаются, если включённый гейт упал или был отменён. Намеренно пропущенные гейты сборку не блокируют — иначе выключение, например, `security` глушило бы и сборку образа.
- **Где смотреть**: пропущенный этап виден в прогоне Actions серым как `Skipped` (`gh run view <run-id>`).
- **Область действия**: переменные репозитория общие для всего репозитория и действуют на все прогоны (`pull_request` и push в `main`), а не на отдельный MR.
- **Это не секреты**: значения видны читающим репозиторий; ничего чувствительного в них не храним.

## Правила безопасности: прочитать до первого выключения

1. **Переключатель — инструмент разработки, а не способ получить зелёный CI.** Выключать падающий гейт, чтобы «покрасить» прогон, запрещено: сначала чинится причина (скилл `ci-cd`, [ADR-011](../adr/ADR-011-risk-based-merge-release-policy.md)).
2. **Перед merge полный набор возвращается.** Merge делает человек и только по зелёным гейтам (ADR-011).
3. **Branch protection.** Если включены обязательные проверки (required status checks), пропущенный чек не считается пройденным и PR может стать не мёрджабельным. Держите список обязательных проверок и выключенные этапы согласованными.
4. **Не выключайте то, что проверяет именно ваше изменение**: правка UIKit без `uikit-storybook`/`uikit-visual`, изменение зависимостей без `security`, упаковка без `build`, код без `test`, Python без `lint`/`typecheck` — это рецепт унести поломку в `main`.
5. **Выключенные этапы не проверяются и на push в `main`** — переменные не различают ветки.

## Этапы поимённо

Порядок — как в `ci.yml`; вес — порядок длительности (точные времена видны в прогоне Actions).

### Python-гейты ядра

| Этап (job) | Переменная | Что проверяет | Вес | Локально |
|---|---|---|---|---|
| `lint` | `CI_SKIP_LINT` | `ruff check .` и `ruff format --check .`: стиль, импорты, форматирование | секунды | `uv run ruff check . && uv run ruff format --check .` |
| `typecheck` | `CI_SKIP_TYPECHECK` | `mypy .` (strict) по всему Python-коду | десятки секунд | `uv run mypy .` |
| `test` | `CI_SKIP_TEST` | `pytest` с сервисом PostgreSQL 16 (порт 55432), чтобы интеграционные тесты не скипались | минуты | `uv run pytest` (см. примечание про БД) |
| `build` | `CI_SKIP_BUILD` | `uv build`: sdist и wheel собираются, упаковка не сломана | секунды | `uv build` |
| `security` | `CI_SKIP_SECURITY` | `pip-audit` по зависимостям из `uv.lock` — известные уязвимости (SCA) | десятки секунд | см. примечание |

### Factory-гейты (dogfooding)

| Этап (job) | Переменная | Что проверяет | Вес | Локально |
|---|---|---|---|---|
| `factory-us1-parity` | `CI_SKIP_FACTORY_US1_PARITY` | `factory doctor` (0 ошибок) и `factory stage run` по фикстуре `fixtures/chg_smoke.yaml`, стадия `construction` — walking skeleton US1 без LLM | десятки секунд | `uv run factory doctor --json` и `uv run factory stage run --change ./fixtures/chg_smoke.yaml --stage construction --json --non-interactive` |
| `factory-stage-smoke` | `CI_SKIP_FACTORY_STAGE_SMOKE` | Тот же smoke-прогон, но через переиспользуемый workflow `.github/workflows/factory-stage.yml` — контракт, которым пользуются stage-job'ы фабрики | минуты | тот же `stage run` плюс `uv run python -m dark_factory.cli.ci_job stage_result.json` |

### Console-гейты

| Этап (job) | Переменная | Что проверяет | Вес | Локально |
|---|---|---|---|---|
| `console-lint` | `CI_SKIP_CONSOLE_LINT` | ESLint по `console/` | десятки секунд | `cd console && npm ci && npm run lint` |
| `console-typecheck` | `CI_SKIP_CONSOLE_TYPECHECK` | `tsc --noEmit` | десятки секунд | `npm run typecheck` |
| `console-test` | `CI_SKIP_CONSOLE_TEST` | vitest: unit- и компонентные тесты | десятки секунд | `npm run test` |
| `console-build` | `CI_SKIP_CONSOLE_BUILD` | `vite build` — продакшн-бандл собирается (нужен и консольному образу) | десятки секунд | `npm run build` |
| `console-e2e` | `CI_SKIP_CONSOLE_E2E` | Playwright smoke: сборка, `vite preview`, синтетический API; отчёт — артефакт | минуты (скачивание Chromium) | `npx playwright install --with-deps chromium && npm run e2e` |

### UIKit-гейты

| Этап (job) | Переменная | Что проверяет | Вес | Локально |
|---|---|---|---|---|
| `uikit-lint` | `CI_SKIP_UIKIT_LINT` | ESLint (кит и продуктовая политика) и stylelint (дисциплина токенов) | десятки секунд | `cd packs/ui/blueprint/ui && npm ci && npm run lint` |
| `uikit-typecheck` | `CI_SKIP_UIKIT_TYPECHECK` | `tsc --noEmit` | десятки секунд | `npm run typecheck` |
| `uikit-test` | `CI_SKIP_UIKIT_TEST` | vitest: компоненты, axe (a11y) и токены | десятки секунд | `npm run test` |
| `uikit-gates` | `CI_SKIP_UIKIT_GATES` | Самотест UI-гейтов: нарушения блокируются, чистый код проходит | десятки секунд | `npm run test:gates` |
| `uikit-storybook` | `CI_SKIP_UIKIT_STORYBOOK` | `storybook build` — исполняемая UI-спецификация собирается | минуты | `npm run storybook:build` |
| `uikit-visual` | `CI_SKIP_UIKIT_VISUAL` | В pinned-контейнере Playwright: сборка Storybook и сравнение скриншотов с закоммиченными baseline'ами | минуты (включает сборку Storybook) | `npm run test:visual` (тот же контейнер) |

### Доверенные сборки образов

| Этап (job) | Переменная | Что проверяет | Вес | Локально |
|---|---|---|---|---|
| `factory-image` | `CI_SKIP_FACTORY_IMAGE` | gitleaks → bandit (SAST) → pip-audit (SCA) → multi-arch сборка и публикация `sha-<sha>` в ghcr → trivy (fail-closed) → SBOM (syft) | самый тяжёлый | `./deploy/ci/scripts/build-image.sh` (`--rebuild-check` — холодная перепроверка digest) |
| `console-image` | `CI_SKIP_CONSOLE_IMAGE` | gitleaks → сборка бандла → multi-arch образ консоли `sha-<sha>` → trivy → SBOM | тяжёлый | `cd console && npm ci && npm run build && docker build -t dark-factory-console:local .` |

### Примечания к этапам

**`test` — база для интеграционных тестов.** CI поднимает сервис PostgreSQL со строго этими параметрами (`.github/workflows/ci.yml`, job `test`); локально то же самое:

```sh
docker run --rm -d --name dark-factory-test-pg -p 55432:5432 \
  -e POSTGRES_USER=test -e POSTGRES_PASSWORD=test -e POSTGRES_DB=dark_factory_test \
  postgres:16-alpine
export DARK_FACTORY_TEST_DATABASE_URL=postgresql+psycopg://test:test@localhost:55432/dark_factory_test
uv run pytest
```

Без `DARK_FACTORY_TEST_DATABASE_URL` интеграционные тесты **скипаются**, а не падают, — то есть локально легко получить зелёный прогон без БД; CI гоняет полный набор.

**`security` локально:**

```sh
uv export --frozen --no-emit-project --no-dev > pip-audit-requirements.txt
uvx pip-audit -r pip-audit-requirements.txt
```

**Образы.** Локальные команды собирают образ примерно так же, но публикация и полный evidence-набор (multi-arch, trivy по обеим платформам, SBOM) доступны только в CI — там pinned-инструменты и registry. `factory-image` собирается только после зелёных включённых гейтов.

**`uikit-visual` и baseline'ы.** Сравнение идёт против закоммиченных скриншотов, валидных только для pinned-браузера: локальный браузер даст ложные расхождения. Обновление baseline'ов — метка `uikit-baselines` на PR (workflow `.github/workflows/uikit-baselines.yml`), ревью диффа и коммит — человеком.

### Соседние workflow (этой схемой не переключаются)

- `factory-image.yml`, `console-image.yml`, `factory-stage.yml` — переиспользуемые (`workflow_call`): запускаются только вызовом из `ci.yml`, поэтому их включение/выключение — это переключатели вызывающих этапов `factory-image`, `console-image` и `factory-stage-smoke`.
- `uikit-baselines.yml` — запускается по метке `uikit-baselines` на PR и в обычных MR не участвует.

## Если что-то не так

| Симптом | Причина | Что делать |
|---|---|---|
| Этап `Skipped`, хотя переменную не ставили | Для сборок образов: этап, от которого они зависят, был пропущен или упал | Проверить, какие гейты выключены/красные (`gh run view <run-id>`); сборка не идёт после упавшего гейта — это норма |
| Переменную поставили, а этап всё равно выполнился | Значение не равно `true` (например, лишние пробелы) или прогон стартовал до изменения | `gh variable list`; перезапустить прогон |
| Экран «Этапы CI» показывает «не сконфигурировано» | API не получил GitHub App с правом Variables: read/write | Задать `ciToggles.existingSecret` и `ciToggles.repositorySlug` (chart) либо `DARK_FACTORY_GITHUB_*` (ADR-027) |
| Экран отвечает 403 на переключение | У токена нет scope `ci:write` или роли operator | Взять operator-токен с нужным scope |
| Нужно выключить этап только в одном MR | Так нельзя: переменные действуют на весь репозиторий | Выключить на время и вернуть после; per-PR переключатели — возможная доработка (см. ADR-026) |
| PR не мёрджабится после выключения этапа | Этап входит в required status checks, а пропущенный чек не считается пройденным | Вернуть этап или убрать его из обязательных проверок ветки |

## Чего пока нет (ограничения)

- **Per-PR переключателей** (метка на MR вида `ci:fast`) нет: управление — только переменными репозитория. Альтернативы и причина — в [ADR-026](../adr/ADR-026-parameterizable-ci-stages.md).
- **Отдельного прогона «только быстрые этапы»** по кнопке нет: выключение — через переменные, как описано выше.
- Визуальные baseline'ы обновляются только по метке `uikit-baselines` и с ревью диффа.

## Откуда взяты правила

- `.github/workflows/ci.yml` — сами условия (`if: vars.CI_SKIP_*`) и состав этапов.
- [ADR-026](../adr/ADR-026-parameterizable-ci-stages.md) — решение: почему repository variables, opt-out и fail-safe.
- [ADR-027](../adr/ADR-027-console-ci-stage-toggles.md) — экран «Этапы CI» в консоли: те же переменные за API (`ci:write` + operator, fail-closed).
- `src/dark_factory/ci/stages.py` — каталог этапов (что API отдаёт консоли); `console/src/pages/CiStagesPage.tsx` — сам экран.
- [ADR-011](../adr/ADR-011-risk-based-merge-release-policy.md) — merge человеком, релиз по зелёным гейтам.
- [docs/development-workflow.md](../development-workflow.md) — git-цикл задачи.
- `tests/test_ci_stage_toggles.py` — тест, который следит, чтобы у каждого этапа был переключатель, а список в этой инструкции не разошёлся с `ci.yml`.
