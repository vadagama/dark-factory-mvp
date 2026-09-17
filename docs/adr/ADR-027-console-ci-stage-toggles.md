# ADR-027: Управление этапами CI из консоли — репозиторные переменные за API (T059)

- **Статус**: принято
- **Дата**: 2026-09-17
- **Автор**: ci-cd
- Уточняет семантику значения из [ADR-026](ADR-026-parameterizable-ci-stages.md) (регистр в сравнении строк GitHub) и расширяет периметр композиции [ADR-025](ADR-025-process-entry-point-and-lazy-composition.md) для команды `api serve`.

## Контекст

- [ADR-026](ADR-026-parameterizable-ci-stages.md) (T058) сделал этапы `.github/workflows/ci.yml` переключаемыми: у каждого из 20 job'ов есть repository variable `CI_SKIP_<JOB>`, а управление — только руками (`gh variable set/delete` или UI GitHub).
- Запрос: те же этапы и переключатели — в консоли фабрики (T036, [ADR-021](ADR-021-console-mvp-delivery.md)), без терминала.
- Факты, определившие выбор:
  - **состояние обязано жить в GitHub**: workflow читает `vars.CI_SKIP_*`, поэтому состояние в БД фабрики или в снапшоте консоли для пайплайна невидимо;
  - запись repository variables требует привилегированной учётки: REST «Actions variables» принимает fine-grained PAT и **installation access token GitHub App** с правами **Variables (read/write)**; в браузере такое право давать нельзя — [ADR-021](ADR-021-console-mvp-delivery.md) п.4 держит в `localStorage` только operator-токен;
  - в репозитории уже есть App-based адаптер GitHub (installation tokens, минимальные права — [ADR-019](ADR-019-multi-provider-sc-ci-github-first.md) п.3) с общим HTTP-клиентом, composition root ([ADR-024](ADR-024-durable-run-driver-and-composition-root.md) п.5) и правилами границ импортов: ядро (включая `cli`) не импортирует адаптеры и не называет `runtime` — биндинги приходят **аргументом**;
  - `factory api serve` до сих пор был core-путём без runtime ([ADR-025](ADR-025-process-entry-point-and-lazy-composition.md)): адаптеры для него не собирались;
  - статичные справочные данные консоли отдаются снапшотом `console/src/generated/meta.json` ([ADR-021](ADR-021-console-mvp-delivery.md) п.5), но состояние переключателей динамическое — снапшот для него не годится;
  - fail-closed — норма проекта: пустой token store отклоняет мутации, неполная конфигурация означает «недоступно», а не догадку.

## Решение

1. **Каталог этапов — код, один источник истины**: `dark_factory.ci.stages` (`CiStage`, `CI_STAGES`, `variable_for_job`, `is_skipped_value`). Из него берут данные API, консоль (через ответ API), тесты и drift-проверки. Тест `tests/test_ci_stage_toggles.py` связывает каталог с `ci.yml`: состав job'ов, `title` = `name:` джобы, `variable` = переменная из её `if`.
2. **Порт `CiStageTogglePort`** (`ports/protocols.py`) — value-level: `values() -> Mapping[str, str]` и `set_value(variable, value | None)`. Значение, выключающее этап, знает каталог, а не провайдер. Порт **привязан к одному репозиторию при конструировании**: запрос не может перенаправить ни чтение, ни запись на другой репозиторий.
3. **Адаптер `GitHubCiStageToggles`** (`adapters/scm/github/variables.py`) поверх Actions variables REST: `GET` (с пагинацией до конца списка), `PATCH`, `POST` при 404 (в REST нет upsert), `DELETE` (404 = переменной уже нет). Использует тот же общий клиент и installation token, что остальные порты GitHub; нужное право — **Variables: read/write**.
4. **API — единственный писатель**: `GET /api/v1/ci/stages` (каталог + состояние; GET без токена, локальный контур [ADR-009](ADR-009-minimal-bootstrap-otel.md) п.7) и `PUT /api/v1/ci/stages/{job}` с телом `{"enabled": bool}`.
   - Запись требует Bearer со scope **`ci:write`** **и** ролью operator: агенты и сервисные токены не перенастраивают пайплайн, который их гейтит ([ADR-011](ADR-011-risk-based-merge-release-policy.md)). Иначе 401/403.
   - Неизвестный job → **404**: запрос никогда не называет произвольную репозиторную переменную (её выводит каталог).
   - Контур без конфигурации → `available: false` + `reason` у GET и **503** у PUT; ошибка провайдера → **502** с санитизированным текстом адаптера, без тела ответа GitHub ([ADR-009](ADR-009-minimal-bootstrap-otel.md)).
   - Тело строгое (`strict=True`): `"yes"`/`1` в `enabled` не коэрцятся — состояние гейта не место для мягкого парсинга.
