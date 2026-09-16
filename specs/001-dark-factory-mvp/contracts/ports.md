# Contract: Ports (Python `Protocol`)

**Feature**: `001-dark-factory-mvp` | **Date**: 2026-09-13 | **Plan**: [`../plan.md`](../plan.md) | **Data model**: [`../data-model.md`](../data-model.md)

Порты — единственная точка интеграции ядра с внешними системами (HLD §7, ADR-015 §3). Зависимости направлены **к** портам: адаптеры импортируют только `dark_factory.ports`; импорт `adapters` из ядра запрещён (проверяется `tests/test_import_boundaries.py`).

Ниже — контрактные сигнатуры. Реализации — в `adapters/`; в P0 — фейки. Единая контрактная тест-сюита исполняется против fake → GitHub → GitLab (ADR-019 §6).

## Общие типы

```python
# dark_factory.ports — общие типы результата
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

@dataclass(frozen=True)
class HealthStatus:
    healthy: bool
    detail: str | None = None

@dataclass(frozen=True)
class PipelineStatus:
    """Наблюдаемое состояние CI-пайплайна (провайдер-нейтрально)."""
    ref: str
    status: str            # queued | in_progress | success | failure | canceled
    url: str | None = None
```

Все методы, меняющие внешнее состояние, принимают `idempotency_key`/`effect_key` и возвращают `external_ref` для effect ledger (ADR-006 §3).

## HarnessPort

```python
@runtime_checkable
class HarnessPort(Protocol):
    async def run_stage(self, envelope: TaskEnvelope, /) -> AgentResult: ...
    async def health(self, /) -> HealthStatus: ...
```

Первый адаптер — PydanticAI (ADR-002 §2); `TaskEnvelope → AgentResult` — контракт задания/результата агента. Детерминированные шаги стадии исполняются без обращения к harness.

## WorkflowEnginePort

```python
@runtime_checkable
class WorkflowEnginePort(Protocol):
    async def start(self, *, idempotency_key: str, expected_revision: int | None = None) -> str: ...
    async def resume(self, run_id: str, *, idempotency_key: str) -> str: ...
    async def cancel(self, run_id: str, *, idempotency_key: str, reason: str) -> None: ...
    async def get_status(self, run_id: str, /) -> RunStatus: ...
```

Лёгкий workflow-core; позже — Temporal тем же портом (ADR-003, ADR-006 §9). `idempotency_key` и `expected_revision` обязательны (ADR-006 §9).

## ReconciliationService

Отдельный сервис, **не** метод движка (ADR-006 §9).

```python
@runtime_checkable
class ReconciliationService(Protocol):
    async def reconcile(self, *, desired: ReconcileDesired, observed: ReconcileObserved) -> ReconcileResult: ...
```

Разрешение рассинхрона «CI ↔ PostgreSQL» — в пользу PostgreSQL **после** сверки observed (HLD §9.1). Конкурентность — PG lease + монотонный `fencing_token`.

## SourceControlPort

Разделён на `RepositoryPort` / `MergeRequestPort` / `PipelinePort` (принцип dmtools; доменный тип CR — единый `ChangeRequestRef`, ADR-019).

```python
@runtime_checkable
class RepositoryPort(Protocol):
    async def get_revision(self, repository: RepositoryRef, ref: str, /) -> str: ...
    async def ensure_branch(self, repository: RepositoryRef, branch: str, *, from_revision: str, idempotency_key: str) -> str: ...

@runtime_checkable
class MergeRequestPort(Protocol):
    async def open(self, request: OpenChangeRequest, *, idempotency_key: str) -> ChangeRequestRef: ...
    async def find_existing(self, repository: RepositoryRef, change_id: str, /) -> ChangeRequestRef | None: ...
    async def add_comment(self, cr: ChangeRequestRef, body: str, *, idempotency_key: str) -> None: ...
    async def merge(self, cr: ChangeRequestRef, *, expected_sha: str, idempotency_key: str) -> None: ...

@runtime_checkable
class PipelinePort(Protocol):
    async def status(self, repository: RepositoryRef, ref: str, /) -> PipelineStatus: ...
```

`find_existing` и `expected_sha` перед merge — реализация FR-011/FR-017. Поля `status` CR берутся из единого `ChangeRequestStatus`.

## CIPort

```python
@runtime_checkable
class CIPort(Protocol):
    async def run_stage_job(self, request: StageJobRequest, *, idempotency_key: str) -> str: ...
    async def gate_status(self, job_ref: str, /) -> GateResult: ...
    async def artifacts(self, job_ref: str, /) -> list[ArtifactRef]: ...
```

Провайдер: GitHub Actions (MVP), GitLab CI (T-034).

## TrackerPort

```python
@runtime_checkable
class TrackerPort(Protocol):
    async def get_change(self, external_ref: str, /) -> Change | None: ...
    async def publish_status(self, change_id: str, status: str, *, idempotency_key: str) -> None: ...
    async def request_approval(self, change_id: str, gate: Gate, *, idempotency_key: str) -> None: ...
```

Plane (self-hosted, webhook + HMAC); до готовности — NoOp-заглушка. Недоступность трекера не блокирует CLI/Console (FR-020).

