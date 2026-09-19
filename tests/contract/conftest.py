"""Adapter bindings for the port contract suite (ADR-019 p.6).

Every fixture hands an adapter to the tests through its port type, so the test
modules never import ``dark_factory.adapters`` and the same suite runs against
fake → GitHub → GitLab. The source-control ports are parametrized over the
in-memory fakes and the real GitHub adapter (T-024) driven by the in-memory
GitHub API emulator (``github_api.py``) — no network, no real credentials; the
the tracker port is parametrized the same way over the fake and the Plane adapter
(T-033) driven by ``plane_api.py``; the telemetry port is parametrized over the
fake and the OTel adapter (T-060) driven by the SDK's in-memory exporter; the
execution port is parametrized over the fake and the real worktree adapter
(T-092) driven by an operator-prepared local git mirror seeded per test.
The ``*_journal`` fixtures expose recorded side effects that the ports
themselves do not surface (comments, dispatches, published statuses, spans,
events); fake bindings read their own state, the GitHub binding reads the
emulator's (comment journals strip the adapter's invisible idempotency
markers), and the OTel binding decodes the exporter it was built with.
"""

import asyncio
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import NamedTuple

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from dark_factory.adapters.fakes import (
    FakeArtifactStore,
    FakeCI,
    FakeEventPublisher,
    FakeExecution,
    FakeHarness,
    FakeKnowledge,
    FakeMergeRequests,
    FakePipelines,
    FakeReconciliationService,
    FakeRepository,
    FakeRepositoryProvisioning,
    FakeTelemetry,
    FakeTracker,
    FakeWorkflowEngine,
)
from dark_factory.adapters.provisioning import LocalMirror, LocalMirrorConfig
from dark_factory.adapters.scm.github import GitHubAdapter, GitHubConfig, StaticTokenProvider
from dark_factory.adapters.telemetry import (
    USAGE_ATTRIBUTE_PREFIX,
    USAGE_SPAN_NAME,
    OtlpTelemetryAdapter,
    TelemetryConfig,
)
from dark_factory.adapters.tracker import PlaneConfig, PlaneTrackerAdapter
from dark_factory.changes.enums import GateStatus
from dark_factory.changes.run import Change
from dark_factory.context.sdd.baseline import current_revision
from dark_factory.context.sdd.native import NativeChangeSetAdapter
from dark_factory.context.sdd.openspec import OpenSpecAdapter
from dark_factory.context.sdd.speckit import SpecKitAdapter
from dark_factory.execution import WorktreeExecution, WorktreeExecutionConfig
from dark_factory.ports import (
    ArtifactRef,
    ArtifactStorePort,
    ChangeRequestRef,
    ChangeSource,
    CIPort,
    DomainEvent,
    EventPublisherPort,
    ExecutionPort,
    Gate,
    HarnessPort,
    KnowledgePort,
    MergeRequestPort,
    PipelinePort,
    Provider,
    ReconciliationService,
    RepositoryPort,
    RepositoryProvisioningPort,
    RepositoryRef,
    RiskClass,
    RunStatus,
    SDDPort,
    SourceKind,
    Span,
    TelemetryPort,
    TrackerPort,
    Usage,
    WorkflowEnginePort,
    WorkspaceHandle,
    WorkspaceRequest,
)
from tests.contract.github_api import (
    GITHUB_API_BASE_URL,
    GITHUB_INSTALLATION_TOKEN,
    GitHubApiEmulator,
    parse_job_ref,
    visible_comment,
)
from tests.contract.plane_api import (
    PLANE_API_BASE_URL,
    PLANE_API_TOKEN,
    PROJECT_ID,
    WORKSPACE_SLUG,
    PlaneApiEmulator,
)
from tests.sdd_factories import seed_baseline

PRODUCT = RepositoryRef(provider=Provider.GITHUB, slug="small/pilot")


class _RepositoryBinding(NamedTuple):
    """RepositoryPort plus the commit journal and file view of the same binding."""

    port: RepositoryPort
    commit_journal: Callable[[str], tuple[str, ...]]
    commit_files: Callable[[str], dict[str, bytes]]


class _MergeRequestBinding(NamedTuple):
    """MergeRequestPort plus the comment journal and review seeder of the same binding."""

    port: MergeRequestPort
    journal: Callable[[ChangeRequestRef], tuple[str, ...]]
    seed_review: Callable[..., object]


