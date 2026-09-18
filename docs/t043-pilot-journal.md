# Журнал T043 — E2E-пилот

Хронология отклонений, решений и наблюдений пилота. Формат: дата, инкремент,
событие → решение/следствие. Метрики прогонов инкремента 1 — здесь же.

## 2026-09-18 — Инкремент 1: гейт входа в construction — нет пути прикрепления Implementation Contract (открыт)

- **Живой факт: advance p02 после резолва planning (зелёный CI на continuation-PR product-1#10,
  PLANNING PASSED на head `c96a8b7c`) остановился гейтом входа в construction:**
  `no implementation contract attached to the run; implementation must not start without
  one (ADR-018 p.3)` — exit 20, честная блокировка по T-016 DoD (политика работает как
  задумано). Проверено по БД: ни у одного из 10 ранов `chg_t043_p*` контракт не прикреплён.
- **Причина — пробел контура инкремента 1, не дефект политики**: `run advance --change-id`
  создаёт ран всегда без контракта (docs/descriptions/cli.md §3.2: «контракт не утверждён»),
  `create_run` идемпотентен и контракт/бюджет существующего рана не трогает
  (state/run_store.py), API контракта не принимает (contracts/api.md), CLI-флага нет.
  T023 (T-016) реализовала модель и политику, wiring «одобренный человеком контракт → ран»
  не построен — территория человеческих решений/reconciler (та же открытая тема, что
  approvals).
- **Что это значит для пилота**: все 10 ранов встанут в этот же блокер перед construction.
  До решения — дальше planning/CI гонять можно (p03–p10), construction не стартует.
- **Варианты решения (выбор — оператор/продукт, значимое решение → согласование/ADR):**
  1. CLI `run advance --contract-json <файл|->` (+ `--approve-contract`?) — контракт
     передаётся при первом advance, человек подтверждает; идемпотентность `create_run`
     требует отдельного `attach_contract`-пути для уже созданного рана.
  2. API-эндпоинт `POST /changes/{id}/contract` (role operator, approvals:write) —
     operator-approve контракта операторским токеном; reconciler-склейка с драйвером.
  3. Для пилота — минимальный скрипт, записывающий контракт прямо в `execution` (обход
     контура, грязный; только как временный мост с фиксацией в журнале).
- **Статус**: решён оператором — **вариант 1**, реализован (см. следующую запись).
  Блокер зафиксирован до реализации; прогресс пилота продолжался стадиями, не
  требующими контракта (planning-агенты p03–p10 + CI + review-путь p02).

## 2026-09-18 — Инкремент 1: прикрепление Implementation Contract через `run advance --contract-json` (вариант 1, закрыт)

- **Реализация выбранного оператором варианта 1** — CLI-путь прикрепления контракта:
  - `RunStore.attach_contract(run_id, contract)` (state/run_store.py): запуск без
    контракта записывает переданный; идентичный — no-op; **другой** — отказ
    `ContractConflictError` (новый подкласс `StateError` в state/repositories.py):
    утверждённая граница работающего изменения не подменяется (ADR-018 p.3).
    Идемпотентность `create_run` не тронута.
  - `run advance --contract-json <path|->` (+ `--approve-contract`): контракт читается
    (`-` — stdin), валидируется pydantic-схемой **до** обращения к store (битый
    файл/JSON/схема → exit 2, ничего не записано) и прикрепляется к резолвнутому
    запуску в той же транзакции, что и решение стадии, — обе ветки (`--change-id`:
    контракт уходит в `create_run` + `attach_contract`; `--run-id`: `attach_contract`).
  - `--approve-contract` проставляет human-утверждение (`approved_by=Role.PRODUCT`,
    `decided_at=now(UTC)`) на загруженный контракт; флаг без `--contract-json` — exit 2;
    контракт с уже стоящим approval вместе с флагом — двусмысленность, exit 2.
    Утверждение — решение человека (ADR-011): агент флаг не ставит никогда.
  - Контракт в `run status --json` виден (доменный `ChangeRun` несёт
    `implementation_contract`).
- **Документация**: contract cli.md (usage + блок Contract-опций),
  docs/descriptions/cli.md (таблица §3 + §3.2, включая разбор `--contract-json`).