## ArtifactStorePort

```python
@runtime_checkable
class ArtifactStorePort(Protocol):
    async def put(self, spec: ArtifactSpec, /) -> ArtifactRef: ...
    async def get(self, ref: ArtifactRef, /) -> bytes: ...
    async def exists(self, ref: ArtifactRef, /) -> bool: ...
```

CI artifacts провайдера; в DC — S3/MinIO (ADR-009, ADR-015 §4).

## TelemetryPort

```python
@runtime_checkable
class TelemetryPort(Protocol):
    def span(self, name: str, /, **attributes: str) -> ContextManager[Span]: ...
    def record_usage(self, usage: Usage, /, **attributes: str) -> None: ...
```

OTLP-экспорт; корреляция `change → run → stage → agent → tool` (ADR-009 §2).

## EventPublisherPort

```python
@runtime_checkable
class EventPublisherPort(Protocol):
    async def publish(self, event: DomainEvent, /) -> None: ...
```

Производители не зависят от схемы таблиц outbox (ADR-016 §7); запись события — в той же транзакции, что изменение состояния.

## SDDPort

```python
@runtime_checkable
class SDDPort(Protocol):
    async def create_change(self, change: ChangeSet, /) -> str: ...
    async def read_requirements(self, change_id: str, /) -> RequirementsSnapshot: ...
    async def apply_delta(self, change_id: str, *, expected_revision: str) -> str: ...
```

`NativeChangeSetAdapter` — основной, `SpecKitAdapter` — bootstrap-импорт legacy-артефактов `specs/`, `OpenSpecAdapter` — compatibility import/export (ADR-020 п.8). Контракт артефактов — [`changeset.md`](./changeset.md).

## KnowledgePort

```python
@runtime_checkable
class KnowledgePort(Protocol):
    async def collect(self, request: ContextRequest, /) -> ContextBundle: ...
```

Сбор источников контекста в `ContextBundle` с версиями и provenance (план T-012, FR-001). Вход — `ContextRequest(change_id, run_id)`; сам `ContextBundle` — domain-тип из `dark_factory.context.bundle`, ре-экспортируемый через `dark_factory.ports` вместе с `build_bundle`. Источник — `ContextSource(kind, location, revision, content_hash, retrieved_at)`: `kind` — `SourceKind` (`repo`, `spec`, `constitution`, `adr`, `engineering_pack`; один `spec` закрывает `specs/` до T-020 и `.factory/` после — их различает `location`), `revision` — закреплённая версия (git sha), `content_hash` — sha256 содержимого, `retrieved_at` — летучий штамп, в hash не входит. `bundle_hash` — sha256 канонической сериализации кортежей `(kind, location, revision, content_hash)`, отсортированных детерминированно: одинаковые входы → одинаковый bundle (воспроизводимость, DoD T-012). Поиск/traversal источников придут с реальными провайдерами (YAGNI); в P0 реализация — in-memory фейк.

## ExecutionPort

```python
@runtime_checkable
class ExecutionPort(Protocol):
    async def prepare_workspace(self, request: WorkspaceRequest, /, *, idempotency_key: str) -> WorkspaceHandle: ...
    async def write_file(self, workspace: WorkspaceHandle, path: str, content: bytes, /, *, idempotency_key: str) -> None: ...
    async def run_command(self, workspace: WorkspaceHandle, argv: tuple[str, ...], /, *, idempotency_key: str) -> ExecutionResult: ...
    async def collect_evidence(self, workspace: WorkspaceHandle, path: str, /, *, idempotency_key: str) -> EvidenceFile: ...
```

Изолированный worktree от закреплённой ревизии, запись файлов, исполнение команд и сбор evidence (план T-012). `WorkspaceRequest(repository, revision, change_id)` → `WorkspaceHandle(workspace_id, repository, revision)`; повтор `prepare_workspace` с тем же `idempotency_key` возвращает тот же handle и не создаёт второй workspace (FR-017). `write_file` кладёт содержимое в рабочее дерево идемпотентно по состоянию (тот же путь после повтора держит те же байты) — это write-половина `collect_evidence`, через неё инструменты роли правят изолированный workspace (T-092 S2). `run_command` исполняет команду и возвращает `ExecutionResult(ok, exit_code, stdout, stderr)`; `collect_evidence` возвращает `EvidenceFile(path, content_hash, content)` с sha256-хешем содержимого. В P0 реализация — in-memory фейк.

## Порты, вводимые позже (не авансом)

- `KnowledgePort`, `ExecutionPort` — T-012 (формализованы выше как контракты; реализация — фейки P0).
- `CIPort` — T-030 (формализован выше как контракт, реализация — там).
- `SDDPort` — T-020.

## Инварианты контракта

- Ядро не импортирует SDK провайдеров и `adapters` (ADR-015 §3).
- Все mutating-операции идемпотентны по ключу; повтор не создаёт второй внешний эффект (FR-017).
- Один run исполняется ровно в одном провайдере; порты не смешивают провайдеров в рамках run (ADR-019 §5).
- Контрактные сюиты одинаковы для fake/GitHub/GitLab.