class _CIBinding(NamedTuple):
    """CIPort plus its dispatch journal and deterministic seeds."""

    port: CIPort
    journal: Callable[[], int]
    seed_gate: Callable[..., None]
    seed_artifacts: Callable[..., None]


class _TrackerBinding(NamedTuple):
    """TrackerPort plus the seeded change and journals of the same binding.

    The tracked change belongs to the binding, not to a separate fixture: a real
    tracker can only be written to for an issue that exists, so every binding
    seeds it up front (the fake tolerates unknown ids, Plane does not).
    """

    port: TrackerPort
    change: Change
    statuses: Callable[[str], tuple[str, ...]]
    approvals: Callable[[str], tuple[Gate, ...]]


class _TelemetryBinding(NamedTuple):
    """TelemetryPort plus the spans and usage records the same binding produced.

    Telemetry has no separate journal: both views are derived from the exporter
    the adapter was built with, so a binding reads what actually left the port.
    """

    port: TelemetryPort
    spans: Callable[[], tuple[Span, ...]]
    usage: Callable[[], tuple[tuple[Usage, dict[str, str]], ...]]


class _ExecutionBinding(NamedTuple):
    """ExecutionPort plus the pinned revision, driving commands and seed hooks of one binding.

    ``prepare_workspace`` mints from the binding's own repository and revision
    (``make_request``); ``ok_command``/``fail_command``/``state_command`` are
    argv pairs the port must run inside the workspace, and the seed hooks create
    the state each binding needs: a registered evidence file, a failing command
    (fake) or a dirty tracked file (worktree adapter) behind ``make_state_fail``.
    """

    port: ExecutionPort
    revision: str
    ok_command: tuple[str, ...]
    fail_command: tuple[str, ...]
    state_command: tuple[str, ...]
    make_state_fail: Callable[[WorkspaceHandle], None]
    seed_file: Callable[[WorkspaceHandle, str, bytes], None]
    make_request: Callable[[], WorkspaceRequest]


class _ProvisioningBinding(NamedTuple):
    """RepositoryProvisioningPort plus the seeded repository states of one binding.

    Provisioning is the port whose validation result *is* the observable state
    (ADR-031 p.4), so the binding owns the seeder: a test materialises the state it
    asserts (absent mirror, empty repository, commits without/with a baseline) and
    the port must report it. ``supports_bootstrap`` states the capability split of
    ADR-031 p.2 explicitly — the fake applies packs, ``LocalMirror`` does not (T069)
    and must fail loudly instead of reporting a bootstrap it did not perform.
    """

    port: RepositoryProvisioningPort
    repository: RepositoryRef
    supports_bootstrap: bool
    seed: Callable[[str], str | None]


def _tracked_change() -> Change:
    """The one change the tracker suite runs against, seeded by every binding.

    ``created_at`` is explicit so the value survives the ISO-8601 round trip
    through the Plane API and stays comparable with the mapped ``Change``.
    """
    return Change(
        id="chg-001",
        title="Add export button",
        source=ChangeSource.TRACKER,
        external_ref="PLANE-42",
        product=PRODUCT,
        risk_class=RiskClass.R1,
        created_at=datetime(2026, 9, 13, 9, 30, tzinfo=UTC),
    )


@pytest.fixture
def repository() -> RepositoryRef:
    """The product repository the source-control tests run against."""
    return PRODUCT


@pytest.fixture
def github_emulator() -> GitHubApiEmulator:
    """A fresh in-memory GitHub API for one test."""
    return GitHubApiEmulator(token=GITHUB_INSTALLATION_TOKEN)


@pytest.fixture
def github_adapter(github_emulator: GitHubApiEmulator) -> GitHubAdapter:
    """The GitHub adapter bound to the emulator: no network, no real App key."""
    return GitHubAdapter(
        GitHubConfig(api_base_url=GITHUB_API_BASE_URL),
        token_provider=StaticTokenProvider(GITHUB_INSTALLATION_TOKEN),
        transport=github_emulator.transport(),
    )


