# ADR-009: Minimal Bootstrap and OpenTelemetry Observability

- **Статус**: принято
- **Дата**: 2026-09-13
- **Автор**: software-architect
- Решение согласовано пользователем (ответы на вопросы раздела 5 plan.md, 2026-09-13)
- **Уточнено** (2026-09-13, архитектурное ревью ADR-пакета): MVP-модель идентичности и доступа (п.7), граница данных LLM-эндпоинта (п.8), retention обязательной evidence (п.9)
- **Уточнено** (2026-09-15, T-060): реализация — прямые зависимости `opentelemetry-api`/`opentelemetry-sdk` и пакет `adapters/telemetry` (`OtlpTelemetryAdapter`); политика экспорта MVP — job logs (stdout) и JSON-lines artifact, внешний OTLP-backend подключается при появлении backend (TD-003); usage/cost — атрибуты `usage.*` span'а `factory.usage`

## Контекст

- Q-8 plan §5: полный корпоративный стек (Keycloak, Vault, MinIO/Harbor, Prometheus/Grafana/Loki/Tempo, Sentry) против минимального bootstrap + OTel. По Langfuse исходные материалы расходятся; Registry: «любой OCI» vs Harbor.
- Влияние: объём bootstrap, ресурсы 24GB, сложность первого запуска; решение до T-060/T-040.

## Решение

1. В MVP — **только минимальный bootstrap**; корпоративный стек не разворачивается (его место — DC-контур, T-091).
2. **OTel — нейтральный контракт наблюдаемости**: `TelemetryPort` + OTLP-адаптер (T-060); экспорт по умолчанию — job logs/artifacts; внешний backend опционален.
3. **Для AI-трасс — Pydantic Evals + OTel** (usage/cost атрибуты); Langfuse — не обязательная зависимость, допустим позже как адаптер.
4. **Registry — любой OCI-совместимый** (Harbor не обязателен); Keycloak/Vault/MinIO/Harbor и Prometheus/Grafana/Loki/Tempo/Sentry — вне MVP.
5. Долгоживущие evidence — в Git/`dark-factory-runs` (ADR-006, ADR-015), а не в observability-хранилище. Сам `dark-factory-runs` содержит компактный индекс (`RunManifest`, итоговый `StageResult`, approvals/decisions, ссылки, digest); тяжёлые данные — логи, отчёты, скриншоты, SBOM — в MVP лежат в GitLab CI artifacts и доступны через `ArtifactStorePort` (ADR-015 п.4).

6. **Приоритет при конфликте.** Настоящий ADR — источник истины по составу инфраструктуры MVP. Если другой ADR или план называет конкретный сервис (Loki, Prometheus, MinIO, Harbor, Vault, Keycloak, Langfuse) как часть MVP, действует настоящий ADR: такой сервис относится к DC-контуру (T-091), а в MVP его роль выполняют job logs/CI artifacts, PostgreSQL (ADR-004) и OTLP-экспорт. Ссылки на профильные хранилища в других ADR читаются как распределение данных по классам хранилищ, а не как обязательный набор развёртываемых сервисов.

7. **MVP-модель идентичности и доступа.** Keycloak вне MVP (п.1) не означает отсутствие AuthN: локальный контур — один оператор + service-идентичности (CI, CLI). Mutating-операции API (approve/retry/pause/cancel) требуют token-based аутентификации; service-токены — минимальные scopes, хранение в Kubernetes Secret, ротация. Approval — version-bound запись `{actor, role, contract_hash/commit_sha, timestamp, decision}`; изменение hash/SHA аннулирует approval (согласовано с ADR-018 п.3, T-016). Все mutating-операции аудируются. Конкретный механизм — в T-050; расширенная модель (OIDC/RBAC) — T-091.
8. **Граница данных LLM-эндпоинта** (ADR-002 п.6): секреты и персональные данные не попадают в prompt/context и в OTel/log-экспорт — scan/redaction до отправки; tool output и содержимое репозитория считаются untrusted input (риск prompt injection, vision §6); TLS до endpoint; негативные тесты на exfiltration — в T-010.
9. **Retention обязательной evidence:** срок хранения CI-артефактов, на которые ссылается run record, перекрывает максимальный срок approval/retry/аудита MVP; перед завершением run проверяется доступность URI (ArtifactStorePort); недоступная обязательная evidence — статус `evidence_unavailable`, успешный терминальный статус запрещён.

Связанные задачи: T-040, T-010, T-050, T-060, T-061, T-091.

## Альтернативы

| Вариант | Плюсы | Минусы | Почему не выбран |
|---|---|---|---|
| Полный корпоративный стек в MVP | Продакшн-наблюдаемость, готовность к DC | Bootstrap нереалистичен на MacBook 24GB; порог входа; медленный первый запуск | Отклонено; стек отложен в T-091 |
| Минимальный bootstrap + OTel как контракт | Быстрый первый запуск; смена backend без изменения кода | Ограниченная ретроспектива инцидентов в MVP | Выбрано |
| Langfuse обязателен для AI-трасс | Готовый UI трасс LLM | Ещё один сервис; противоречие в исходных материалах | Отклонено: Pydantic Evals + OTel закрывают MVP-потребность |

## Последствия

**Позитивные**
- Наблюдаемость не привязана к вендору: OTLP-экспорт заменяет backend без правок ядра.

**Негативные / риски**
- Логи в artifacts с ограниченным retention — при разборе инцидентов опираться на run records.
- Трассировка «change → run → stage → agent → tool» должна быть сквозной с первого дня, иначе корреляцию придётся догонять.

**Дальше**
- T-060: TelemetryPort + OTLP-адаптер и cost-атрибуты; T-073: evals-кейсы из отклонений пилота; T-091: корпоративный стек в DC.
