# Bootstrap локального Kubernetes (T029, ADR-010)

Воспроизводимый bootstrap локальной платформы «тёмной фабрики» на Docker Desktop Kubernetes (macOS, MacBook 24GB): namespaces, квоты, service accounts, базовые NetworkPolicy, PostgreSQL фабрики с бэкапами и набор проверок (негативные egress-тесты, capacity smoke, проверка учётных данных GitHub App). GitOps/Argo CD устанавливается позже (T032), здесь не ставится.

## Требования

- macOS + Docker Desktop (проверено на 4.69.0 / движок 29.4.0), `kubectl`, `jq`, `openssl`, `bash`.
- Ресурсы Docker Desktop: **10–12GB RAM** на всю VM (Settings → Resources → Memory), 4+ CPU. Профиль зафиксирован в ADR-010: concurrency=1, повышение — только после подтверждённого запаса (capacity smoke).
- **Включить Kubernetes**: Docker Desktop → Settings → Kubernetes → «Enable Kubernetes» → Apply & Restart, дождаться зелёного индикатора (первый старт — несколько минут). Проверка: `kubectl --context docker-desktop cluster-info`.
- ARM64: все образы мульти-арх (`postgres:16.4`, `busybox:1.37`, `curlimages/curl:8.11.1`), версии запинены без `:latest`.

### Лицензия Docker Desktop

Коммерческое использование Docker Desktop требует платной подписки. Проверка в bootstrap — **best-effort**: скрипт читает `~/Library/Group Containers/group.com.docker/settings-store.json` (наличие `LicenseTermsVersion` = условия приняты) и всегда печатает предупреждение с необходимостью самостоятельной проверки: https://www.docker.com/legal/subscription/. Enforce-механизма нет; флаг `--strict-license` делает невозможность подтверждения лицензии блокирующей ошибкой.

## Использование

```bash
./bootstrap.sh                       # полный прогон: применить всё + проверки
./bootstrap.sh --only-license-check  # только проверка лицензии
./bootstrap.sh --skip-capacity-smoke --skip-github-check
./bootstrap.sh --context <имя>       # другой kubectl-контекст (по умолчанию docker-desktop)
./teardown.sh                        # удалить всё (см. ниже)
```

Скрипт **идемпотентен**: повторный запуск безвреден (`kubectl apply`, секрет не пересоздаётся). Kubernetes должен быть включён заранее; если API кластера недоступен, скрипт выводит точную инструкцию по включению и завершается с кодом 1 (настройки Docker Desktop он не меняет).

| Флаг | Действие |
|---|---|
| `--context NAME` | kubectl-контекст (по умолчанию `docker-desktop`, или env `KUBECTL_CTX`) |
| `--only-license-check` | только проверка лицензии и выход |
| `--strict-license` | fail, если лицензия не подтверждена (по умолчанию warn-only) |
| `--skip-capacity-smoke` | пропустить capacity smoke |
| `--skip-github-check` | пропустить проверку GitHub App |
| `--skip-egress-tests` | пропустить негативные egress-тесты |
| `--skip-restore-test` | пропустить автоматический restore-тест |

## Что создаётся

| Объект | Где | Назначение |
|---|---|---|
| Namespaces `argocd`, `factory`, `ci`, `factory-runs`, `apps-dev` | — | платформенные namespace с label `app.kubernetes.io/part-of=dark-factory` |
| ResourceQuota + LimitRange (×5) | каждый ns | потолки ресурсов и дефолты для подов |
| ServiceAccounts `factory-api`, `factory-reconciler` (factory), `factory-ci` (ci), `factory-agent` (factory-runs), `apps-deployer` (apps-dev) | — | все с `automountServiceAccountToken: false`; роли привяжет T030 |
| NetworkPolicy ×19 | каждый ns | `deny-all`, `allow-dns`, `allow-intra-ns` везде; `allow-egress-https` в factory/factory-runs/ci/argocd |
| Secret `factory-postgres-credentials` | factory | генерируется `openssl rand` при первом запуске (ключи `POSTGRES_PASSWORD`, `FACTORY_DB_PASSWORD`, `PLANE_DB_PASSWORD`); **в git не попадает** |
| StatefulSet `factory-postgres` (postgres:16.4), Service `factory-postgres` (ClusterIP) + headless, PVC `data` 5Gi | factory | БД фабрики; две логически раздельные БД: `factory` (владелец `factory`) и `plane` (владелец `plane`) |
| PVC `factory-postgres-backups` 2Gi, CronJob `factory-postgres-backup` + ConfigMap со скриптом | factory | ночные дампы обеих БД |
| Job `capacity-smoke-e2e`, Pod `negative-egress-probe`, Pod `restore-test-runner` | временные | проверки; создаются и удаляются скриптами |

