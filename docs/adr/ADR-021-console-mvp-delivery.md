# ADR-021: Console MVP — размещение, сборка и отдача (T036)

- **Статус**: принято
- **Дата**: 2026-09-15
- **Автор**: software-architect
- Решение подготовлено до реализации T036 (Console MVP, план T-051); Console — первый JS/TS-сабпроект в Python-репозитории, поэтому способ размещения, сборки, публикации и отдачи зафиксирован отдельным ADR
- UI-стек Console уже зафиксирован ([ADR-014](ADR-014-react-uikit-storybook.md) п.4: React + Radix/shadcn; Console не определяет UI Kit продуктов) — настоящий ADR его не пересматривает

## Контекст

- T036 (docs plan T-051): 5 экранов — (1) список изменений со статусами, (2) карточка изменения с evidence и цепочкой стадий, (3) гейты/approvals, (4) бюджеты/лимиты, (5) настройки/профили; режимы «с согласованиями» и «автономно до MR». DoD: typecheck + lint + test (vitest); e2e 5 сценариев (Playwright smoke); сборка chart'ом (`charts/dark-factory`, задача T031/план T-042).
- Спека (US6, [spec.md](../../specs/001-dark-factory-mvp/spec.md)): FR-019 (Console: список инициатив/запусков, стадия, вопросы/блокеры, стоимость, гейты, ссылки на MR/проверки/evidence; согласования — проверенная операция авторизованного пользователя), FR-001 (intake из Console), SC-008 (потеря локального кэша Console не влияет на процесс).
- API готов (T035, [api.md](../descriptions/api.md)): `GET /api/v1/changes`, `/changes/{id}`, `/changes/{id}/trace`, `/changes/{id}/approvals`, `POST /changes/{id}/approvals` (Bearer + `approvals:write` + роль `operator`), `GET /runs`, `/runs/{id}` (RunCard: stages, usage, gates, open_blockers) и trace/evidence/gates/findings. GET открыты (локальный контур, [ADR-009](ADR-009-minimal-bootstrap-otel.md) п.7), мутации — Bearer-токены из `DARK_FACTORY_API_TOKENS`, fail-closed.
- [ADR-002](ADR-002-python-core-stack.md) фиксирует Python-стек ядра; репозиторий — `dark-factory`, модульный монолит ([ADR-015](ADR-015-repository-boundaries.md) п.1, включая charts и CI-шаблоны); деплой — локальный K8s + Helm + GitOps ([ADR-010](ADR-010-local-k8s-helm-argocd.md)); merge — человек, approval version-bound ([ADR-011](ADR-011-risk-based-merge-release-policy.md), [ADR-009](ADR-009-minimal-bootstrap-otel.md) п.7).
- Инфраструктурные факты, влияющие на выбор:
  - chart `charts/dark-factory` рассчитан на расширение «второй деплой/сервис рядом с API» (README chart'а); квота ns `factory` (T029: requests 1 CPU/2Gi, limits 2 CPU/4Gi, pods 20, LimitRange min 25m/32Mi) имеет запас под компактный второй под; Ingress включён в values-local, но без контроллера инертен — штатный локальный доступ `kubectl port-forward`.
  - API-образ (`deploy/ci/image/Dockerfile`, T033/T044) — детерминированный: pinned base-дайджесты, SOURCE_DATE_EPOCH, нормализация mtime, runtime-дерево одной COPY в несуществующий /app, rebuild-check на совпадение digest. Контракт тонко выверен и валидирован — его дестабилизация дорога.
  - Trusted workflow `.github/workflows/factory-image.yml` публикует immutable `sha-<sha>` в ghcr; ci.yml — детерминированные jobs (lint, typecheck, test, build, security + factory-гейты).
  - `.gitignore` уже содержит Node-записи (`node_modules/`, `dist/`, `build/`); `.dockerignore` в репозитории нет (docker-контекст не фильтруется gitignore).
  - hatchling пакует весь `src/dark_factory` (`packages = ["src/dark_factory"]`, sdist — `["src/dark_factory", "tests"]`); ruff/mypy настроены на Python (`src`, `tests`); `tests/test_import_boundaries.py` сканирует только `*.py` в `src/dark_factory`.
  - tasks.md (bootstrap-фаза) намечает `src/dark_factory/console/` — расположение требует проверки на конфликты.
- Данные для экранов 4–5: лимиты — `src/dark_factory/rules/limits.py` (rework/token/cost/deadline как чистые правила), профили — `src/dark_factory/agents/profiles/` (versioned frozen-манифесты). В API этих данных сейчас нет.

## Решение

1. **Размещение и тулинг: `console/` верхнего уровня; Vite + React + TypeScript; npm.**
   - Console размещается в **`console/`** (топ-уровень), а не в `src/dark_factory/console/`:
     - hatchling пакует всё содержимое `src/dark_factory` в wheel и sdist — TS-исходники, `package.json` и тем более `node_modules` попали бы в Python-артефакты; исключения в hatch-конфиге лечили бы симптом, но не инвариант «всё в `src/dark_factory/` — Python, поставляемый колесом» (mypy strict, `py.typed`, границы импортов ADR-015 п.3);
     - ruff/mypy/import-boundaries сканируют только `*.py`, поэтому прямой поломки проверок нет — риск в упаковке и гигиене (`node_modules` внутри исходников пакета);
     - Console — отдельный рантайм-артефакт со своим тулингом и образом, а не модуль монолита; физическое размещение это отражает (по образцу топ-уровневых `charts/`, `deploy/`, `docs/`);
     - компонент `console` в HLD §6 остаётся логическим именем; физическое размещение — настоящий ADR.
   - **Правка AGENTS.md (раздел «Структура репозитория»: добавить `console/`) входит в задачу T036** — вносит оркестратор/исполнитель при реализации; сам AGENTS.md настоящим ADR не правится.
   - Тулинг: Vite + TypeScript (strict) + React + Radix/shadcn ([ADR-014](ADR-014-react-uikit-storybook.md)); vitest; Playwright; ESLint.
   - **Пакетный менеджер — npm** с зафиксированным `package-lock.json` (`npm ci`): один пакет без workspaces, минимум движущихся частей; pnpm не даёт преимуществ, оправдывающих второй тулчейн (corepack, отдельный lockfile-формат). Node-версия пиннится в `console/.nvmrc` (LTS на момент реализации; CI использует `node-version-file`).

2. **Модель отдачи: отдельный образ консоли (nginx) + отдельные Deployment/Service в том же chart; API-образ не трогается.**
   - Сборка: dist собирается в trusted workflow `console-image.yml` (по образцу `factory-image.yml`: gitleaks до сборки, pinned node из `console/.nvmrc`, `npm ci`, `vite build`, нормализация mtime dist) и копируется в образ **из build-контекста**; сам Dockerfile консоли одноцелевой: pinned-by-digest `nginx-unprivileged` + `COPY dist` + `COPY nginx.conf`. Сборка dist вне Docker устраняет npm под QEMU-arm64 в multi-arch сборке и даёт одинаковый dist для обеих платформ. `.gitignore` (`dist/`) не блокирует контекст — в репозитории нет `.dockerignore`; нюанс фиксируется комментарием в Dockerfile, случайный `.dockerignore`, исключающий dist, запрещён.
   - Публикация: immutable-тег `sha-<sha>` в `ghcr.io/vadagama/dark-factory-console`; multi-arch (linux/amd64 + linux/arm64, ADR-010 п.4); evidence — trivy (обе платформы, fail-closed на unfixed HIGH/CRITICAL), syft SBOM, digest-артефакт. **Rebuild-check для консольного образа не требуется** — осознанное отличие от контракта T033: детерминизм digest является DoD только API-образа; консоль — функциональный UI-артефакт, digest остаётся immutable.
   - Размещение в chart: второй Deployment + Service (`console`), ресурсы requests 25m/32Mi / limits 100m/64Mi (вписывается в LimitRange и запас квоты T029), тот же securityContext-паттерн (non-root, readOnlyRootFilesystem, drop ALL, seccomp, automount=false), SA по конвенции chart'а. **nginx проксирует `/api` на API Service** — консоль самодостаточна как единая точка входа: same-origin без CORS, один port-forward (`svc/...-console`) даёт и UI, и API.
   - Ingress (при появлении контроллера): `/` → console, `/api` → API; до того штатный доступ — port-forward (как и сегодня, NOTES.txt).
   - Почему не отдача статикой из API-образа: потребовала бы node-тулчейн в builder-стадии детерминированного образа (новый источник недетерминизма, пересборка и повторная валидация контракта T044 — ONE-COPY /out, rebuild-check), добавила бы npm-registry в supply-chain сборки API и перестволила бы API-образ при каждом изменении UI. Выгода «один Deployment/Service» не покрывает стоимость.
   - Почему не sidecar/ConfigMap-варианты: dist в ConfigMap (лимит ~1MiB, чужой механизм обновления) или nginx-sidecar в API-поде связывают жизненные циклы API и UI без уменьшения числа частей.

3. **Dev-режим: vite dev server с прокси на локальный API.**
   - `cd console && npm ci && npm run dev` — dev-сервер на `127.0.0.1:5173`; прокси `/api` → `http://127.0.0.1:8000` (API поднимается штатно `factory api serve`; цель прокси переопределяется env-переменной, например `VITE_API_TARGET`).
   - Топология same-origin в dev повторяет прод (nginx-прокси) — CORS не нужен ни в одном режиме, код API не меняется. Инструкция — в `console/README.md`.

4. **Аутентификация write-операций: Bearer-токен оператора на экране настроек, localStorage, fail-closed UX.**
   - Токен вводится на экране 5 (настройки/профили), хранится в `localStorage` (SC-008 прямо допускает потерю локального кэша: теряется только токен, процесс не затронут — токен перевводится). Отображение маскированное (суффикс), есть замена/очистка.
   - Заголовок `Authorization: Bearer <token>` прикрепляется **только к мутациям** (`POST /changes` — intake, `POST /changes/{id}/approvals`) вместе с `Idempotency-Key`; GET-запросы токен не носят (минимизация экспозиции; API игнорирует Authorization на чтении).
   - Fail-closed без изменения API: без токена мутационные действия в UI недоступны с подсказкой ввести токен; 401 → диалог «нужен токен», 403 → показ ошибки scope/role как есть (`approvals:write`, роль `operator` — сервисная роль не аппрувит, ADR-009 п.7/ADR-018), 409 `state_revision mismatch` → перечитать карточку и повторить решение (version-bound approval, ADR-011).
   - Токен никогда не попадает в git, URL, логи консоли и отчёты e2e (фикстуры используют синтетические значения). Секреты репозитория — только env/секреты, как и прежде.

5. **Данные экранов 4–5: генерируемый статический снапшот из Python-источников + drift-тест; read-only API-расширение — не в MVP.**
   - Экран 4 (бюджеты/лимиты): фактический расход (токены/стоимость/раунды) — из API (`RunCard.usage`, гейты); настроенные лимиты — из снапшота. Экран 5 (настройки/профили): ролевые профили — из снапшота; локальные настройки консоли (токен, адрес API) — localStorage.
   - Снапшот **генерируется механически** из источника истины: `console/tools/export_meta.py` (запуск `uv run`) сериализует `rules/limits` и `agents/profiles` в JSON, коммитится в `console/src/generated/meta.json`; pytest-тест (в штатном сюите) регенерирует снапшот в памяти и сверяет с коммиченным — расхождение красит CI (drift невозможен незамеченным). Дублирование в JS — **известный tech-debt** (коммит генерата + шаг регенерации, описан в console/README.md), а не ручная копия.
   - Консистентность версий обеспечивается сборкой: консоль- и API-образы публикуются из одного SHA (`sha-<sha>`), GitOps-промоушен ведёт их согласованно — снапшот в бандле соответствует API того же SHA.
   - Почему не read-only API-расширение (`GET /api/v1/meta/limits`, `/api/v1/meta/profiles`) сейчас: это аддитивное, но не бесплатное изменение модуля T035 (роутер + тесты, включая тест состава путей OpenAPI; обновление `docs/descriptions/api.md` и контракта) ради статичных справочных данных; перенос несложён и выполняется отдельным изменением при появлении динамики (allowance-политики T-062, управление профилями) — тогда meta-эндпоинты станут естественным источником.

6. **Тест-стратегия: vitest (unit/component) + Playwright smoke против vite preview с перехватом API.**
   - vitest + @testing-library/react: unit/component-тесты экранов и API-клиента; моки на уровне fetch-обёртки; фикстуры типизированы TS-типами, зеркалящими wire-контракты (`specs/001-dark-factory-mvp/contracts/api.md`); каноничность wire-формата уже закреплена Python-тестами T035 (`tests/test_api_auth.py`, `tests/integration/test_api.py`).
   - Playwright (chromium, версия пиннится) — smoke 5 сценариев против `vite preview` продакшн-сборки с перехватом `/api/v1/*` (`page.route`) на коммиченных JSON-фикстурах: эрметично, быстро, без PostgreSQL/uvicorn/сидов в e2e-job.
   - Прогон реальной консоли против реального API в chart-контуре — отдельная последующая задача (кластерный e2e), не часть DoD MVP.
   - Паритет локально/CI соблюдён: одинаковые npm-скрипты (`test:unit`, `test:e2e`, `lint`, `typecheck`, `build`) выполняются локально и в CI без различий окружения, кроме установки браузера Playwright.

7. **Интеграция CI: отдельные консольные jobs в ci.yml + trusted console-image job; существующие jobs не меняются.**
   - Добавляются jobs по паттерну «один чек = один job»: `console-lint` (eslint), `console-typecheck` (tsc --noEmit), `console-test` (vitest run), `console-build` (vite build), `console-e2e` (Playwright); checkout пиннится на итоговый SHA, как в существующих jobs; node — `setup-node@v4` с `node-version-file: console/.nvmrc` и кэшем npm.
   - Добавляется trusted job `console-image` (uses `./.github/workflows/console-image.yml`), needs: python-гейты `[lint, typecheck, test, build, security]` + консольные гейты — публикация только полностью зелёного SHA (семантика factory-image). `packages: write` — только в console-image (третий держатель credentials, trusted tier, как factory-image).
   - Drift-тест снапшота выполняется штатным pytest-job — node ему не нужен (генератор вызывается из Python-окружения).
   - Влияние на существующие jobs — нулевое: python-гейты, factory-us1-parity, factory-stage-smoke, factory-image не меняются.

## Альтернативы

| Вариант | Плюсы | Минусы | Почему не выбран |
|---|---|---|---|
| `src/dark_factory/console/` (как в tasks.md) | Не правит структуру репозитория | TS/package.json/node_modules попадают в wheel/sdist (hatchling пакует весь пакет); нарушает инвариант «пакет = Python»; гигиена node_modules внутри src/ | Отклонено: топ-уровневый `console/` |
| pnpm | Быстрее, строгий node_modules, экономия диска | Второй тулчейн (corepack, свой lockfile) ради одного пакета без workspaces | Отклонено: npm |
| API-образ отдаёт статику (FastAPI StaticFiles; node-стадия в Dockerfile или dist из контекста) | Один под/Service, один port-forward, нет нового образа | Дестабилизация проверенного контракта T044 (node в builder, расширение ONE-COPY /out, повторная валидация rebuild-check); npm в supply-chain сборки API; пересборка API-образа при каждом изменении UI | Отклонено: риск детерминизму T033/T044 не оправдан |
| Sidecar-nginx в API-поде / dist в ConfigMap | Один Deployment; нет нового образа фабрики | dist в ConfigMap (лимит ~1MiB, чужой механизм обновления); связывание жизненных циклов API и UI; тот же объём chart-работы при меньшей ясности | Отклонено |
| Read-only API-расширение (`/meta/limits`, `/meta/profiles`) для экранов 4–5 | Runtime-источник истины, нет дублирования | Изменение проверенного модуля T035 (роутер, тесты, OpenAPI-тест состава путей, docs) ради статичных справочных данных; лишняя поверхность API | Отложено до появления динамики (T-062); сейчас — генерируемый снапшот + drift-тест |
| Ручной статический снапшот (TS-константы) | Ноль инфраструктуры | Ручное дублирование Python-источника, незамечаемый drift | Отклонено: снапшот генерируется + drift-тест |
| Playwright против реального API с сидами | Проверка реального контракта end-to-end | PostgreSQL + сиды + uvicorn в e2e-job; хрупко и медленно для smoke | Отклонено для MVP: фикстуры + `page.route`; контурный e2e — отдельная задача |
| Один общий консольный CI-job (lint+typecheck+test+build) | Меньше jobs | Нарушает паттерн репозитория «ошибка атрибуется одному чеку» | Отклонено: отдельные jobs |

## Последствия

**Позитивные**
- Детерминированный API-образ (T033/T044) не затронут: Dockerfile, rebuild-check, сканы и evidence-контракт остаются без изменений.
- Chart расширяется предсказанным заранее способом («второй деплой/сервис»), локальный контур остаётся простым: один port-forward к console-сервису, same-origin без CORS, без изменений кода API.
- Консоль и API собираются из одного SHA и продвигаются согласованно; immutable `sha-<sha>` и evidence-практики распространяются на второй образ.
- Единый источник истины для лимитов/профилей сохранён (Python), дублирование механическое и контролируемое drift-тестом.

**Негативные / риски**
- Второй trusted workflow и второй образ — новая поверхность поддержки (пины node/nginx, обновления фикстур e2e); митигируется копированием проверенных паттернов factory-image и пиннингом всего.
- Коммиченный генерат снапшота требует дисциплины регенерации; пропуск ловится drift-тестом, но коммит-шум возможен — tech-debt зафиксирован, миграция на `/meta/*` при появлении динамики.
- `.gitignore`/docker-контекст-нюанс (dist из контекста) зависит от отсутствия `.dockerignore` — правило задокументировано в Dockerfile консоли.
- e2e на фикстурах не проверяет живой контракт консоль↔API; расхождение типов может быть замечено только контурным прогоном — контурный e2e заведён как последующая задача.

**Дальше**
- T036: создать `console/` (Vite + React + TS, npm, пины в `console/.nvmrc`/`package-lock.json`), правка раздела «Структура репозитория» AGENTS.md, chart-расширение (Deployment/Service/Ingress-пути, тесты `tests/test_chart_dark_factory.py`), trusted workflow `console-image.yml`, jobs в ci.yml, генератор и drift-тест снапшота, `console/README.md` (dev-режим, регенерация снапшота, токен).
- После MVP: контурный e2e (реальная консоль против реального API в chart-контуре); при появлении динамики лимитов/профилей (T-062) — read-only `/api/v1/meta/*` и отказ от снапшота.

Связанные задачи: T036 (T-051), T035 (T-050, API), T031 (T-042, chart), T033 (T-044, API-образ), T-062 (allowance — триггер миграции на meta-API). Связанные ADR: [ADR-002](ADR-002-python-core-stack.md), [ADR-009](ADR-009-minimal-bootstrap-otel.md), [ADR-010](ADR-010-local-k8s-helm-argocd.md), [ADR-011](ADR-011-risk-based-merge-release-policy.md), [ADR-014](ADR-014-react-uikit-storybook.md), [ADR-015](ADR-015-repository-boundaries.md), [ADR-018](ADR-018-human-participation-autonomous-execution.md).