@pytest.fixture(params=["fake", "github"])
def repository_binding(
    request: pytest.FixtureRequest, repository: RepositoryRef
) -> _RepositoryBinding:
    """RepositoryPort and its commit journal/file view, backed by the same adapter."""
    if request.param == "github":
        adapter: GitHubAdapter = request.getfixturevalue("github_adapter")
        emulator: GitHubApiEmulator = request.getfixturevalue("github_emulator")
        return _RepositoryBinding(
            port=adapter.repository,
            commit_journal=emulator.commit_journal,
            commit_files=emulator.commit_files,
        )
    fake = FakeRepository()
    return _RepositoryBinding(
        port=fake,
        commit_journal=lambda branch: fake.commits_of(repository, branch),
        commit_files=fake.commit_files,
    )


@pytest.fixture
def repository_port(repository_binding: _RepositoryBinding) -> RepositoryPort:
    """RepositoryPort bound to the fake and the GitHub adapter (ADR-019 p.6)."""
    return repository_binding.port


@pytest.fixture
def commit_journal(
    repository_binding: _RepositoryBinding,
) -> Callable[[str], tuple[str, ...]]:
    """Commit SHAs on a branch (oldest first) as the binding recorded them."""
    return repository_binding.commit_journal


@pytest.fixture
def commit_files(
    repository_binding: _RepositoryBinding,
) -> Callable[[str], dict[str, bytes]]:
    """The file set a commit carries, as the binding stored it."""
    return repository_binding.commit_files


@pytest.fixture
def merge_requests() -> FakeMergeRequests:
    return FakeMergeRequests()


@pytest.fixture(params=["fake", "github"])
def merge_request_binding(
    request: pytest.FixtureRequest, merge_requests: FakeMergeRequests
) -> _MergeRequestBinding:
    """MergeRequestPort, its comment journal and its review seeder, same adapter."""
    if request.param == "github":
        adapter: GitHubAdapter = request.getfixturevalue("github_adapter")
        emulator: GitHubApiEmulator = request.getfixturevalue("github_emulator")
        return _MergeRequestBinding(
            port=adapter.pull_requests,
            journal=_github_comment_journal(emulator),
            seed_review=_github_review_seeder(emulator),
        )
    return _MergeRequestBinding(
        port=merge_requests,
        journal=merge_requests.comments_of,
        seed_review=merge_requests.record_review,
    )


@pytest.fixture
def merge_request_port(merge_request_binding: _MergeRequestBinding) -> MergeRequestPort:
    return merge_request_binding.port


@pytest.fixture
def comment_journal(
    merge_request_binding: _MergeRequestBinding,
) -> Callable[[ChangeRequestRef], tuple[str, ...]]:
    return merge_request_binding.journal


@pytest.fixture
def review_seeder(merge_request_binding: _MergeRequestBinding) -> Callable[..., object]:
    """Seed one human review on a change request through the binding's provider state."""
    return merge_request_binding.seed_review


def _github_comment_journal(
    emulator: GitHubApiEmulator,
) -> Callable[[ChangeRequestRef], tuple[str, ...]]:
    """Comments of a PR as the provider renders them (markers are invisible)."""

    def journal(cr: ChangeRequestRef) -> tuple[str, ...]:
        return tuple(visible_comment(body) for body in emulator.comments_of(cr.number))

    return journal


def _github_review_seeder(emulator: GitHubApiEmulator) -> Callable[..., object]:
    """Seed one review through the provider state, in the port's neutral vocabulary."""

    def seed(
        cr: ChangeRequestRef,
        *,
        author: str,
        state: str,
        commit_sha: str | None = None,
        submitted_at: datetime | None = None,
    ) -> object:
        return emulator.seed_review(
            cr.number,
            author=author,
            state=state.upper(),
            commit_sha=commit_sha,
            submitted_at=submitted_at.isoformat() if submitted_at is not None else None,
        )

    return seed


@pytest.fixture(params=["fake", "github"])
def pipeline_port(request: pytest.FixtureRequest) -> PipelinePort:
    """PipelinePort bound to the fake and the GitHub adapter (ADR-019 p.6)."""
    if request.param == "github":
        adapter: GitHubAdapter = request.getfixturevalue("github_adapter")
        return adapter.pipelines
    return FakePipelines()


