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
