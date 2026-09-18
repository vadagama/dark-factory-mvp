# Журнал T043 — E2E-пилот

Хронология отклонений, решений и наблюдений пилота. Формат: дата, инкремент,
событие → решение/следствие. Метрики прогонов инкремента 1 — здесь же.

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