@pytest.fixture(params=["fake", "github"])
def ci_binding(request: pytest.FixtureRequest) -> _CIBinding:
    """CIPort with its dispatch journal and gate/artifact seeds, one adapter."""
    if request.param == "github":
        adapter: GitHubAdapter = request.getfixturevalue("github_adapter")
        emulator: GitHubApiEmulator = request.getfixturevalue("github_emulator")
        return _CIBinding(
            port=adapter.ci,
            journal=emulator.run_count,
            seed_gate=_github_seed_gate(emulator),
            seed_artifacts=_github_seed_artifacts(emulator),
        )
    fake = FakeCI()
    return _CIBinding(
        port=fake,
        journal=fake.dispatch_count,
        seed_gate=fake.seed_outcome,
        seed_artifacts=fake.seed_artifacts,
    )


@pytest.fixture
def ci_port(ci_binding: _CIBinding) -> CIPort:
    return ci_binding.port


@pytest.fixture
def stage_job_journal(ci_binding: _CIBinding) -> Callable[[], int]:
    """Number of dispatched stage jobs; a replay is counted only once."""
    return ci_binding.journal


@pytest.fixture
def seed_gate(ci_binding: _CIBinding) -> Callable[..., None]:
    """Fix the terminal gate outcome of a dispatched job."""
    return ci_binding.seed_gate


@pytest.fixture
def seed_artifacts(ci_binding: _CIBinding) -> Callable[..., None]:
    """Record the artifacts a dispatched job is expected to have produced."""
    return ci_binding.seed_artifacts


# GitHub's conclusion vocabulary, from the fixture-side GateStatus values;
# pending is the default before seeding, so it has no conclusion.
_CONCLUSIONS: Mapping[GateStatus, str] = {
    GateStatus.PASSED: "success",
    GateStatus.FAILED: "failure",
    GateStatus.SKIPPED: "skipped",
}


def _github_seed_gate(emulator: GitHubApiEmulator) -> Callable[..., None]:
    """Seed the stage-named check run the adapter reads the gate from."""

    def seed(job_ref: str, *, status: GateStatus, summary: str | None = None) -> None:
        _, stage, run_id = parse_job_ref(job_ref)
        emulator.seed_check_run(
            run_id,
            name=f"dark-factory/{stage}",
            conclusion=_CONCLUSIONS[status],
            summary=summary,
        )

    return seed


def _github_seed_artifacts(emulator: GitHubApiEmulator) -> Callable[..., None]:
    def seed(job_ref: str, refs: Sequence[ArtifactRef]) -> None:
        _, _, run_id = parse_job_ref(job_ref)
        emulator.seed_artifacts(run_id, refs)

    return seed


@pytest.fixture
def plane_emulator() -> PlaneApiEmulator:
    """A fresh in-memory Plane API for one test."""
    return PlaneApiEmulator()


@pytest.fixture
def plane_adapter(plane_emulator: PlaneApiEmulator) -> PlaneTrackerAdapter:
    """The Plane tracker adapter bound to the emulator: no network, no real API key."""
    return PlaneTrackerAdapter(
        PlaneConfig(
            base_url=PLANE_API_BASE_URL,
            workspace_slug=WORKSPACE_SLUG,
            project_id=PROJECT_ID,
            repository=PRODUCT,
            api_key=PLANE_API_TOKEN,
        ),
        transport=plane_emulator.transport(),
    )


@pytest.fixture(params=["fake", "plane"])
def tracker_binding(request: pytest.FixtureRequest) -> _TrackerBinding:
    """TrackerPort bound to the fake and the Plane adapter, both seeded (ADR-013)."""
    change = _tracked_change()
    if request.param == "plane":
        emulator: PlaneApiEmulator = request.getfixturevalue("plane_emulator")
        adapter: PlaneTrackerAdapter = request.getfixturevalue("plane_adapter")
        emulator.seed_change("PLANE-42", change)
        return _TrackerBinding(
            port=adapter,
            change=change,
            statuses=emulator.statuses_of,
            approvals=emulator.approvals_of,
        )
    fake = FakeTracker()
    fake.seed("PLANE-42", change)
    return _TrackerBinding(
        port=fake,
        change=change,
        statuses=fake.statuses_of,
        approvals=fake.approvals_of,
    )


@pytest.fixture
def tracker_port(tracker_binding: _TrackerBinding) -> TrackerPort:
    return tracker_binding.port


@pytest.fixture
def tracked_change(tracker_binding: _TrackerBinding) -> Change:
    """The change seeded in the bound tracker; ``get_change`` finds it by external ref."""
    return tracker_binding.change


