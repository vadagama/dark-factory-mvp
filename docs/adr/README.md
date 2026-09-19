# Архитектурные решения (ADR)

Лёгкий процесс фиксации значимых технических решений. Все решения — в этой папке, по одному файлу на решение.

## Когда писать ADR

- Выбор стека, фреймворка, БД, инфраструктуры.
- Изменение архитектуры или ключевых паттернов.
- Отказ от ранее принятого решения (со ссылкой на старый ADR).

Не пишутся ADR для тривиальных решений (имена переменных, мелкий рефакторинг).

## Формат

- Имя файла: `ADR-NNN-короткое-название.md`, где NNN — порядковый номер (начинается с 001).
- Шаблон: `ADR-000-template.md` в этой папке.
- Название решения — на английском в имени файла; содержимое — на русском.

## Статусы

| Статус | Значение |
|---|---|
| `предложено` | Решение вынесено на обсуждение, не принято |
| `принято` | Действующее решение |
| `принято с условиями` | Действующее решение; финальное принятие — после выполнения зафиксированных в ADR условий |
| `отклонено` | Не принято (в файле указана причина) |
| `заменено` | Заменено другим ADR (ссылка в файле) |

## Реестр решений

| ADR | Решение | Статус |
|---|---|---|
| [ADR-001](ADR-001-adopt-spec-kit.md) | Adopt GitHub Spec Kit as the SDD toolkit (bootstrap-фаза; заменён ADR-020) | принято (bootstrap; историческое) |
| [ADR-002](ADR-002-python-core-stack.md) | Python core stack (PydanticAI + pydantic-graph) | принято |
| [ADR-003](ADR-003-no-temporal-in-mvp.md) | No Temporal in MVP | принято |
| [ADR-004](ADR-004-postgresql-factory-state.md) | PostgreSQL as factory state store | принято |
| [ADR-005](ADR-005-stage-scoped-graphs-light-workflow-core.md) | Stage-scoped graphs + light workflow core | принято |
| [ADR-006](ADR-006-ephemeral-job-pods-reconciler-cronjob.md) | Ephemeral job pods + reconciler CronJob | принято |
| [ADR-007](ADR-007-nine-role-catalog.md) | Nine role catalog | принято |
| [ADR-008](ADR-008-plugin-architecture-core-sdk.md) | Plugin architecture: core + SDK + 8 plugin types | принято |
| [ADR-009](ADR-009-minimal-bootstrap-otel.md) | Minimal bootstrap + OTel contract | принято |
| [ADR-010](ADR-010-local-k8s-helm-argocd.md) | Local K8s (Docker Desktop) + Helm + Argo CD | принято |
| [ADR-011](ADR-011-risk-based-merge-release-policy.md) | Risk-based merge and release policy | принято |
| [ADR-012](ADR-012-gitlab-premium.md) | GitLab Premium | принято |
| [ADR-013](ADR-013-plane-tracker-trackerport.md) | Plane tracker via TrackerPort | принято |
| [ADR-014](ADR-014-react-uikit-storybook.md) | React Small UIKit + Storybook | принято |
| [ADR-015](ADR-015-repository-boundaries.md) | Repository boundaries for factory, operational state and products | принято с условиями |
| [ADR-016](ADR-016-postgresql-outbox.md) | PostgreSQL outbox event model | принято |
| [ADR-017](ADR-017-unified-openspec-sdd-factory-profile.md) | Unified OpenSpec SDD model with factory profile (частично перекрывает ADR-001) | заменено (ADR-020) |
| [ADR-018](ADR-018-human-participation-autonomous-execution.md) | Human participation and autonomous execution boundaries | принято |
| [ADR-019](ADR-019-multi-provider-sc-ci-github-first.md) | Multi-provider SC/CI: GitHub-адаптер первым, GitLab — вторым | принято |
| [ADR-020](ADR-020-native-sdd-core.md) | Native SDD Core: ChangeSet, Product Baseline, OKF-проекция (заменяет ADR-017) | принято |
| [ADR-021](ADR-021-console-mvp-delivery.md) | Console MVP: размещение (`console/`), сборка (npm/Vite) и отдача (отдельный nginx-образ + chart) | принято |
| [ADR-022](ADR-022-pure-psycopg-libpq.md) | Pure-Python psycopg with the distribution's libpq (drop the `binary` extra) | принято |
| [ADR-023](ADR-023-risk-classes-and-control-points.md) | Risk classes R0–R4 and control points | принято |
| [ADR-024](ADR-024-durable-run-driver-and-composition-root.md) | Durable run driver and the composition root | принято с условиями |
| [ADR-025](ADR-025-process-entry-point-and-lazy-composition.md) | Process entry point and lazy composition | принято |
| [ADR-026](ADR-026-parameterizable-ci-stages.md) | Параметризуемые этапы CI: выключатели `CI_SKIP_<JOB>` через repository variables, opt-out и fail-safe | принято |
| [ADR-027](ADR-027-console-ci-stage-toggles.md) | Управление этапами CI из консоли: репозиторные переменные за API (`ci:write` + operator, fail-closed) | принято |
| [ADR-028](ADR-028-console-reactivity-polling.md) | Console reactivity: background polling (WebSockets rejected in MVP; SSE as a future option) | принято |
| [ADR-029](ADR-029-human-gates-at-design-phase.md) | Human gates at the design phase and machine-only pipeline resolution (уточняет ADR-018 п.1 и ADR-023 п.1/3/4/6) | принято |
| [ADR-030](ADR-030-product-registry.md) | Product registry and product scope | принято |
| [ADR-031](ADR-031-repository-provisioning.md) | Repository provisioning (validate, clone, bootstrap) | принято с условиями |
| [ADR-032](ADR-032-phase-projection.md) | Phase projection over Flow stages | принято |
| [ADR-033](ADR-033-operator-guidance.md) | Operator guidance as a server-computed read model | принято |
| [ADR-034](ADR-034-conversations-and-rework-orders.md) | Conversations — questions, comments and rework orders | принято |
| [ADR-035](ADR-035-document-artifacts-git-source-of-truth.md) | Document artifacts — git as the source of truth, revisions and drafts | принято с условиями |
| [ADR-036](ADR-036-merge-assistant.md) | Merge assistant — human-initiated merge from the console (уточняет ADR-011) | принято с условиями |
| [ADR-037](ADR-037-console-changeset-workspace-ia.md) | Console IA and the ChangeSet workspace | принято с условиями |
