# Helm chart `dark-factory` (T031 / план T-042)

Chart устанавливает **API «тёмной фабрики»** (`factory api serve`, T035) в namespace `factory` как Deployment + Service (ClusterIP), с pre-install/pre-upgrade hook-Job миграций Alembic и опциональным локальным Ingress. **Console появится в T036** — chart рассчитан на расширение (второй деплой/сервис рядом с API).

PostgreSQL **не бандлится**: chart использует bootstrap-овский Service `factory-postgres` (T029) — это «зависимость PostgreSQL фабрики», а не установка БД. Все секреты создаются **вне git** — chart их не создаёт, только ссылается на existingSecret.

## Prerequisites

1. **Bootstrap T029 применён** (`deploy/bootstrap/`): namespace `factory`, PostgreSQL (`factory-postgres`), SA `factory-api`, квоты и NetworkPolicy.
2. **Секрет `factory-api-database` создан вне git** (паттерн deploy/events, T028):

   ```bash
   kubectl -n factory create secret generic factory-api-database \
     --from-literal=DATABASE_URL='postgresql+psycopg://factory:<пароль>@factory-postgres:5432/factory'
   ```

   Удобный вариант — взять пароль из bootstrap-секрета:

   ```bash
   kubectl -n factory create secret generic factory-api-database \
     --from-literal=DATABASE_URL="postgresql+psycopg://factory:$(kubectl -n factory get secret factory-postgres-credentials -o jsonpath='{.data.FACTORY_DB_PASSWORD}' | base64 -d)@factory-postgres:5432/factory"
   ```

   ⚠️ Пароль из bootstrap (`openssl rand -base64 24`) содержит `+/=` — в URL его нужно percent-кодировать (`python3 -c "import urllib.parse; print(urllib.parse.quote('<пароль>', safe=''))"`), иначе SQLAlchemy не распарсит `DATABASE_URL`.

3. Опционально — секрет с токенами авторизации (иначе mutating-запросы API **fail-closed**, GET доступны без токена — норма локального контура, ADR-009 §7):

   ```bash
   kubectl -n factory create secret generic factory-api-tokens \
     --from-literal=DARK_FACTORY_API_TOKENS='[{"token":"...","actor":"...","role":"...","scopes":[...]}]'
   ```

   и `--set auth.existingSecret=factory-api-tokens` при установке.

4. Опционально — секрет с GitHub App, чтобы консоль могла читать и переключать этапы CI (экран `/ci`, T059/ADR-027). Установке приложения нужен доступ **Variables: read/write** к репозиторию фабрики (создание App и ключа — `deploy/ci/README.md`):

   ```bash
   kubectl -n factory create secret generic factory-github-app \
     --from-literal=DARK_FACTORY_GITHUB_APP_ID='<app id>' \
     --from-literal=DARK_FACTORY_GITHUB_INSTALLATION_ID='<installation id>' \
     --from-file=DARK_FACTORY_GITHUB_APP_PRIVATE_KEY=app.pem
   ```

   и `--set ciToggles.existingSecret=factory-github-app --set ciToggles.repositorySlug=<owner>/<name>`. Без этого `/api/v1/ci/stages` отдаёт каталог этапов без состояния, а переключение отвечает 503 (fail-closed): консоль честно показывает, что контролы не сконфигурированы, вместо фиктивного выключателя.

## Установка

```bash
helm lint charts/dark-factory
helm lint charts/dark-factory -f charts/dark-factory/values-local.yaml

# рендер (без кластера)
helm template dark-factory charts/dark-factory
helm template dark-factory charts/dark-factory -f charts/dark-factory/values-local.yaml

# установка/обновление в ns factory (локальный профиль)
helm upgrade --install dark-factory charts/dark-factory -n factory \
  -f charts/dark-factory/values-local.yaml
```

## Ключевые values