@pytest.fixture
def published_statuses(tracker_binding: _TrackerBinding) -> Callable[[str], tuple[str, ...]]:
    return tracker_binding.statuses


@pytest.fixture
def requested_approvals(tracker_binding: _TrackerBinding) -> Callable[[str], tuple[Gate, ...]]:
    return tracker_binding.approvals


@pytest.fixture
def harness_port() -> HarnessPort:
    return FakeHarness()


@pytest.fixture
def knowledge() -> FakeKnowledge:
    return FakeKnowledge()


@pytest.fixture
def knowledge_port(knowledge: FakeKnowledge) -> KnowledgePort:
    return knowledge


@pytest.fixture
def seeded_knowledge_port(knowledge: FakeKnowledge) -> KnowledgePort:
    """Knowledge seeded with one pinned repo source and one unpinned ADR."""
    knowledge.seed(SourceKind.REPO, "src/dark_factory", "abc123", "factory source")
    knowledge.seed(SourceKind.ADR, "docs/adr/ADR-001.md", None, "decision record")
    return knowledge


@pytest.fixture(params=["fake", "worktree"])
def execution_binding(request: pytest.FixtureRequest, tmp_path: Path) -> _ExecutionBinding:
    """ExecutionPort bound to the fake and the real worktree adapter (T-092, TD-022)."""
    if request.param == "worktree":
        revision = _seeded_mirror(tmp_path)
        port = WorktreeExecution(
            WorktreeExecutionConfig(
                root=tmp_path / "workspaces-root", mirror_root=tmp_path / "mirror"
            )
        )
        return _ExecutionBinding(
            port=port,
            revision=revision,
            ok_command=("git", "rev-parse", "HEAD"),
            fail_command=("sh", "-c", "echo worktree-command-failed >&2; exit 3"),
            state_command=("git", "diff", "--quiet"),  # 0 on a clean tree, 1 on a dirty one
            make_state_fail=_worktree_state_fail(port),
            seed_file=_worktree_seed_file(port),
            make_request=lambda: WorkspaceRequest(
                repository=PRODUCT, revision=revision, change_id="chg-001"
            ),
        )
    fake = FakeExecution()
    fake.seed_failure(("pytest", "tests/"))  # the deterministic fail_command of the fake
    return _ExecutionBinding(
        port=fake,
        revision="abc123",
        ok_command=("pytest", "-q"),
        fail_command=("pytest", "tests/"),
        state_command=("pytest", "-q"),
        make_state_fail=lambda _handle: fake.seed_failure(("pytest", "-q"), exit_code=1),
        seed_file=fake.seed_file,
        make_request=lambda: WorkspaceRequest(
            repository=PRODUCT, revision="abc123", change_id="chg-001"
        ),
    )


@pytest.fixture
def evidence_workspace(execution_binding: _ExecutionBinding) -> WorkspaceHandle:
    """A prepared workspace of the bound port with one seeded evidence file."""
    handle = asyncio.run(
        execution_binding.port.prepare_workspace(
            execution_binding.make_request(), idempotency_key="ws-evidence"
        )
    )
    execution_binding.seed_file(handle, "reports/pytest-report.xml", b"<testsuite tests='3'/>")
    return handle


@pytest.fixture(params=["fake", "local_mirror"])
def provisioning_binding(request: pytest.FixtureRequest, tmp_path: Path) -> _ProvisioningBinding:
    """RepositoryProvisioningPort bound to the fake and the local-mirror adapter (T067)."""
    if request.param == "local_mirror":
        mirror_root = tmp_path / "provisioning-mirror"
        return _ProvisioningBinding(
            port=LocalMirror(LocalMirrorConfig(mirror_root=mirror_root)),
            repository=PRODUCT,
            supports_bootstrap=False,
            seed=lambda state: _seed_local_mirror(mirror_root, PRODUCT, state),
        )
    fake = FakeRepositoryProvisioning()
    return _ProvisioningBinding(
        port=fake,
        repository=PRODUCT,
        supports_bootstrap=True,
        seed=lambda state: fake.seed(PRODUCT, state),
    )


