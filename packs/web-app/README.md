# Пак `web-app`

Engineering pack пилотного продукта (T041, исторический T-070): версионированный
шаблон продуктового репозитория — blueprint (React + Small UIKit + FastAPI +
PostgreSQL), продуктовый CI, Helm chart приложения и тестовые конвенции. Пак —
данные, не код: шаблоны копируются в новый продуктовый репозиторий как есть,
а «уплывший» шаблон ловится тестами фабрики (`tests/test_packs_web_app.py`),
не падением в продукте.

UI-слой — реальный Small UIKit из пака `packs/ui` (T042, ADR-014): workspace-пак
`@small/ui` в blueprint содержит 12 компонентов и 5 паттернов поверх Radix,
DTCG-токены, Storybook как исполняемую спеку и UI-гейты (UIKit-policy ESLint,
stylelint, axe, Storybook-сборка в продуктовом CI; visual regression —
закреплён за фабричным CI). Console фабрики UI-кит продуктов не определяет.

## Структура

```text
web-app/
├── README.md          # этот файл
├── pack.yaml          # манифест: schema, id, SemVer, состав
├── CHANGELOG.md       # история версий пака
├── rules.md           # правила пака + тестовые конвенции продукта
└── blueprint/         # скелет продуктового репозитория (копируется как есть)
    ├── README.md              # как запустить локально и как деплоится
    ├── .github/workflows/ci.yml  # CI продукта: гейты + публикация образов
    ├── backend/               # FastAPI: src-layout, Alembic, async SQLAlchemy
    ├── frontend/              # React + Vite + TS, воркспейс-пак @small/ui (Small UIKit из packs/ui)
    └── deploy/                # Dockerfile.backend/.frontend + Helm chart
```

## Как применить

1. Скопировать `blueprint/` в корень нового продуктового репозитория.
2. Заменить плейсхолдеры (полный список — `blueprint/README.md`): имя продукта
   (`example-product`), registry-владелец (`example-org`), при желании — имя
   Python-пакета `app`.
3. Создать секрет БД вне git — команда в комментарии `deploy/chart/values.yaml`
   (секрет несёт и `POSTGRES_PASSWORD` для StatefulSet, и `DATABASE_URL` для
   backend и миграций).
4. Прибить digest'ы образов: после первой сборки на `main` GitOps-MR заменяет
   `sha256:__*_IMAGE_DIGEST__` в values (форма изменения — fixture
   `deploy/argocd/gitops-seed/examples/gitops-mr-digest-change.md` фабричного
   репозитория); digest PostgreSQL разрешается один раз вручную через
   `docker buildx imagetools inspect postgres:16.4`.
5. Дальнейший релизный цикл — ADR-010/ADR-011: merge продуктового MR (человек)
   → образы `sha-<sha>` → GitOps-MR с digest → Argo CD автоматически синкает
   `apps-dev`; миграции — pre-upgrade hook Job чарта.

## Версионирование

- `pack.yaml.version` и верхняя запись `CHANGELOG.md` меняются вместе, SemVer:
  патч — правки шаблонов без изменения контрактов; минор — новые возможности
  шаблона; мажор — несовместимые изменения структуры blueprint.
- Связи между фабрикой и продуктом фиксируются идентификаторами, а не ветками:
  `pack_name`/`pack_version` рядом с `blueprint_version`/`product_commit`
  в run-записях (ADR-015 п.5).
- Продукты, созданные из версии N, не обязаны автоматически обновляться на
  N+1: обновление шаблона в существующем продукте — отдельное изменение
  продуктового репозитория.