- **Валидация**: юнит-тесты attach-семантики и CLI-ошибок (9 новых, test_cli_runner.py,
  test_cli_parser.py), integration: attach идемпотентен/swap-free на PostgreSQL +
  сквозной «advance с контрактом на существующем ране → контракт в run status --json»
  (test_runner_advance.py). Полный набор: 1838 passed / 51 skipped (PostgreSQL 16 в
  одноразовом docker-контейнере), ruff/mypy чисто.
- **Дальше по пилоту**: черновики контрактов p02–p10 (deploy/local/pilot/contracts/),
  утверждение оператором, advance p02–p05 (construction), затем тема маппинга
  human-гейтов в `ScmFactsProvider` для UI-гейта R1 и control point `solution` R2.

## 2026-09-18 — Инкремент 1: публикация после смерженного спек-CR (фикс cr-after-merge)

- **Живой дефект: planning p02 застряла в waiting навсегда.** Оператор смержил спек-CR
  product-1#1 (решение выражено мержем, ADR-011 — резолв human-гейта specification по
  9ed3d9b). Planning-агент опубликовал ревизию `c96a8b7c` в ветку `factory/chg_t043_p02`,
  но `_publish` нашёл по change-маркеру в теле **смерженный** PR #1 (`find_existing`
  сканирует все PR, новейшие первыми) и переиспользовал его вместо открытия нового.
  Пуш в ветку смерженного PR не создаёт PR и не триггерит CI → на финальном SHA нет
  check-runs (`pipelines.status` → `queued`) → наблюдение `merged=True`, а
  `gate_resolved` для machine-gated стадии резолвится только зелёным пайплайном при
  открытом CR. Тупик: реплаи чекпоинта без шанса на резолв.
- **Причина**: переиспользование CR в `_publish` не различало «CR открыт — несёт
  коммиты дальше» и «CR смержен/закрыт — несущей больше нет». Merge-as-decision —
  официально поддержанный путь (9ed3d9b), так что дефект системный, не частность пилота.
- **Фикс** (ветка `fix/t-043-publish-continues-after-merge`): `_publish` переиспользует
  CR только в статусе `open`; иначе открывает свежий CR той же ветки (`open`
  идемпотентен по head-ветке, маркер change-id в теле сохраняет FR-011 cold lookup —
  новейший PR с маркером находится первым). Тесты: смерженный CR → executor planning
  открывает CR #2, wait-action называет его, cold lookup резолвит #2; открытый CR
  переиспользуется без дубля.
- **Проверки**: pytest 1804 passed/72 skipped (было 1802/72, +2), ruff check+format
  чисто, mypy чисто (176 файлов).
- **Следствие для прогона**: p02 — planning-попытка уже запаркована на merged CR #1,
  чекпоинт неизменяем (ADR-006 p.8), фикс на неё не действует ретроактивно → оператор
  открывает continuation-PR с change-маркером в теле (1 ручное вмешательство, считается
  в метриках SC-008); далее advance резолвит planning зелёным CI на PR. p03–p10 —
  planning ещё не стартовали: их публикации откроют continuation-CR автоматически.
- **Открытая тема (из предыдущей записи, актуальна):** R2-раны p06–p10 — контрольная
  точка `solution` не закрывается наблюдаемым review'ем (маппинг
  `ScmFactsProvider._approvals` на `Gate.REVIEW`); до её решения planning R2-ранов
  после зелёного CI будет парковаться на human-гейте без пути резолва.

## 2026-09-18 — Инкремент 1: планирование паркуется на CI (фикс planning → wait_for_ci)

