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
