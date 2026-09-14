# Outbox Dispatcher (T028, ADR-016)

`factory-outbox-dispatcher` — CronJob в namespace `factory`, **единственный владелец доставки** outbox-событий (ADR-016 п.4). Каждые 2 минуты выполняет `factory outbox dispatch --cleanup` — один идемпотентный проход доставки плюс шаг очистки.

## Назначение и механика

- **Резервирование**: короткая транзакция `SELECT ... FOR UPDATE SKIP LOCKED` + lease 120 c (`next_attempt_at`); вызов обработчика — строго вне транзакции (ADR-016 п.4).
- **Порядок**: только в рамках stream = `aggregate_id` по монотонному `sequence` (ADR-016 п.6, MVP-решение). Событие с большим `sequence` откладывается (defer), пока не доставлен минимальный недоставленный `sequence` этого потребителя; `dead` тоже блокирует stream до replay/skip.
- **Исходы**: фиксируются в `event_delivery` — `delivered` / `failed` (exponential backoff 30 с × 4, cap 1 ч) / `dead` после 5 попыток (ADR-016 п.5).
- **At-least-once**: exactly-once не гарантируется; потребители обязаны дедуплицировать по `eventId` (ADR-016 п.2). Крэш между вызовом обработчика и фиксацией исхода → повторная доставка после истечения lease.
- **Очистка** (`--cleanup`): событие удаляется из `outbox` только когда все его доставки `delivered` либо операторно `waived` **и** истёк retention 30 дней (ADR-009 §9); `dead` блокирует удаление до успешного replay или зафиксированного решения оператора (ADR-016 п.9).

## Расписание и политики

| Параметр | Значение |
|---|---|
| `schedule` | `*/2 * * * *`, `timeZone: Etc/UTC` |
| `concurrencyPolicy` | `Forbid` (только оптимизация; корректность даёт SKIP LOCKED) |
| `activeDeadlineSeconds` | 300 |
| `backoffLimit` | 0 (retry сам владеет backoff'ом в `event_delivery`) |
| `restartPolicy` | `OnFailure` |
| resources | requests 50m/64Mi, limits 200m/128Mi |

## Ручные операции

```bash
# разовый проход вручную (из CronJob)
kubectl -n factory create job --from=cronjob/factory-outbox-dispatcher outbox-manual

# локально / в pod'е
uv run factory outbox dispatch [--json] [--limit N] [--cleanup]
uv run factory outbox replay --event-id <ID> [--consumer <ID>]   # dead/failed -> pending, attempts=0
uv run factory outbox skip  --event-id <ID> --consumer <ID>      # админский skip: -> waived, разблокирует stream
```

`replay` и `skip` — ручные операции: replay возобновляет исчерпанные доставки, skip — явное административное решение «этого события потребителю не нужно» с фиксацией `waived` в `event_delivery` (терминальный статус, допустимый для очистки).

## Конфигурация и ограничения

- **Образ — плейсхолдер** (`ghcr.io/vadagama/dark-factory:bootstrap`): реальный образ собирается в T033 (OCI build job, immutable digest), chart — T042. До этого тег обновляется вручную.
- **Секрет — плейсхолдер**: `DATABASE_URL` берётся из Secret `factory-outbox-dispatcher-database` (ключ `DATABASE_URL`, URL не попадает в git; ADR-009). Создать вне репозитория:
  ```bash
  kubectl -n factory create secret generic factory-outbox-dispatcher-database \
    --from-literal=DATABASE_URL='postgresql+psycopg://<user>:<password>@factory-postgres:5432/factory'
  ```
- **ServiceAccount**: `factory-api` из bootstrap (T029), token automount выключен; выделенный SA для диспетчера — T030/T042.
- **Обработчики**: MVP использует `NoOpHandler` — реальные потребители (трекер/статусы T-063, T-090) регистрируются в `HandlerRegistry`; запуск CI-pipeline как потребителя пока не реализован (YAGNI, T-063+).
- Архивация outbox (альтернатива удалению, ADR-016 п.9) не реализована — только удаление по правилам очистки.