### Квоты

| Namespace | requests | limits | pods |
|---|---|---|---|
| factory | 1 CPU / 2Gi | 2 CPU / 4Gi | 20 |
| factory-runs | 750m / 1500Mi | 1.5 CPU / 3Gi | 10 |
| ci | 500m / 1Gi | 1 CPU / 2Gi | 10 |
| apps-dev | 500m / 1Gi | 1 CPU / 2Gi | 10 |
| argocd | 400m / 800Mi | 750m / 1500Mi | 20 |

Сумма лимитов ≈ 12.5Gi — это потолки, а не резервирование: при concurrency=1 фактический пик (postgres ≤1Gi + API + один job) остаётся заметно ниже бюджета 10–12GB. LimitRange задаёт дефолты (`defaultRequest` 100m/128Mi, `default` 500m/512Mi) — без них ResourceQuota, ограничивающий limits, отвергал бы поды без явно указанных ресурсов.

### Сетевые политики (deny-by-default)

> **Ограничение Docker Desktop (проверено живым прогоном):** встроенный Kubernetes Docker Desktop не исполняет NetworkPolicy (CNI без поддержки политик). Baseline-политики применяются как данные и становятся эффективными в кластере с исполняющим CNI (DC-контур, T-091); в локальном контуре безопасность подов держится на остальных слоях (SA без токена и RBAC, non-root, отсутствие privileged, разделение namespace). Egress-тесты детектят неисполнение автоматически (см. ниже) и не падают из-за него.

| Namespace | DNS 53 | intra-ns | egress 443 | внешний egress прочий |
|---|---|---|---|---|
| factory | ✓ | ✓ (вкл. доступ к postgres) | ✓ (GitHub API) | запрещён (80/25 и др.) |
| factory-runs | ✓ | ✓ | ✓ (GitHub API, LLM) | запрещён |
| ci | ✓ | ✓ | ✓ (Actions queue) | запрещён |
| argocd | ✓ | ✓ | ✓ (git/helm repos) | запрещён |
| apps-dev | ✓ | ✓ | — (осознанно, политика приложения — T030/T031+) | запрещён |

`allow-egress-https` для MVP широкий (`0.0.0.0/0:443`); T030 сузит до явных endpoint'ов. Обоснование каждого allow — комментариями в `manifests/networkpolicies.yaml`.

## PostgreSQL

- Подключение: `kubectl --context docker-desktop -n factory exec -it factory-postgres-0 -- psql -U factory -d factory` или `kubectl port-forward -n factory svc/factory-postgres 5432:5432`.
- Разделение БД Plane — логическое (отдельные база и владелец внутри одного инстанса); физическое разделение — за пределами MVP.
- Пароли существуют только в секрете кластера. Пересоздание секрета не меняет пароли уже инициализированной БД: ротация — `ALTER ROLE ... WITH PASSWORD` в БД + обновление секрета вручную.

## Backup / восстановление

- CronJob `factory-postgres-backup`: nightly 02:30 UTC (`"30 2 * * *"`), `pg_dump -Fc` обеих БД в `/backups/<db>/<ts>.dump` на PVC 2Gi, `concurrencyPolicy: Forbid`, retention 7 дней (`find -mtime +7 -delete`).
- **RPO = 24h** (ночные дампы). **RTO ≈ 15 минут** при ручной процедуре ниже.
- Посмотреть дампы: `kubectl -n factory run ls-backups --rm -it --image=postgres:16.4 --overrides='{"spec":{"containers":[{"name":"ls-backups","image":"postgres:16.4","command":["ls","-laR","/backups"],"volumeMounts":[{"name":"b","mountPath":"/backups"}]}],"volumes":[{"name":"b","persistentVolumeClaim":{"claimName":"factory-postgres-backups"}}]}}'`.
- Ручное восстановление: временный под `postgres:16.4` с PVC `factory-postgres-backups` и envFrom секрета, затем `pg_restore -h factory-postgres -U postgres -d <цель> --no-owner --exit-on-error /backups/factory/<ts>.dump` (создать целевую БД заранее через `CREATE DATABASE`). Восстановление в продовую БД — только осознанно, поверх пустой/согласованной схемы.
- **Автоматический restore-тест**: `scripts/restore-test.sh` — берёт последний дамп `factory` с PVC, разворачивает в базу `restore_test_<ts>`, сверяет список таблиц и количество записей с исходной БД, затем DROP (в т.ч. при ошибке). `SKIP`, если дампов ещё нет; `PASS tables=0` с пометкой, пока в БД нет пользовательских таблиц (до миграций T-006).