| Ключ | Дефолт | Назначение |
|---|---|---|
| `image.repository` / `image.tag` | `ghcr.io/vadagama/dark-factory` / `bootstrap` | **Плейсхолдер до T033**: реальный immutable-образ соберёт OCI-build job; до того поды с этим тегом уйдут в `ImagePullBackOff`, если тега нет в registry |
| `api.replicas` | `1` | Больше нельзя: квота ns `factory` допускает ровно один под такого размера рядом с PG |
| `api.port` | `8000` | Порт API (CLI `factory api serve --host 0.0.0.0 --port 8000`) |
| `api.strategy` | `Recreate` | RollingUpdate при квоте приведёт к перекрытию подов → отказ планирования |
| `database.existingSecret` | `factory-api-database` | Секрет с полным `DATABASE_URL` в ключе `DATABASE_URL` (создаётся вне git) |
| `auth.existingSecret` | `""` | Опциональный секрет с `DARK_FACTORY_API_TOKENS`; пусто = mutating fail-closed |
| `ciToggles.existingSecret` | `""` | Опциональный секрет с `DARK_FACTORY_GITHUB_*` (App с доступом **Variables: read/write**); пусто = переключатели этапов CI не сконфигурированы (`/ci` без состояния) |
| `ciToggles.repositorySlug` | `""` | `owner/name` репозитория, чьи этапы CI переключает консоль; обязателен при заданном `ciToggles.existingSecret` |
| `migrations.enabled` / `migrations.command` | `true` / `["alembic","upgrade","head"]` | Hook-Job `pre-install,pre-upgrade`, weight 0, `before-hook-creation,hook-succeeded` |
| `ingress.enabled` / `className` / `host` | `false` / `nginx` / `factory.localhost` | В `values-local.yaml` включён; без контроллера объект инертен |
| `resources` | requests `100m/128Mi`, limits `500m/512Mi` | Вписывается в LimitRange (max 2CPU/2Gi) и квоту T029 |
| `probes.liveness.path` | `/openapi.json` | Живость процесса без обращения к БД |
| `probes.readiness.path` | `/api/v1/runs` | Готовность трафика = живая БД (200/500) |
| `serviceAccount.create` / `name` | `false` / `factory-api` | Переиспользуем bootstrap-SA — второй источник истины не создаём |

## Probes: почему именно так

- **liveness = `GET /openapi.json`** — служебный роут FastAPI отвечает 200 без обращения к БД. Если недоступен state store, процесс жив и не должен рестартовать: liveness по бизнес-эндпоинту дал бы бесполезный restart-loop при простое БД.
- **readiness = `GET /api/v1/runs`** — GET-роут возвращает 200 только при доступной БД и 500 в противном случае. Readiness снимает под с трафика ровно на время недоступности state store, и возвращает его автоматически после восстановления. В отдельном `/healthz` кода API нет (T035 слит, менять запрещено) — и он не нужен: открытый OpenAPI-документ выполняет роль liveness.

## Расчёт квоты ns `factory` (T029: requests 1 CPU / 2Gi, limits 2 CPU / 4Gi, pods 20)

| Компонент | requests | limits |
|---|---|---|
| PostgreSQL (bootstrap) | 250m / 256Mi | 1 CPU / 1Gi |
| API (chart) | 100m / 128Mi | 500m / 512Mi |
| Hook-Job миграций (жил кратковременно, параллельно с PG, до старта API) | 50m / 64Mi | 200m / 128Mi |
| **Пик** | **400m / 448Mi** | **1.7 CPU / 1.64Gi** |

Итого: requests ≤ 1 CPU / 2Gi ✅, limits ≤ 2 CPU / 4Gi ✅, подов ≤ 3 из 20 ✅. Значения проходят и LimitRange (min 25m/32Mi, max 2 CPU/2Gi). CronJob-ы bootstrap (`factory-outbox-dispatcher` 50m/64Mi, бэкапы) добавляют ≤ 100m/128Mi requests — запас сохраняется.

## Ограничения

- **Образ-плейсхолдер до T033** (`tag: bootstrap`): `helm upgrade --install` завершится, но поды будут в `ImagePullBackOff`, пока образа нет в registry.
- **Hook-Job миграций требует образа с alembic и каталогом `migrations/`** — требование зафиксировано для сборки образа в T033.
- **Ingress без контроллера инертен**: bootstrap контроллер не ставит; штатный локальный доступ — `kubectl -n factory port-forward svc/dark-factory 8000:8000`, затем `http://127.0.0.1:8000/docs`; живучесть туннелей (переживают перезапуск кластера и перезагрузку) — launchd-агенты из `deploy/local/port-forward/` (T-093).
- **Secret-ы вне git**: chart никогда не создаёт Secret (проверяется тестом `tests/test_chart_dark_factory.py`).
- Console (T036) будет собираться этим же chart'ом — схема values оставляет место под второй компонент.
