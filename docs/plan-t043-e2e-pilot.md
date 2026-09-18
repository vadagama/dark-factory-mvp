# План T043 — E2E-пилот фабрики

Статус: в работе. Трекер: `specs/001-dark-factory-mvp/tasks.md` T043.
Цель: 10 реальных задач (5 quick, 5 standard) через сквозной сценарий
`intake → SDD → реализация → MR → CI/review → merge → image → GitOps → dev → smoke`,
сбор метрик vision §7 и FR-024, журнал отклонений, отчёт по SC-001…SC-008.

## Инкремент 0 — bootstrap пилотного продукта (2026-09-18)

Цель: живой пилотный контур, на котором прогоняются 10 задач. DoD T-070
(«blueprint разворачивается в apps-dev») и TD-010 закрываются этим инкрементом.

Выполнено:

1. **Пилотный репозиторий** `vadagama/dark-factory-product-1` (private):
   применены `packs/web-app/blueprint/` + `packs/product-baseline/baseline/`
   (`.factory/`), плейсхолдеры заменены (22 файла), baseline валиден фабричным
   адаптером, bootstrap-коммит `940b3ae` запушен в `main` (операторское
   развёртывание паков, вне конвейера — по определению bootstrap).
2. **Фиксы шаблона пака** (канонично в `packs/web-app/blueprint/`, зеркально в
   продукт; пак 0.2.0 → 0.2.1):
   - backend импортируется без `DATABASE_URL`: убрана модульная сборка
     `app = create_app()`, uvicorn запускает фабрику `--factory`
     (run 1 CI: `ValidationError: Settings` при импорте);
   - hermetic-тесты `Settings` через fixture `model_config` + подавления
     `call-arg` (strict-mypy поверх pydantic `dataclass_transform`);
   - `psycopg[binary]` для резолва pq в окружениях без libpq — с верхним пином
     `<3.3` (см. ниже);
   - runtime-стадия `Dockerfile.backend` поднимает OS-пакеты `apt-get upgrade -y`
     (паттерн фабричного образа): дайджест-пин базы `python:3.12-slim-bookworm`
     не пересобран upstream и несёт `libpcre2-8-0 10.42-1` с 3 HIGH —
     fail-closed trivy-гейт отвергал каждую сборку (run 2);
   - `psycopg[binary]<3.3`: колёса psycopg-binary 3.3.x вшивают auditwheel-SBOM
     (`dist-info/sboms/auditwheel.cdx.json`) со старыми bundled-библиотеками
     build-окружения — arm64-колесо тащит `pcre2 10.32-3.el8_6` (6 CVE), trivy
     читает SBOM и бракует образ (run 3); 3.2.x колёса без SBOM (run 4 зелёный).
3. **CI продукта зелёный**: run 35301270578 (main @ `2a80607`) — 9/9 джоб,
   образы `sha-2a80607…` собраны и отсканированы:
   - backend `sha256:b5d96345d7626a0581786f539ba4d96f6ff2c0bf97aa6fefa7b6bfb9ec6820c1`
   - frontend `sha256:e12eda99bfd40fa6a26eaa1ac774eabe343245888cf62ef416ef3bbe8d2cd11a`
4. **GitOps**: MR `dark-factory-gitops#5` (admin-merge, TD-026) — каталог
   `envs/dev/dark-factory-product-1/` (чарт с digest-пинами), child Application
   `product-1-dev` в `envs/dev/apps.yaml`, seed-fixture `envs/dev/pilot/`
   удалена (квота: продукт занимает 1 CPU limits, busybox-фixture не влезает);
   секрет `dark-factory-product-1-db` (DATABASE_URL + POSTGRES_PASSWORD)
   создан в `apps-dev` вне git. Argo создал Application `product-1-dev`;
   старый `pilot-dev` Application удалён вместе с ресурсами fixture.
5. **Блокер инкремента (ожидает оператора)**: ghcr-пакеты продукта приватные —
   kubelet получает `unauthorized` (ErrImagePull на миграционном хуке). API для
   смены видимости пакета не существует, gh-токен без `read:packages`.
   Решение — сделать оба пакета публичными в GitHub UI (toy digest-pinned
   образы; решение зафиксировано в журнале) — после этого Argo докатит деплой,
   далее smoke: `GET /api/healthz` → `{"status":"ok","database":"ok"}`, `GET /`.

## Инкремент 1 — прогон 10 задач через фабрику

Подготовка окружения:

- PostgreSQL фабрики доступна CLI через проброс
  `kubectl -n factory port-forward svc/factory-postgres 5432:5432` (внутри
  скрипта; `DATABASE_URL` из `.env` переписывается на `127.0.0.1:5432`);
- workspace env: `DARK_FACTORY_WORKSPACE_ROOT=/Users/olegkrasnov/Documents/GitHub/df-workspaces`,
  `DARK_FACTORY_WORKSPACE_MIRROR_ROOT=/Users/olegkrasnov/Documents/GitHub/df-mirrors`;
- продукт зарегистрирован в фабрике intake'ом `POST /api/v1/changes`;
  запуск — `factory run advance` циклами, human-гейты (approval/merge) —
  оператор, агенты не мержат (ADR-011).

Матрица задач (черновик, финализируется перед стартом):

| # | Тип | Задача (суть) |
|---|-----|---------------|
| 1 | quick | текст/копирайтинг страницы или README продукта |
| 2 | quick | точечный bugfix backend с тестом |
| 3 | quick | правка UI-текста/лейбла на странице Health |
| 4 | quick | добавить unit-тест на существующий модуль |
| 5 | quick | мелкая правка конфигурации/докстроки |
| 6 | standard | новый GET-эндпоинт с интеграционным тестом |
| 7 | standard | новая страница frontend с компонентом кита |
| 8 | standard | расширение схемы БД + миграция Alembic |
| 9 | standard | фича с изменением backend+frontend |
| 10 | standard | рефакторинг с сохранением контрактов + тесты |

Для каждой задачи фиксируются метрики vision §7: стоимость с учётом попыток,
принятые с первого прохода, раунды rework (≤3 жёсткий лимит), время до
принятого MR, число ручных вмешательств (не approvals), escaped defects.

DoD пилота: ≥7/10 e2e без ручных правок артефактов агента; отчёт по
SC-001…SC-008 (SC-004…SC-006 — CLI/Console/roll back — покрываются прогоном);
журнал отклонений — `docs/t043-pilot-journal.md`.
