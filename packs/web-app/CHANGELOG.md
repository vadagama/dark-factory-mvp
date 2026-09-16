# Changelog

Формат — Keep a Changelog; версии — SemVer, синхронно с `pack.yaml.version`.

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