def _seeded_mirror(tmp_path: Path) -> str:
    """An operator-prepared local mirror of ``PRODUCT``; returns its HEAD sha."""
    source = tmp_path / "mirror" / "github" / "small" / "pilot"
    source.mkdir(parents=True)
    _git(source, "init", "-b", "main")
    (source / "docs").mkdir()
    (source / "docs" / "note.md").write_bytes(b"note\n")
    _git(source, "add", ".")
    _git(
        source,
        "-c",
        "user.email=factory@example.com",
        "-c",
        "user.name=Dark Factory",
        "commit",
        "-m",
        "seed",
    )
    return _git(source, "rev-parse", "HEAD").strip()


def _git(cwd: Path, *argv: str) -> str:
    """Run one git command in ``cwd`` and return its stdout (local repository, no network)."""
    process = subprocess.run(
        ("git", "-C", str(cwd), "-c", "commit.gpgsign=false", *argv),
        check=True,
        capture_output=True,
        text=True,
    )
    return process.stdout


def _seed_local_mirror(mirror_root: Path, repository: RepositoryRef, state: str) -> str | None:
    """Materialise one repository state under ``mirror_root``; returns its head sha.

    ``unavailable`` leaves no mirror at all, ``empty`` is an unborn HEAD, and the
    two baseline states commit a tree with or without ``.factory/product``
    (ADR-031 p.4, ADR-020).
    """
    path = mirror_root / repository.provider.value / repository.slug
    if path.exists():
        shutil.rmtree(path)
    if state == "unavailable":
        return None
    path.mkdir(parents=True)
    _git(path, "init", "-b", "main")
    if state == "empty":
        return None
    (path / "docs").mkdir()
    (path / "docs" / "note.md").write_bytes(b"note\n")
    if state == "baseline_current":
        (path / ".factory" / "product").mkdir(parents=True)
        (path / ".factory" / "product" / "product.yaml").write_bytes(b"name: pilot\n")
    _git(path, "add", ".")
    _git(
        path,
        "-c",
        "user.email=factory@example.com",
        "-c",
        "user.name=Dark Factory",
        "commit",
        "-m",
        "seed",
    )
    return _git(path, "rev-parse", "HEAD").strip()


def _worktree_seed_file(port: WorktreeExecution) -> Callable[[WorkspaceHandle, str, bytes], None]:
    """``write_file`` through the port: the adapter applies its own path safety."""

    def seed(handle: WorkspaceHandle, path: str, content: bytes) -> None:
        asyncio.run(port.write_file(handle, path, content, idempotency_key=f"seed:{path}"))

    return seed


def _worktree_state_fail(port: WorktreeExecution) -> Callable[[WorkspaceHandle], None]:
    """Dirty the tracked file, so the binding's ``git diff --quiet`` fails next."""

    def fail(handle: WorkspaceHandle) -> None:
        asyncio.run(port.write_file(handle, "docs/note.md", b"changed\n", idempotency_key="dirty"))

    return fail


@pytest.fixture
def artifact_store_port() -> ArtifactStorePort:
    return FakeArtifactStore()


@pytest.fixture(params=["fake", "otlp"])
def telemetry_binding(request: pytest.FixtureRequest) -> _TelemetryBinding:
    """TelemetryPort bound to the fake and the OTel adapter (T-060, ADR-009)."""
    if request.param == "otlp":
        return _otlp_telemetry_binding()
    fake = FakeTelemetry()
    return _TelemetryBinding(port=fake, spans=fake.recorded_spans, usage=fake.recorded_usage)


def _otlp_telemetry_binding() -> _TelemetryBinding:
    """The OTel adapter over an in-memory exporter that doubles as its journal."""
    exporter = InMemorySpanExporter()
    return _TelemetryBinding(
        port=OtlpTelemetryAdapter(TelemetryConfig(), exporter=exporter),
        spans=lambda: _exported_spans(exporter),
        usage=lambda: _exported_usage(exporter),
    )


def _exported_spans(exporter: InMemorySpanExporter) -> tuple[Span, ...]:
    """Finished spans as port values, in completion order and without usage spans."""
    return tuple(
        Span(
            name=recorded.name,
            attributes={
                key: str(value)
                for key, value in (recorded.attributes or {}).items()
                if not key.startswith(USAGE_ATTRIBUTE_PREFIX)
            },
        )
        for recorded in exporter.get_finished_spans()
        if recorded.name != USAGE_SPAN_NAME
    )