## Capacity smoke

`scripts/capacity-smoke.sh`: allocatable узла, idle-замеры через `kubectl top` (если metrics-server доступен, иначе warn) и один e2e-подобный job (`busybox:1.37`, requests 128Mi/100m, limits 512Mi/500m) с cgroup v2-замером peak memory (`memory.peak`/`memory.current`) и среднего CPU (`cpu.stat`) за ~20 сек. Вердикт: allocatable ≥10Gi — запас есть; 8–10Gi — warn; <8Gi — ошибка. Запускать перед повышением concurrency (ADR-010).

## Проверка GitHub App

`scripts/check-github-app.sh`: env `DARK_FACTORY_GITHUB_APP_ID`, `DARK_FACTORY_GITHUB_INSTALLATION_ID`, `DARK_FACTORY_GITHUB_APP_PRIVATE_KEY` (PEM-текст), опц. `DARK_FACTORY_GITHUB_API_URL`. Без env — `SKIP` (exit 0). С env: PEM парсится openssl, строится JWT RS256 (bash + openssl, без зависимостей), `POST /app/installations/{id}/access_tokens` — `201` означает валидные учётные данные. Печатаются только факты (app_id, installation_id, expires_at); ключ/токен никогда не выводятся. Офлайн-проверка JWT-пайплайна: `scripts/check-github-app.sh --self-test`.

## Негативные egress-тесты

`scripts/negative-egress-test.sh [ns ...]` (по умолчанию `factory` и `factory-runs`): временный под с двумя контейнерами проверяет — DNS резолвится; `https://example.com` (443) доступен (критерий — любой завершённый HTTP-обмен: код ответа — свойство цели, а не сети; `api.github.com` при этом проверяется информационно — `github_code` в выводе); в `factory` доступен `factory-postgres:5432`. Расхождение ожиданий — ненулевой exit; поды удаляются после прогона.

**Детект исполнения NetworkPolicy:** перед пробами скрипт создаёт throwaway-namespace с двумя одинаковыми канарейками, одна из которых выбрана deny-all-egress политикой. Если «запрещённая» канарейка всё равно достигает сети — политики кластером не исполняются, и блокирующие проверки (80/25) печатаются как `SKIPPED` с предупреждением вместо FAIL. Так тест честно разделяет «политика дырявая» (FAIL при работающем enforcement) и «CNI без enforcement» (SKIPPED + warn).

## Teardown

`./teardown.sh [--yes]` удаляет все пять namespace с ресурсами, PVC (данные postgres и бэкапы) и секретами. **Необратимо.** Без `--yes` требует ввести `DELETE`.

## Ограничения

- Одноузловой локальный кластер, HA нет; concurrency=1 (ADR-010).
- `allow-egress-https` широкий — сужается в T030; роли для service accounts — T030; Argo CD и GitOps — T032.
- **NetworkPolicy не исполняется в Docker Desktop** (см. заметку в разделе «Сетевые политики») — фикс детекта: fix/t-029-egress-probe; занесено в `docs/tech-dept.md` (TD-001).
- **Живой прогон при создании не выполнялся**: Kubernetes в Docker Desktop был выключен (проверка оркестратором и скриптом); валидация статическая — `bash -n`, `shellcheck` (docker), `kubeconform -strict` (docker). Первый живой прогон: включить K8s → `./bootstrap.sh`.
- StorageClass по умолчанию Docker Desktop (hostpath): PVC привязаны к узлу — норма для одноузлового MVP.
- metrics-server при отсутствии переводит capacity smoke в режим без idle-замеров (warn, не fail).

## Проверка (валидация артефактов)

- `bash -n` — все `.sh`, синтаксис чистый;
- `shellcheck` (docker `koalaman/shellcheck:stable`) — без замечаний;
- `kubeconform -strict` (docker `ghcr.io/yannh/kubeconform`) — все манифесты валидны;
- `check-github-app.sh --self-test` — JWT-пайплайн подтверждён локально.