5. **Семантика записи**: `enabled: true` → переменной нет (DELETE); `enabled: false` → значение `true`. Операция идемпотентна (целевое состояние, не событие), `Idempotency-Key` не требуется.
6. **Чтение состояния зеркалит workflow**: `is_skipped_value` сравнивает значение **без учёта регистра** (`true`/`True`/`TRUE` — GitHub игнорирует регистр при сравнении строк в выражениях) и **не** нормализует пробелы: консоль не показывает состояние, с которым пайплайн не согласится. Это уточнение к формулировке ADR-026.
7. **Композиция**: адаптер строится в `build_runtime` по `DARK_FACTORY_GITHUB_*` плюс новый `DARK_FACTORY_GITHUB_REPOSITORY_SLUG`; `runtime/entrypoint` для `api serve` собирает `Runtime` и передаёт биндинг аргументом (правило D: `cli` не импортирует `runtime`). Core-путь `python -m dark_factory.cli` остаётся без seams — `/ci` там честно `available: false`. Chart: опциональные `ciToggles.existingSecret` (`envFrom` `secretRef`, `optional: true` — паттерн runner'а) и `ciToggles.repositorySlug`.
8. **Консоль**: экран `/ci` — этапы по группам, вес, локальная команда, выключатель `button[role="switch"]` на существующих CSS-токенах; **новых npm-зависимостей нет** (ADR-021 п.1). Правила ADR-026 (только для разработки, вернуть перед merge) показаны прямо на экране; при `available: false` контролы выключены, состояние — «неизвестно».
9. **Проверяемость**: каталог и дрейф с `ci.yml` — в `tests/test_ci_stage_toggles.py`; эндпоинты, авторизация и fail-closed — `tests/test_api_ci_stages.py`; адаптер (пагинация, upsert-по-404, идемпотентный DELETE, маппинг ошибок) — `tests/test_adapters_github_variables.py`; chart-рендер — `tests/test_chart_dark_factory.py`; консоль — vitest + Playwright smoke.

## Альтернативы

| Вариант | Плюсы | Минусы | Почему не выбран |
|---|---|---|---|
| Хранить состояние переключателей в PostgreSQL фабрики | Единый state store, аудит, нет зависимости от GitHub API | Workflow читает `vars.CI_SKIP_*` — состояние из БД пайплайну невидимо; понадобился бы шаг синхронизации в GitHub | Отклонено: источник истины обязан быть там, откуда читает CI |
| Отдавать каталог снапшотом в `console/src/generated/` (как `meta.json`) | Ноль изменений в API для каталога | Состояние всё равно динамическое, а API обязан валидировать имена переменных → каталог дублировался бы в двух местах | Отклонено: один источник (`dark_factory.ci.stages`) и один контракт |
| Привилегированный GitHub-токен в браузере (localStorage) | Простая реализация без бэкенда | Admin-уровневые креденшелы в UI и в XSS-периметре; [ADR-021](ADR-021-console-mvp-delivery.md) п.4 прямо запрещает | Отклонено |
| Обёртка над `gh variable set` из контейнера | Переиспользование CLI | `gh` в образе нет, обход port-дисциплины, шелл-поверхность | Отклонено |
| Разрешить запись любому валидному Bearer-токену (без scope/роли) | Меньше конфигурации | Сервисный токен/агент смог бы выключить собственные гейты | Отклонено: `ci:write` + operator ([ADR-011](ADR-011-risk-based-merge-release-policy.md)) |
| Строить адаптер прямо в `cli/api.py` | Не трогает runtime | Нарушает правила A/D границ импортов и дисциплину композиции [ADR-024](ADR-024-durable-run-driver-and-composition-root.md) п.5 | Отклонено |
| Писать `false` вместо удаления переменной при включении этапа | Значение видно в UI GitHub | Эквивалентно по семантике ADR-026, но оставляет мусор в настройках репозитория | Отклонено: DELETE (enabled = «переменной нет») |
| Разрешить `PATCH` с произвольной переменной и значением | Гибкость | Произвольная запись в настройки репозитория из UI; инъекция имён | Отклонено: только каталог + строгий bool |
| Мягкий bool (`enabled: "yes"`) | «Удобнее» для ручных вызовов | Тихая коэрция состояния гейта | Отклонено: `strict=True`, иначе 422 |

## Последствия

**Позитивные**

- Оператор включает и выключает этапы из консоли: рядом с выключателем — что этап проверяет, его вес и локальная команда; `gh` не нужен, правила «вернуть перед merge» видны на экране.
- Каталог, workflow, инструкция и консоль связаны тестами: расхождение (новый job без переключателя, изменённый `name:`, забытая переменная) краснит CI, а не остаётся незамеченным.
- Право записи минимально и явно: App с Variables: read/write включается только конфигурацией контура (`ciToggles.existingSecret`), а operator-токен остаётся отдельным гейтом мутации.

**Негативные / риски**

- `api serve` впервые собирает runtime, что ослабляет формулировку ADR-025 «api serve зависит только от ядра»: компенсировано ленивостью по команде — без `DARK_FACTORY_GITHUB_*` адаптеры не строятся, а эндпоинты отвечают «не сконфигурировано».
- Переключатели, выключенные из консоли, действуют на весь репозиторий и не хранятся в git (то же ограничение, что у ADR-026): нужна дисциплина «вернуть полный набор перед merge».
- Права App расширяются до Variables: read/write — supply-chain-креденшел ([ADR-019](ADR-019-multi-provider-sc-ci-github-first.md) п.3) с более широкой областью, чем чтение кода; включается только явно и осознанно.
- Состояние читается с GitHub API на каждый заход на экран; гонка между оператором и ручным `gh variable set` не предотвращается — последнее слово всегда за GitHub (это и есть источник истины).
- «Неизвестное» состояние (`enabled: null`) требует дисциплины UI: показывать именно «неизвестно» и блокировать контролы, иначе оператор прочитает неизвестное как «выключено».

**Дальше**

- Per-PR переключатели (метка вида `ci:fast`) остаются возможной доработкой ADR-026 и естественно ложатся на тот же каталог и API.
- Read-only `/api/v1/meta/*` для лимитов/профилей ([ADR-021](ADR-021-console-mvp-delivery.md) п.5, T-062) — тот же путь «динамика вместо снапшота», что пройден здесь.

Связанные задачи: T059 (эта задача), T058 (ADR-026, переключатели в CI), T036 (ADR-021, консоль), T035 (API). Связанные ADR: [ADR-009](ADR-009-minimal-bootstrap-otel.md), [ADR-011](ADR-011-risk-based-merge-release-policy.md), [ADR-019](ADR-019-multi-provider-sc-ci-github-first.md), [ADR-021](ADR-021-console-mvp-delivery.md), [ADR-024](ADR-024-durable-run-driver-and-composition-root.md), [ADR-025](ADR-025-process-entry-point-and-lazy-composition.md), [ADR-026](ADR-026-parameterizable-ci-stages.md).
