# План T043 — E2E-пилот фабрики

Статус: в работе; инкремент 0 закрыт 2026-09-18. Трекер:
`specs/001-dark-factory-mvp/tasks.md` T043.
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
5. **Инкремент закрыт (2026-09-18)**: пакеты
   `dark-factory-product-1-{backend,frontend}` сделаны публичными в GitHub UI
   (оператор; TD-029 закрыт) — kubelet тянет анонимно; миграционный хук
   дофикшен канонично в паке (только post-фазы: Argo мапит pre-* в PreSync —
   first-install deadlock; wait-for-database retry 30×5s — коммиты `2ab3709`,
   `03d30da`). Argo `product-1-dev` Synced/Healthy, миграции прошли, smoke:
   `GET /api/healthz` → `{"status":"ok","database":"ok"}`, `GET /` → HTTP 200.
   DoD T-070 подтверждён живым деплоем, TD-010 закрыт; журнал —
   `docs/t043-pilot-journal.md`.

## Инкремент 1 — прогон 10 задач через фабрику

Подготовка окружения (выполнено, `deploy/local/pilot/increment-1.sh`):

- PostgreSQL фабрики доступна CLI через проброс; на этой машине порт
  55432 (5432 занят слушателем Docker Desktop) — `DATABASE_URL` из `.env`
  переписывается на `127.0.0.1:55432` внутри скрипта;
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

Прогресс (2026-09-18): окружение готово, intake 10 изменений выполнен.
Живой e2e `chg_t043_p02`: intake → specification (агент опубликовал спеку,
открыт CR `product-1#1`, CI зелёный) → waiting на human-гейте. По пути
закрыты 6 дефектов доводки (ключение ревизии run'а, GitHub 422, httpx2
через смену event loop, `write_file` у product-профиля, wait-действие по
характеру гейтов, human-резолюция waiting) — детали и открытые темы в
`docs/t043-pilot-journal.md`. Гейт — решение оператора; `chg_t043_p01`
пере-intake после закрытия гейта p02 (run до фикса приколот к digest).

Прогресс (2026-09-18, MR #91): контур гейтов дожат до construction. planning p02
резолвнута зелёным CI continuation-PR product-1#10 (PLANNING PASSED на `c96a8b7c`),
planning p03 — через continuation-PR product-1#11, открытый автоматически фиксом
`fix/t-043-publish-continues-after-merge` (публикация после смерженного спек-CR
открывает свежий CR). Оба рана остановлены гейтом входа в construction: нет пути
прикрепления Implementation Contract к рану (T-016 wiring) — решение оператора,
варианты в `docs/t043-pilot-journal.md`. p04–p10 — planning по той же схеме
(advance → CI → advance); p01 — пере-intake. Детали — `docs/t043-pilot-journal.md`.

Прогресс (2026-09-18, PR #86): доводка продолжена — tool-ошибки порта
(`KeyError` отсутствующего файла, отклонённый путь) больше не роняют
попытку: модель получает текст `error: ...` (`WorkspaceTools._model_facing`);
`GitHubClient.aclose` не await'ит пул чужого event loop (exit 1 после
сделанной работы). Все 9 спек опубликованы и ждут human-гейта
`specification`: p02→product-1#1, p03→#2, p04→#7, p05→#3, p06→#4,
p07→#5, p08→#8, p09→#9, p10→#6 (CI продукта зелёный). `chg_t043_p01` —
ран приколот к битому snapshot-digest (создан до PR #85), снимается
пере-intake после первого решения оператора. Решения по гейтам —
оператор (ADR-011/ADR-018); детали — `docs/t043-pilot-journal.md`.

Прогресс (2026-09-18, PR #88): оператор смержил все 9 спек-CR
(product-1#1–#9, без формальных review) — решение выражено мержем. Механизм
резолюции доведён до этого решения (3 дефекта): merge CR резолвит
human-gated стадию (`gate_resolved`); резолвнутая human-gated стадия строит
SUCCESS с human-гейтом PASSED на observed head SHA (`_resolve_human_gated`);
control points R2+ биндятся к SHA, на котором прошёл human-гейт стадии —
approval мержа закрывает точку `problem` (биндинг к head CR, не к
input_revision). След: advance p02–p10 → planning; p01 — пере-intake после
подтверждения резолва p02. Следующий advance каждой стадии planning
запускает агента (LLM-расход). Детали — `docs/t043-pilot-journal.md`.

Прогресс (2026-09-18, p02 после merge #12): оператор смержил `product-1#12`,
но advance p02 снова вернул `waiting` (exit 10) — merge policy требует
version-bound approving review на итоговом SHA (`f61f247a`), а у #12 ревью не
было; ран в `release` не перешёл. Решение оператора — протокол: перед merge
ставить approving review на CR (автор — фабричный бот, approve человеком
возможен), гейт не ослабляем, кода не меняем; p02 — невалидный прогон,
пере-запуск ради продукта не делается (код изменения уже в `main`). Попутно
найдено: `main` продукта не защищён (404 «Branch not protected»), фабрика не
наблюдает настройки защиты (`protection_violations` — только в тестах).
Формулировка ADR-029 п.5 уточнена. Детали — `docs/t043-pilot-journal.md`.

Прогресс (2026-09-18, p04): p04 провёден по схеме «advance → CI → advance» — planning
attempt 1 (роль product, ~51 с) → CR `product-1#14` (CI 9/9 зелёный) → planning
резолвнута, `gate planning passed sha=d750066c`; ран остановлен гейтом входа в
construction (нет Implementation Contract) — `blocked` (exit 20, LLM не потрачен).
Дальше — оператор: `advance-contract chg_t043_p04 … --approve`, затем `advance`
(construction). p03 — на human-гейте: CR `product-1#13` (CI 9/9) ждёт approving
review + merge → advance → `release`. p05 — той же схемой; p06–p10 (R2) — упираются
в блокер `solution`. Детали — `docs/t043-pilot-journal.md`.