def _exported_usage(exporter: InMemorySpanExporter) -> tuple[tuple[Usage, dict[str, str]], ...]:
    """Usage spans decoded back into ``Usage`` plus the attributes of the call."""
    records: list[tuple[Usage, dict[str, str]]] = []
    for recorded in exporter.get_finished_spans():
        if recorded.name != USAGE_SPAN_NAME:
            continue
        attributes: Mapping[str, object] = recorded.attributes or {}
        records.append(
            (
                Usage(
                    prompt_tokens=_int_attribute(attributes, "prompt_tokens"),
                    completion_tokens=_int_attribute(attributes, "completion_tokens"),
                    total_tokens=_optional_int_attribute(attributes, "total_tokens"),
                    cost=_optional_decimal_attribute(attributes, "cost"),
                ),
                {
                    key: str(value)
                    for key, value in attributes.items()
                    if not key.startswith(USAGE_ATTRIBUTE_PREFIX)
                },
            )
        )
    return tuple(records)


def _int_attribute(attributes: Mapping[str, object], name: str) -> int:
    return int(str(attributes[f"{USAGE_ATTRIBUTE_PREFIX}{name}"]))


def _optional_int_attribute(attributes: Mapping[str, object], name: str) -> int | None:
    value = attributes.get(f"{USAGE_ATTRIBUTE_PREFIX}{name}")
    return None if value is None else int(str(value))


def _optional_decimal_attribute(attributes: Mapping[str, object], name: str) -> Decimal | None:
    value = attributes.get(f"{USAGE_ATTRIBUTE_PREFIX}{name}")
    return None if value is None else Decimal(str(value))


@pytest.fixture
def telemetry_port(telemetry_binding: _TelemetryBinding) -> TelemetryPort:
    return telemetry_binding.port


@pytest.fixture
def recorded_spans(telemetry_binding: _TelemetryBinding) -> Callable[[], tuple[Span, ...]]:
    return telemetry_binding.spans


@pytest.fixture
def recorded_usage(
    telemetry_binding: _TelemetryBinding,
) -> Callable[[], tuple[tuple[Usage, dict[str, str]], ...]]:
    return telemetry_binding.usage


@pytest.fixture
def workflow_engine() -> FakeWorkflowEngine:
    return FakeWorkflowEngine()


@pytest.fixture
def workflow_engine_port(workflow_engine: FakeWorkflowEngine) -> WorkflowEnginePort:
    return workflow_engine


@pytest.fixture
def waiting_run(workflow_engine: FakeWorkflowEngine) -> str:
    """A started run parked in ``waiting``; resume must move it back to running."""
    run_id = asyncio.run(workflow_engine.start(idempotency_key="start-waiting"))
    workflow_engine.set_status(run_id, RunStatus.WAITING)
    return run_id


@pytest.fixture
def reconciliation_service() -> ReconciliationService:
    return FakeReconciliationService()


@pytest.fixture
def publisher() -> FakeEventPublisher:
    return FakeEventPublisher()


@pytest.fixture
def event_publisher_port(publisher: FakeEventPublisher) -> EventPublisherPort:
    return publisher


@pytest.fixture
def recorded_events(publisher: FakeEventPublisher) -> Callable[[], tuple[DomainEvent, ...]]:
    return publisher.recorded_events


@pytest.fixture
def sdd_factory_root(tmp_path: Path) -> Path:
    """A seeded ``.factory/`` Product Baseline for the SDD contract suite."""
    root = tmp_path / ".factory"
    seed_baseline(root)
    return root


@pytest.fixture
def sdd_revision(sdd_factory_root: Path) -> str:
    """Current baseline revision of the seeded factory root."""
    return current_revision(sdd_factory_root)


@pytest.fixture(params=["native", "speckit", "openspec"])
def sdd_port(sdd_factory_root: Path, tmp_path: Path, request: pytest.FixtureRequest) -> SDDPort:
    """SDDPort bound to every adapter over one shared factory root (ADR-020 p.8)."""
    native = NativeChangeSetAdapter(sdd_factory_root)
    if request.param == "speckit":
        return SpecKitAdapter(native, tmp_path / "specs")
    if request.param == "openspec":
        return OpenSpecAdapter(native, tmp_path / "openspec")
    return native
