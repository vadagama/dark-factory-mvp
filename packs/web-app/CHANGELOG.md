# Changelog

Формат — Keep a Changelog; версии — SemVer, синхронно с `pack.yaml.version`.

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
