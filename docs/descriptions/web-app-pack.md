# Пак `packs/web-app` — engineering pack пилотного продукта (T-070, T041, T042)

Engineering pack для веб-приложений: шаблон продуктового репозитория (blueprint),
продуктовый CI, Helm chart приложения и тестовые конвенции. Стек зафиксирован
ADR-014 (React + Small UIKit), ADR-015 (границы репозиториев) и ADR-019
(GitHub-first CI): FastAPI + async SQLAlchemy + PostgreSQL на бэкенде,
React + Vite + TypeScript на фронтенде. Смена стека — новый ADR.

Пак — данные, не код (ADR-015 п.5): шаблоны копируются в новый продуктовый
репозиторий как есть и валидируются тестами фабрики
(`tests/test_packs_web_app.py`), поэтому дрейф шаблона ломает CI фабрики, а не
продукта. Продуктовый код не зависит от Python API фабрики (ADR-015 п.6) —
blueprint собирается и деплоится самостоятельным репозиторием.

## Структура

```
packs/web-app/
├── pack.yaml            # манифест: schema dark-factory.dev/pack/v1, id pack:web-app, version 0.2.0
├── CHANGELOG.md         # история версий пака (SemVer; версия = pack.yaml.version)
├── README.md            # назначение, применение, версионирование
├── rules.md             # правила пака + тестовые конвенции продукта
└── blueprint/           # скелет продуктового репозитория (копируется как есть)
    ├── README.md        # локальный запуск и деплой нового продукта
    ├── .github/workflows/ci.yml   # CI продукта: гейты + доверенная сборка образов
    ├── backend/         # FastAPI: src/app, /api/healthz, Alembic, pytest, uv.lock
    ├── frontend/        # React+Vite+TS; packages/ui = Small UIKit (@small/ui)
    └── deploy/
        ├── Dockerfile.backend / Dockerfile.frontend
        └── chart/       # Helm chart: backend + frontend + in-chart PostgreSQL
```

## Что внутри blueprint

- **Backend**: приложение-фабрика `create_app`, конфиг из env
  (pydantic-settings, `.env.example`), `GET /` — liveness без БД,
  `GET /api/healthz` — readiness с `SELECT 1` (503 при недоступности БД);
  Alembic со стартовой миграцией `0001` (таблица `notes` под модель);
  тесты: unit (httpx `ASGITransport` + stub-сессия, без pytest-asyncio) и
  integration с skip без `APP_TEST_DATABASE_URL`.
- **Frontend**: одна страница `HealthPage` (loading/ready/error, retry),
  типизированный клиент `fetchHealth`, тесты vitest + testing-library;
  `packages/ui` — реальный Small UIKit `@small/ui` (12 компонентов + 5
  паттернов, токены DTCG, Storybook, UI-гейты —
  `docs/descriptions/ui-kit.md`), подключенный скриптами `ui:*` и
  ESLint-политикой кита.
- **CI продукта**: ruff + mypy strict + pytest (с service-PostgreSQL) и
  eslint + tsc + vitest; гейты Small UIKit (`ui:lint`, `ui:typecheck`,
  `ui:test`, `ui:gates`, `ui:storybook:build` — джоба `frontend-ui-gates`,
  блокирует сборку frontend-образа); OCI-образы backend+frontend — multi-arch
  linux/amd64+arm64, тег только immutable `sha-<sha>`, публикация только из
  main (fork-PR и обычные ветки собирают, но не публикуют — CAN_PUSH),
  gitleaks и fail-closed trivy, digest-evidence артефактом; checkout всегда
  на итоговом SHA изменения (FR-009-паттерн). Без reusable-workflow механики
  фабрики — один самодостаточный файл (осознанное упрощение).
- **Dockerfile'ы**: pinned digest'ы базовых образов (те же, что у фабрики и
  console), non-root (65532 / 101), `uv sync --frozen` из uv.lock,
  `npm ci` из package-lock.json, без floating-тегов. Полный reproducible-build
  контракт фабрики (SOURCE_DATE_EPOCH, mtime-нормализация, apt-snapshot)
  намеренно не тянут — TD-012.
- **Helm chart**: Deployment backend + frontend, Service'ы, StatefulSet
  PostgreSQL 5Gi (обоснование in-chart против dependency-чарта — в шапке
  values.yaml: контроль digest-пиннинга, паритет с bootstrap T029), Job
  миграций `helm.sh/hook: pre-install,pre-upgrade` (`alembic upgrade head`),
  probes как у фабричного чарта (liveness без БД, readiness `/api/healthz`),
  readOnlyRootFilesystem + drop ALL, секреты только через `existingSecret`.
  Образы — digest-placeholder'ы `sha256:__*_IMAGE_DIGEST__` (паттерн
  `deploy/argocd/gitops-seed`), никаких `latest`. Ресурсы умещаются в квоту
  apps-dev (requests 500m/1Gi, limits 1 CPU/2Gi) включая пик pre-upgrade —
  стратегия Recreate, проверено тестом.

## Как применить

1. Скопировать `blueprint/` в новый продуктовый репозиторий; переименовать
   placeholder'ы (`example-product`, `example-org`). Small UIKit уже внутри
   blueprint (`frontend/packages/ui`) — при обновлении кита брать актуальную
   копию из `packs/ui/blueprint/ui/` (паритет проверяют тесты фабрики).
2. CI продукта на main публикует `sha-<sha>` в ghcr; GitOps-MR подставляет
   digest в chart values (пример формы MR —
   `deploy/argocd/gitops-seed/examples/gitops-mr-digest-change.md`).
3. Релиз: `helm upgrade --install example-product deploy/chart -n apps-dev -f
   deploy/chart/values-apps-dev.yaml` (через Argo CD); секрет
   `example-product-db` создаётся вне git.

## Версионирование

SemVer в `pack.yaml.version` = запись в `CHANGELOG.md`; изменения шаблонов
требуют bump версии и записи в CHANGELOG (правила паков —
`packs/product-baseline/README.md`, ADR-015 п.5). Валидация консистентности —
тесты фабрики.