- **Живой дефект: advance p02 из planning падал `InvalidFlowTransition: Flow transition planning -> wait_for_ci is not allowed` (exit 1).** LLM-вызов успевал пройти, commit + change request публиковались идемпотентно *до* крэша, но решение не персистилось — валидация перехода в `apply_result` стоит раньше записи. Каждый повторный advance перезапускал LLM и падал снова (выгорание бюджета при нулевом прогрессе). До мерджа фикса planning-стадии не advance'ить.
- **Причина**: `AgentStageExecutor._waiting` выбирает wait-действие по характеру гейтов стадии (`required_gates − HUMAN_GATES`); для planning `{planning}` — machine-гейт → `WaitForCIAction`, но `FLOW_TRANSITIONS[Stage.PLANNING]` не содержал `wait_for_ci` (таблица росла от construction; specification не спотыкается, т.к. её гейты чисто human). Тот же класс дефекта, что помечен в коде как «found in T-043 increment 1».
- **Фикс** (ветка `fix/t-043-planning-wait-for-ci`): `wait_for_ci` в `FLOW_TRANSITIONS[Stage.PLANNING]` + комментарий-инвариант таблицы (planning/construction публикуют работу одним коммитом + CR — machine-гейты гоняются в CI по финальному SHA, FR-009; specification чисто human). Тесты: executor planning → `WaitForCIAction` с CR и producer `product`; runner-регрессия парковки свежей planning-попытки на `wait_for_ci`; runner-резолв ожидающего planning зелёным пайплайном → SUCCESS, `execute_stage` → construction, `[(Gate.PLANNING, PASSED, head_sha)]`; exhaustive-траверсал пар таблицы подхватывает новую пару автоматически.
- **Проверки**: pytest 1802 passed/72 skipped (HEAD до фикса — 1799/72, +3 новых), ruff check+format чисто, mypy чисто (176 файлов).
- **Открытые темы (наблюдения пилота, фикс отдельно):**
  - **R2-раны (p06–p10): контрольная точка `solution` не закрывается наблюдаемым review'ем.** После зелёного CI на plan-CR выход из planning требует human-аппрува точки `solution` (PLANNING-гейт), но `ScmFactsProvider._approvals` мапит review'и planning-стадии на `Gate.REVIEW` (фолбэк: у planning нет базового human-гейта) — точка не может закрыться через наблюдение. Нужен путь operator-decision для planning-гейта или правка маппинга провайдера.
  - **Эффективный риск-класс до approved-контракта — R0/фолбэк классификатора.** Поэтому R2-раны прошли specification без контрольной точки `problem` (обязательства R2+ считаются от эффективного класса, а он ещё не поднят контрактом).
- **Инфра-замечание (повтор)**: инструменты правки сессии дважды за сессию отдали/записали устаревшее состояние файла (перезапись буфера редактора поверх `git checkout`; агрессивный fuzzy-match правок, портящий соседние блоки). Рабочий паттерн подтверждён: восстановление из HEAD + python-патчер с якорем «ровно одно вхождение» + сверка `git diff` (только добавления) до коммита.

## 2026-09-18 — Инкремент 1: резолюция human-гейтов (PR #88), оператор смержил спеки

