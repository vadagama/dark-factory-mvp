# Changelog

Формат — Keep a Changelog; версии — SemVer, синхронно с `pack.yaml.version`.

## [0.2.1] - 2026-09-18

### Fixed

- Backend импортируется без `DATABASE_URL`: модульная сборка `app = create_app()`
  убрана (она валидировала настройки при импорте и роняла CI и любые
  hermetic-импорты модуля); uvicorn запускает фабрику —
  `uvicorn app.main:create_app --factory`.
- Hermetic-тесты `Settings` изолируются fixture'ой на уровне `model_config`
  (per-call `_env_file=None` несовместим со strict-mypy поверх
  pydantic `dataclass_transform`); no-arg `Settings()` подавляет `call-arg`,
  т.к. обязательный `database_url` приходит из окружения.
- В runtime-зависимости добавлен extra `psycopg[binary]` с верхним пином
  `<3.3`: без колеса `psycopg-binary` резолв pq падает в окружениях без
  системного libpq (CI, hermetic unit-тесты), но колёса 3.3.x вшивают
  auditwheel-SBOM (`dist-info/sboms/auditwheel.cdx.json`) со списком
  старых bundled-библиотек build-окружения (например `pcre2
  10.32-3.el8_6` на arm64, 6 CVE) — fail-closed trivy-гейт читает этот
  SBOM и отвергает каждую сборку образа. Пин снимается, когда psycopg
  обновит bundled-библиотеки или trivy перестанет миксовать wheel-SBOM
  с OS-сканом.
- Runtime-стадия `Dockerfile.backend` поднимает все OS-пакеты до текущего
  пропатченного состояния (`apt-get upgrade -y`, паттерн фабричного образа):
  дайджест-пин базы `python:3.12-slim-bookworm` пересобирается upstream
  периодически, а `libpcre2-8-0 10.42-1` из базы несёт 3 HIGH CVE —
  fail-closed trivy-гейт отвергал каждую сборку образа.
- Миграционный hook Job переведён с `pre-install` на `post-install`
  (плюс `pre-upgrade`) с retry-циклом до готовности БД: pre-install
  выполняется ДО всех ресурсов чарта, поэтому первый релиз на пустом
  кластере дедлочился — Job не резолвил ещё не созданный in-chart Service
  PostgreSQL. Неудачный install сохраняет ресурсы, повторная попытка Argo
  идёт по ветке pre-upgrade на живой базе (конвергентно); retry
  (`sh`-обёртка, 30 попыток x 5s) поглощает время первого initdb.

## [0.2.0] - 2026-09-16

### Changed

- Реальный Small UIKit заменил заготовку в `frontend/packages/ui`: workspace-пак
  `@small/ui` из пака `packs/ui` 0.1.0 (12 компонентов, 5 паттернов, DTCG-токены,
  Storybook как исполняемая спека, побайтовый паритет с `packs/ui/blueprint/ui`
  под тестом фабрики).
- Подключены UI-гейты в продуктовый CI (джоба `frontend-ui-gates`):
  `ui:lint` (UIKit-policy ESLint + stylelint), `ui:typecheck`, `ui:test` (в т.ч.
  axe a11y и drift токенов), `ui:gates` (самотест политик), `ui:storybook:build`;
  продуктовая ESLint-политика кита подключена в `eslint.config.js` (запрет
  deep-импортов `@small/ui/*` и прямых импортов `@radix-ui/*` вне пакета кита).
- Visual regression в продуктовый CI не входит — он закреплён за фабричным CI
  на паке `packs/ui` (закреплённый браузерный контейнер, `packs/ui/rules.md`).
- HealthPage: setState больше не достижим синхронно из эффекта (правило
  `react-hooks/set-state-in-effect`), vitest скоуплен на `src/**` (тесты кита
  живут в `ui:test`), optional catch binding в API-клиенте.

## [0.1.0] - 2026-09-16

### Added

- Начальная версия пака (T041, docs plan T-070).
- Blueprint продуктового репозитория: FastAPI + PostgreSQL backend
  (`/api/healthz` с ping БД, конфиг из env через pydantic-settings, async
  SQLAlchemy, Alembic со стартовой миграцией, unit + integration тесты),
  React + Vite + TS frontend со слоем-заготовкой `@small/ui` (tokens + Button)
  и страницей, вызывающей backend `/api/healthz`.
- Helm chart приложения: backend/frontend Deployments + Services, in-chart
  PostgreSQL StatefulSet, миграции — pre-upgrade hook Job; образы только по
  immutable digest (digest-placeholder'ы в values), ресурсы в квоте apps-dev.
- Продуктовый CI: гейты (ruff/mypy strict/pytest, eslint/tsc/vitest) и сборка
  backend+frontend OCI-образов с публикацией immutable `sha-<sha>` только из
  main (паттерн factory-image.yml, упрощённо).
- `rules.md`: тестовые конвенции продукта (пирамида unit/integration/smoke) и
  связь с гейтами фабрики code/review/verification.
