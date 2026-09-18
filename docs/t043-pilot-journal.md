# Журнал T043 — E2E-пилот

Хронология отклонений, решений и наблюдений пилота. Формат: дата, инкремент,
событие → решение/следствие. Метрики прогонов инкремента 1 — здесь же.

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