- **Оператор выразил решения мержем всех 9 спек-CR (product-1#1–#9, без формальных review).** На момент проверки все PR `MERGED`, CI зелёный. Механизм такое решение не съедал — три дефекта доводки (PR #88, ветка `fix/t-043-human-gate-resolution`):
  1. `gate_resolved` учитывал `merged` только для review-стадии — чисто human-gated стадия с мерженым CR оставалась в waiting навсегда (живой блокер). Теперь merge CR резолвит human-gated стадию (наблюдаемый human merge — сильнейшая форма решения, ADR-011 p.2).
  2. `build_gate_resolution` гнал резолвнутую human-gated стадию через `_resolve_machine_gated`: machine-набор пуст → `unsatisfied=[specification]` → честный BLOCKED с нерелевантной FR-009-причиной. Даже review-approval ушёл бы в BLOCKED. Выделен `_resolve_human_gated`: SUCCESS + human-гейт PASSED, забинженный к observed head SHA (ADR-009 p.7).
  3. Control points R2+ (`flow._escalation_reason`) биндинг к `result.input_revision` (base-коммит стадии), а approval забинжен к head CR — версии никогда не совпадают → p06–p10 (R2) остановились бы «risk class R2 obligations are not met». Теперь точка биндится к SHA, на котором прошёл human-гейт стадии (фолбэк — input_revision, прежнее поведение); unbound approval больше не закрывает точку при зафиксированном SHA (усиление по ADR-009 p.7, тесты сьюта flow-policy обновлены под bound-approvals).
- **Проверки**: pytest 1799 passed/72 skipped (+7 новых тестов: truth-table резолва, builder для approval/merge, интеграция раннера merged/approval/replay, control-point binding), ruff check+format чисто, mypy чисто (176 файлов).
- **След**: advance p02–p10 → раны в planning (стадия pending; следующий advance запускает агента планирования — LLM-расход). p01 — пере-intake после подтверждения резолва p02. Прим.: изменения доков в PR #88 — только журнал/план.
- **Инфра-замечание**: read-инструменты сессии отдавали устаревший снимок файлов (конфликт с checkout'ом веток) — правки вносились python-патчерами с якорями «ровно одно вхождение», диф сверялся через `git diff` перед коммитом. Кандидат в tech-dept: надёжность инструментов чтения агента не относится к продукту, но паттерн «якорь + проверка дифа» стоит держать в AR-памяти.

## 2026-09-18 — Инкремент 1: доводка драйвера (PR #85/#86), матрица на гейтах

- **Tool-ошибки роняли попытку агента (fixed, PR #86).** Модель читала ещё не созданный файл → `read_file` → `collect_evidence` поднимал `KeyError` → harness-адаптер переводил исключение в `ok=False` → попытка целиком `blocked` («harness did not produce a result»). p04 пережила 2 такие попытки (3–4), p08/p09 — застряли на attempt 1. → `_model_facing`-декоратор в `WorkspaceTools`: модель-корректируемые сбои (`KeyError`, `UnsafeWorkspacePath`, пустые argv/pattern) возвращаются модели текстом `error: ...` — по собственной политике модуля; неожиданные исключения продолжают пробрасываться. `resolve_path` не изменён — изоляция workspace прежняя. Evidence: p08/p09 после фикса с первой попытки опубликовали спеки (product-1#8, product-1#9).
- **`aclose` на чужом event loop (fixed, PR #86).** Sync-швы драйвера живут по одному `asyncio.run`; entry point закрывает runtime в новом loop'е — `GitHubClient.aclose` делал await пула, привязанного к уже закрытому loop'у → `RuntimeError` → exit 1 после успешно сделанной работы (p04: попытка опубликовала product-1#7 и процесс упал на aclose). → Клиент чужого loop'а выбрасывается на GC, зеркально `_client_for_loop`.
- **`chg_t043_p01` приколот к битой ревизии — до пере-intake.** Ран создан до фикса ключа первой стадии (PR #85): первая стадия приколота к snapshot-digest `d507b6f…`, которого нет в git → `WorkspaceError` на каждом advance (attempt 3). Дефект данных старого рана, не кода; снимается пере-intake p01 после решения оператора по гейту p02 (план).
- **Матрица на human-гейтах `specification` (9/10).** Спеки опубликованы: p02→product-1#1, p03→#2, p05→#3, p06→#4, p07→#5, p10→#6, p04→#7, p08→#8, p09→#9; все `waiting` на human-гейте (ADR-018: спека — человеческое решение). Решения оператора — следующий шаг инкремента; агенты гейты не подают (ADR-011).

## 2026-09-18 — Инкремент 0: bootstrap пилотного продукта

- **CI run 1 (35299665450, `940b3ae`) — Backend pytest + mypy упали.**
  `app.main` собирал модульный `app = create_app()` → `ValidationError`
  (`DATABASE_URL` обязателен) при импорте; mypy strict поверх pydantic
  `dataclass_transform` не пропускал `Settings(_env_file=None)`.
  → Каноничный фикс в паке (`packs/web-app/`, bump 0.2.1), зеркало в продукт:
  фабрика `create_app` + uvicorn `--factory`; изоляция тестов на уровне
  `model_config` + `# type: ignore[call-arg]` на no-arg `Settings()`.
  Урок: шаблоны пака валидируются фабрикой структурно, но CI продукта —
  первый настоящий исполнитель; дефекты шаблона обязаны чиниться в паке
  (иначе bootstrap следующего продукта воспроизведёт баг).

- **CI run 2 (35300089368, `acd7fb7`) — Backend OCI image упал на trivy.**
  3 HIGH в `libpcre2-8-0 10.42-1` (debian 12.15 в пиннутой базе
  `python:3.12-slim-bookworm@sha256:782412e8…`); тег базы не пересобран
  upstream, т.е. bump digest не помогает. → `apt-get upgrade -y` в
  runtime-стадии (паттерн фабричного образа «fresh mirror serves patched
  packages»; пер-пакетный список не успевает за новыми advisory).

- **CI run 3 (35300427112, `911fa98`) — Backend OCI image упал на trivy
  снова, набор уязвимостей другой.** arm64-скан: `pcre2 10.32-3.el8_6`
  (AlmaLinux RPM!) ×6 CVE, при этом amd64 — 0. Расследование локально
  (сборка arm64 + trivy + SBOM): psycopg-binary **3.3.5** вшивает в колесо
  `dist-info/sboms/auditwheel.cdx.json` (новое в 3.3.x) со списком
  bundled-библиотек build-окружения; arm64-колесо бандлит `libpcre2-8`
  от AlmaLinux 8.6 с 6 CVE. Trivy читает embedded SBOM и смешивает
  wheel-компоненты с OS-сканом (PkgType «debian», FixedVersion от
  debian-трекера к rpm-purl — очевидный mismatch). amd64-колесо бандлит
  pcre1 (el7) — CVE в базе нет, поэтому прошло.
  → Пин `psycopg[binary]>=3.2,<3.3` (3.2.x колёса без SBOM). Пин снять,
  когда psycopg обновит bundled-библиотеки или trivy перестанет читать
  wheel-SBOM как OS-пакеты. Кандидат в tech-dept (следить за psycopg 3.3.x).

- **CI run 4 (35301270578, `2a80607`) — 9/9 джоб зелёные.** Digest'ы:
  backend `sha256:b5d96345…`, frontend `sha256:e12eda99…`.

- **GitOps `dark-factory-gitops#5` смержен admin-override** (TD-026: на
  GitHub Free enforce_admins недоступен, требование review владелец
  обходит `--admin`; правило «merge — человек» для gitops-репо пилота не
  вводилось, но ветка main защищена). Учесть при формализации
  release-политики: admin-merge — это то же «ручное решение человека».

- **Квота `apps-dev`: seed-fixture удалена до деплоя продукта.** Продукт
  занимает 1 CPU limits (пик с миграционным хуком), busybox-фixture (50m)
  не влезает. `envs/dev/pilot/` удалён из gitops, Application `pilot-dev`
  удалён из Argo (root app с `prune:false` не удаляет ресурсы автоматически —
  deploy/pilot удалён вручную). DoD fixture (US5/T-044) ранее подтверждён
  (TD-020), потеря не критична.

- **БЛОКЕР: ErrImagePull `unauthorized` — ghcr-пакеты приватные.** Пакеты,
  запушенные GITHUB_TOKEN приватного репо, создаются приватными; kubelet
  docker-desktop тянет анонимно. Сменить видимость API нельзя
  (`PATCH /user/packages/container/…` не существует; локальный gh-токен без
  `read:packages`). → Решение пилота: сделать оба пакета публичными в UI
  (toy digest-pinned образы). Альтернатива на будущее для приватных
  продуктов: imagePullSecret (PAT с `read:packages`) + поддержка
  `imagePullSecrets` в чарте пака.

- Наблюдение: фабричный образ (`deploy/ci/image/Dockerfile`) собран на той же
  базе `python:3.12-slim-bookworm@sha256:782412e8…`, но уже содержит
  `apt-get upgrade -y` из snapshot.debian.org (20260906T000000Z) — дефект
  «старая база» на фабричный образ не распространяется, если фикс
  `libpcre2-8-0 10.42-1+deb12u1` вошёл в snapshot до 2026-09-06
  (проверить при первом релизе фабричного образа из ghcr, T-045/T-052).

- Наблюдение: проброс PostgreSQL для CLI (`127.0.0.1:5432`) не настроен —
  нужен для инкремента 1 (intake/advance из CLI); делать внутри скрипта.

## 2026-09-18 — Инкремент 0: докат деплоя и smoke (инкремент закрыт)

- **Миграционный хук: first-install deadlock на pre-фазах.** Хук шёл в
  `pre-install,pre-upgrade` → Argo CD мапит pre-* хуки в фазу PreSync, которая
  выполняется ДО применения ресурсов чарта — на первой установке хук не может
  разрешить `<release>-postgres` (DNS: `Name or service not known`, 26+
  попыток). → Каноничный фикс в паке (web-app 0.2.1, коммиты `2ab3709`,
  `03d30da`): хук — только post-фазы (`post-install,post-upgrade`), обёртка
  wait-for-database (30×5s внутри `activeDeadlineSeconds: 300`),
  `hook-delete-policy: before-hook-creation,hook-succeeded`. Следствие
  зафиксировано в шаблоне: на апгрейдах миграции идут ПОСЛЕ деплоя нового
  backend — schema-изменения обязаны быть backward-compatible
  (expand/contract). Упавший install/PostSync ресурсы сохраняет — следующий
  прогон Argo/helm сходится (хук добивает миграции на живой БД).

- **Блокер ErrImagePull снят оператором**: пакеты
  `dark-factory-product-1-{backend,frontend}` сделаны публичными в GitHub UI
  (решение из предыдущей записи, TD-029). Argo докатил деплой: поды backend/
  frontend/postgres Running, миграционный хук отработал (retry поглотил и
  окно инициализации initdb, и окно применения ресурсов), job убран по
  hook-delete-policy, Application `product-1-dev` — Synced/Healthy
  (gitops-ревизия `b7da87f`).

- **Smoke инкремента 0 зелёный**: `GET /api/healthz` →
  `{"status":"ok","database":"ok"}`, `GET /` → HTTP 200 (frontend HTML,
  заголовок `dark-factory-product-1`). DoD T-070 «blueprint разворачивается
  в apps-dev» подтверждён живым деплоем; TD-010 и TD-029 закрыты. Живой цикл
  «образы CI → GitOps-MR с digest → Argo → apps-dev → smoke» пройден целиком.

- Наблюдение на будущее: в ходе цикла подведения PVC postgres пересоздавался
  (новый claim) — reinstall релиза теряет данные БД; с инкремента 1 релиз
  обновлять только upgrade'ом (или откатом revert-коммита в gitops, TD-020),
  за PVC следить при каждом вмешательстве.

## 2026-09-18 — Инкремент 1: прогон до human-гейта (инкремент в работе)

Состояние: окружение инкремента 1 поднято (pg-forward 55432, intake 10
изменений `chg_t043_p01…p10`), e2e-цикл агентной стадии пройден живым
прогоном `chg_t043_p02` — от intake до waiting на human-гейте, с
опубликованным коммитом и открытым change request. Ниже — найденные и
закрытые дефекты (все вскрыты только живым прогоном; юнит-контур их не
видел).

- **run-creation keying (критический, закрыт).** `factory run advance
  --change-id` создавал run с `input_revision` = digest снапшота change'а
  (S1), и первая stage-строка наследовала его — резолвер `ScmRevision`
  (ADR-006 p.4) не вызывался вовсе, поэтому `_mint` пытался сминтить
  worktree на SHA, которого не существует в git → WorkspaceError на каждой
  попытке. Юнит-тест резолвера выставлял `stages[0].input_revision = None`
  вручную и не покрывал путь создания run'а CLI. Фикс:
  `RunStore.create_run(..., initial_stage_revision=...)` — run-id остаётся
  от digest (контракт cli.md «one run per snapshot»), первая stage-строка
  ключится SCM-ревизией (`cli.runner._resolve_run` вызывает резолвер);
  e2e-регрессионный тест — `tests/integration/test_runner_advance.py`.

- **GitHub 422 для несуществующего ref (закрыт).** Контракт
  `RepositoryPort` — «missing ref = KeyError» (404), но GitHub отвечает
  **422** на `GET /commits/{ref}` для ref'а, который не резолвится
  (несуществующая тасковая ветка — нормальное состояние до первой стадии).
  Адаптеры (`repository.get_revision`, `repository._head`, `ci._resolve_ref`)
  теперь мапят 422 как absent; контрактный эмулятор воспроизводит 422,
  добавлен контрактный тест `test_get_revision_of_a_missing_ref_is_a_keyerror`.

- **httpx2-клиент GitHub переживает смену event loop (закрыт).** Sync-швы
  драйвера (`ScmRevision`, `ScmFactsProvider`, executor) работают через
  `asyncio.run` — по одному короткому loop'у на вызов; keep-alive пул
  httpx2 привязан к loop'у и падал на следующем вызове
  (`RuntimeError: Event loop is closed` — хаотично: то KeyError, то
  обрыв LLM-вызова на полпути). `GitHubClient` держит клиент **per loop**:
  запрос на новом loop'е пересоздаёт клиент, пул мёртвого loop'а
  сбрасывается (ADR-025-совместимо, binding-слой).

- **product-профиль без write_file (закрыт).** Скилл `spec-authoring`
  требует записать спеку в layout репо, а `PRODUCT_PROFILE.tools` не
  содержал `write_file` — агент писал спеку в чат, `_publish` видел пустую
  дельту. Добавлен `write_file` (version 1.0.0 → 1.0.1), meta-снапшот
  консоли перегенерирован.

- **wait-действие агентной стадии (закрыт).** `AgentStageExecutor._waiting`
  всегда возвращал `WaitForCIAction`, но `FLOW_TRANSITIONS` разрешает
  `wait_for_ci` только construction/review/release — спецификация с
  публикацией падала `InvalidFlowTransition` (exit 1). Теперь действие
  выбирается по характеру гейтов стадии (`rules.gates`): только human-гейты
  → `wait_for_input` (парковка на человека), есть machine-гейты →
  `wait_for_ci`.

- **human-гейт не резолвился пайплайном (закрыт).** `gate_resolved`
  считал зелёный пайплайн резолюцией для любой не-review стадии — спека
  «продет» без человека, а `_resolve_machine_gated` честно блокировался
  («cannot be attributed to a head SHA»). Новая семантика: стадия с
  чисто-human-гейтами резолвится **только** version-bound human approval
  (наблюдаемым review'ем change request'а); пайплайн решает только стадии
  с machine-гейтами. `ScmFactsProvider` теперь привязывает approvals к
  human-гейту стадии (specification → Gate.SPECIFICATION, review → REVIEW).
  `gate_resolved(observation, stage, route)` — route передают оба call-site
  драйвера.

- **Наблюдаемость харнесса (закрыт).** Падение `agent.run` глушилось в
  «harness did not produce a result» без указания типа — отладка вслепую.
  `PydanticAIHarness` пишет тип исключения в лог процесса (не в reason —
  контракт ADR-009 сохранён), плюс диагностический
  `DARK_FACTORY_HARNESS_TRACEBACK=1` (локальный stderr, по умолчанию выкл).

- **psycopg pin (закрыт, из bring-up).** `psycopg[binary]>=3.2,<3.3` —
  3.3.x без libpq не работает локально на macOS.

Живой статус прогона: `chg_t043_p02` — waiting (human-гейт specification,
change request `vadagama/dark-factory-product-1#1`, CI на PR зелёный,
head `3b56f569`). Решение — оператора (ADR-011). `chg_t043_p01` — run
создан до фикса keying'а, операционно приколот к digest-ревизии (retry
сохраняет операционную идентичность, ADR-006 p.7); будет пере-intake под
новым id после закрытия human-гейта p02. Остальные 8 изменений — не
запускались, ждут разблокировки контура гейтов.

Известные открытые темы (не блокируют дальнейший прогон, фиксируются):
- **aclose после закрытого loop'а** — `Runtime.entrypoint` зовёт
  `asyncio.run(runtime.aclose())` в finally; если ресурсы родились на
  закрытом `asyncio.run`-loop'е швов, aclose падает с шумным traceback
  (после основного результата; exit-код в CLI-ветке не искажает — но
  traceback грязнит stderr). Кандидат: общий loop для швов в ADR-025.
- **API-approvals не видны драйверу** — operator-approve через
  `POST /changes/{id}/approvals` пишет Decision в store, но драйвер читает
  human-факты только из observation (GitHub reviews). Для пилота путь
  аппрува — review на change request'е; склейку decision-store ↔ драйвер
  делать отдельным куском (reconciler-территория, ADR-006).
- **advance-all паттерн ERROR(1)** — при прогоне p02 через advance-all
  retry-циклы агента (blocked → attempt N) выгорают попытки LLM; перед
  следующим advance-all прогнать аккуратно с паузами и наблюдением.
