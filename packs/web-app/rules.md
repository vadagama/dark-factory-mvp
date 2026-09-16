# Правила пака

## Пак — данные, не код

- Шаблоны копируются в продуктовый репозиторий как есть; правки шаблона делаются
  в паке (версия + CHANGELOG), а не в конкретном продукте.
- Каждый шаблон валидируется тестами фабрики (`tests/test_packs_web_app.py`):
  манифест, парсимость (tomllib/json/yaml), digest-пиннинг базовых образов,
  отсутствие floating-тегов (`:latest`) и секретов, публикация образов только
  из `main`, вписанность чарта в квоту `apps-dev` (T029).
- Секретов в шаблонах нет и быть не может: только env / `.env.example` с
  placeholder'ами и `existingSecret`-ссылки (секрет создаётся вне git —
  правило 2 репозитория GitOps).
- Изменение стека blueprint (ADR-014/ADR-015/ADR-019) — только через новый ADR.

## Тестовые конвенции продукта (пирамида)

- **Unit — быстрый слой.** Python: `pytest` без сети и БД — конфиг,
  бизнес-логика, эндпоинты на подменённых зависимостях (`dependency_overrides`);
  детерминизм через `asyncio.run` без pytest-asyncio (паттерн тестов фабрики).
  TypeScript: `vitest` в jsdom — компоненты и API-клиенты на подменённом
  `fetch`. Цель — секунды, локально на каждый коммит.
- **Integration — реальные зависимости.** Python: `pytest` против живого
  PostgreSQL, включается переменной `APP_TEST_DATABASE_URL` (без неё — skip:
  паттерн `tests/integration/conftest.py` фабрики). Локально — docker/postgres,
  в CI — service-контейнер. TypeScript: `vitest` + `@testing-library` против
  реальных компонентов.
- **Smoke/e2e — релизный слой.** Playwright smoke собранного приложения
  (паттерн console e2e); в релизном контуре — smoke на digest в `apps-dev`
  как часть release evidence (T-045): статус «выпущено» — только при успешном
  smoke (ADR-011 п.6).

## Связь с гейтами фабрики

| Гейт | Что проверяет | Где исполняется |
|---|---|---|
| code | lint + typecheck + unit/integration тесты | продуктовый CI (`.github/workflows/ci.yml`) на каждый MR и push; проверки повторяются на итоговом SHA изменения (FR-009) |
| review | человеческое approve продуктового MR; merge — только человек (ADR-011) | продуктовый репозиторий |
| verification | release evidence: digest образа, smoke в dev, GateDecision | пилотный прогон фабрики (T-045) |

- UI-гейты входят в blueprint: workspace-пак `@small/ui` — реальный Small UIKit
  из пака `packs/ui` (T042, ADR-014) с UIKit-policy ESLint (запрет импортов вне
  `@small/ui`), stylelint, axe-тестами, Storybook-сборкой и самотестом гейтов
  (`npm run ui:*` в `frontend`, джоба `frontend-ui-gates` продуктового CI).
  Visual regression исполняется в фабричном CI на паке `packs/ui` в закреплённом
  браузерном контейнере (контракт детерминизма — `packs/ui/rules.md`);
  неавтоматизируемые критерии WCAG 2.2 AA подтверждает человек как UI evidence.
- Продукт не зависит от внутреннего Python API фабрики (ADR-015 п.6):
  интеграция — через схемы, CLI и этот версионированный пак.
