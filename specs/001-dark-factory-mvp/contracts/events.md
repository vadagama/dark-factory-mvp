# Contract: Domain Events (PostgreSQL outbox)

**Feature**: `001-dark-factory-mvp` | **Date**: 2026-09-13 | **Plan**: [`../plan.md`](../plan.md) | **ADR**: ADR-016

Событийная модель — transactional outbox в PostgreSQL: изменение состояния и событие пишутся в одной транзакции; доставка at-least-once; порядок — только в рамках `changeId`/`runId` по монотонному `sequence`.

## Envelope

```json
{
  "eventId": "evt_01H...",
  "eventType": "run.stage_completed",
  "eventVersion": 1,
  "occurredAt": "2026-09-13T10:00:00Z",
  "changeId": "chg_01H...",
  "runId": "run_01H...",
  "stage": "construction",
  "aggregateId": "run_01H...",
  "aggregateVersion": 7,
  "correlationId": "corr_01H...",
  "causationId": "cmd_01H...",
  "artifactRefs": [],
  "payload": { }
}
```

- `eventVersion` — версия схемы события; новые поля — обратносовместимо, ломающие изменения — новая версия.
- `aggregateVersion` — монотонная версия агрегата; используется для порядка и дедупликации.
- `eventId` уникален; потребители дедуплицируют по `eventId`/`commandId`.

## Типы событий (MVP)

| `eventType` | Публикуется при | Потребители (MVP) |
|---|---|---|
| `change.intaken` | создан Change из снапшота | Sync-статус трекера |
| `run.started` | старт run | Console/API cache |
| `run.stage_completed` | сохранён `StageResult` | Flow-продолжение, трекер |
| `run.status_changed` | смена `RunStatus` | Console, трекер |
| `gate.evaluated` | результат гейта на итоговом SHA | Merge policy (T-032) |
| `approval.recorded` | записано `Decision` | Flow-продолжение |
| `merge.completed` | merge доверенным финализатором | Post-merge pipeline |
| `release.completed` | успешный smoke, статус «выпущено» | Трекер, метрики |
| `usage.recorded` | учтён расход попытки | Бюджет-координатор (T-062) |

## Доставка и надёжность

- **At-least-once**, per-consumer состояние `event_delivery` (`event_id`, `consumer_id`, `status`, `attempts`, `next_attempt_at`, `last_error`); уникальность пары (`event_id`, `consumer_id`).
- **Retry** с exponential backoff; после исчерпания — dead-letter; ручной replay — отдельной операцией.
- **Ordering**: только в рамках `changeId`/`runId` по `sequence`; кросс-агрегатный порядок не гарантируется.
- **Cleanup** outbox — только after all-delivered + retention; retention evidence перекрывает окно approval/retry/аудита (ADR-009 §9).

## Побочные эффекты и идемпотентность

- Событие — не команда: потребитель, порождающий внешний эффект, использует `effect_key` и effect ledger; при `unknown` — lookup по детерминированному маркеру до повтора (ADR-006 §3).
- Вебхуки внешних систем — ускоритель, не источник истины: реакция подтверждается идемпотентным reconcile по `changeId` (FR-017, ADR-016 §8).
- Kafka в MVP не разворачивается; миграция — отдельным ADR по измеримым триггерам (ADR-016 §10).
