"""Tests of the runtime composition root (T-092 S2, ADR-024 p.5).

The composition root is configuration in, adapters out: these tests pin that a
missing configuration yields an *absent* piece (never a fallback), that a
complete one assembles the bindings of the working path, and that the layer
holds no behaviour of its own.
"""

import asyncio
import subprocess
from pathlib import Path
from typing import Any

import pytest

from dark_factory.adapters.fakes import FakeExecution
from dark_factory.adapters.harness import PydanticAIHarness
from dark_factory.adapters.scm.github import GitHubAdapter, StaticTokenProvider
from dark_factory.adapters.telemetry import OtlpTelemetryAdapter
from dark_factory.agents.profiles.registry import DEVELOP_PROFILE
from dark_factory.execution import (
    WORKSPACE_MIRROR_ROOT_ENV_VAR,
    WORKSPACE_ROOT_ENV_VAR,
    WorktreeExecution,
)
from dark_factory.orchestration.stages.agent import AgentStageExecutor, ScmRevision
from dark_factory.orchestration.stages.tools import WorkspaceTools
from dark_factory.ports import WorkspaceRequest
from dark_factory.runtime import RuntimeNotConfiguredError, build_runtime
from dark_factory.runtime.facts import ScmFactsProvider
from tests.changes_factories import make_change

LLM_ENV = {
    "DARK_FACTORY_LLM_BASE_URL": "https://llm.example/v1",
    "DARK_FACTORY_LLM_API_KEY": "secret-value",
    "DARK_FACTORY_LLM_MODEL": "openai/gpt-4o-mini",
}
GITHUB_ENV = {
    "DARK_FACTORY_GITHUB_APP_ID": "1",
    "DARK_FACTORY_GITHUB_APP_PRIVATE_KEY": "-----BEGIN KEY-----\n-----END KEY-----",
    "DARK_FACTORY_GITHUB_INSTALLATION_ID": "2",
}


def _static_tokens() -> StaticTokenProvider:
    return StaticTokenProvider("installation-token")


def _git(cwd: Path, *argv: str) -> None:
    """Run one git command in ``cwd`` (local repository, no network)."""
    subprocess.run(
        ("git", "-C", str(cwd), "-c", "commit.gpgsign=false", *argv),
        check=True,
        capture_output=True,
    )


def _seeded_mirror(tmp_path: Path) -> Path:
    """An operator-prepared local mirror of the product repository (T-092)."""
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
    return tmp_path / "mirror"


def _workspace_env(tmp_path: Path, mirror_root: Path) -> dict[str, str]:
    """A complete workspace configuration: absolute workspace and mirror roots."""
    return {
        WORKSPACE_ROOT_ENV_VAR: str(tmp_path / "workspaces-root"),
        WORKSPACE_MIRROR_ROOT_ENV_VAR: str(mirror_root),
    }


def test_unconfigured_runtime_is_valid_but_has_no_provider_or_harness() -> None:
    runtime = build_runtime(env={}, token_provider=_static_tokens())

    assert isinstance(runtime.telemetry, OtlpTelemetryAdapter)  # console default, always present
    assert runtime.github is None
    assert runtime.repository is None
    assert runtime.merge_requests is None
    assert runtime.harness_config is None
    assert runtime.agent_stage_executor() is None
    assert runtime.revision_of() is None
    assert runtime.facts_provider() is None


def test_configured_runtime_assembles_the_provider_ports() -> None:
    runtime = build_runtime(env=GITHUB_ENV, token_provider=_static_tokens())

    assert isinstance(runtime.github, GitHubAdapter)
    assert runtime.repository is runtime.github.repository
    assert runtime.merge_requests is runtime.github.pull_requests
    assert runtime.revision_of() is not None
    assert isinstance(runtime.facts_provider(), ScmFactsProvider)


def test_complete_runtime_builds_the_agent_stage_executor() -> None:
    # An explicitly injected execution port wins over the environment.
    runtime = build_runtime(
        env={**LLM_ENV, **GITHUB_ENV},
        execution=FakeExecution(),
        token_provider=_static_tokens(),
    )

    executor = runtime.agent_stage_executor()
    assert isinstance(executor, AgentStageExecutor)


def test_executor_needs_the_execution_port_as_well() -> None:
    # Without the workspace configuration the worktree adapter stays absent:
    # a runtime without it cannot run an agent stage and says so instead of
    # failing later.
    runtime = build_runtime(env={**LLM_ENV, **GITHUB_ENV}, token_provider=_static_tokens())
    assert runtime.execution is None
    assert runtime.agent_stage_executor() is None


def test_workspace_configuration_builds_the_worktree_execution_adapter(tmp_path: Path) -> None:
    mirror_root = _seeded_mirror(tmp_path)

    runtime = build_runtime(
        env={**LLM_ENV, **GITHUB_ENV, **_workspace_env(tmp_path, mirror_root)},
        token_provider=_static_tokens(),
    )

    assert isinstance(runtime.execution, WorktreeExecution)
    assert runtime.agent_stage_executor() is not None


def test_explicit_execution_injection_wins_over_the_environment(tmp_path: Path) -> None:
    injected = FakeExecution()

    runtime = build_runtime(
        env={**LLM_ENV, **GITHUB_ENV, **_workspace_env(tmp_path, _seeded_mirror(tmp_path))},
        execution=injected,
        token_provider=_static_tokens(),
    )

    assert runtime.execution is injected


def test_broken_workspace_configuration_is_not_swallowed(tmp_path: Path) -> None:
    # Fail-closed like telemetry: a set-but-invalid workspace variable is a
    # misconfiguration (an error), not an absent adapter.
    with pytest.raises(ValueError, match=WORKSPACE_ROOT_ENV_VAR):
        build_runtime(
            env={
                **LLM_ENV,
                **GITHUB_ENV,
                WORKSPACE_ROOT_ENV_VAR: "relative/workspaces",
                WORKSPACE_MIRROR_ROOT_ENV_VAR: str(tmp_path / "mirror"),
            },
            token_provider=_static_tokens(),
        )


def test_harness_factory_binds_the_role_tools() -> None:
    runtime = build_runtime(env=LLM_ENV, token_provider=_static_tokens())
    execution = FakeExecution()
    change = make_change()
    workspace = asyncio.run(
        execution.prepare_workspace(
            WorkspaceRequest(repository=change.product, revision="abc", change_id=change.id),
            idempotency_key="ws-1",
        )
    )
    tools = WorkspaceTools(execution, workspace).tools_for(DEVELOP_PROFILE)

    harness = runtime.harness_of(DEVELOP_PROFILE, tools)
    assert isinstance(harness, PydanticAIHarness)


def test_unconfigured_harness_factory_raises() -> None:
    runtime = build_runtime(env={}, token_provider=_static_tokens())
    with pytest.raises(RuntimeNotConfiguredError, match="DARK_FACTORY_LLM"):
        runtime.harness_of(DEVELOP_PROFILE, ())


def test_broken_telemetry_configuration_is_not_swallowed() -> None:
    # Unlike the harness/provider configs, telemetry is fail-closed: a typo is
    # an error, not an absent adapter (adapters/telemetry/config.py).
    with pytest.raises(ValueError, match="DARK_FACTORY_TELEMETRY_FILE"):
        build_runtime(
            env={"DARK_FACTORY_TELEMETRY_EXPORTER": "file"}, token_provider=_static_tokens()
        )


def test_aclose_releases_the_assembled_adapters() -> None:
    runtime = build_runtime(env=GITHUB_ENV, token_provider=_static_tokens())
    asyncio.run(runtime.aclose())


def test_revision_resolver_is_an_scm_revision_when_configured() -> None:
    runtime = build_runtime(env=GITHUB_ENV, token_provider=_static_tokens())
    resolver = runtime.revision_of()
    assert isinstance(resolver, ScmRevision)


# --- the GitOps repository adapter (T-092 S4, ADR-024 §7 S4) -----------------

GITOPS_ENV = {
    "DARK_FACTORY_GITOPS_APP_ID": "3",
    "DARK_FACTORY_GITOPS_APP_PRIVATE_KEY": "-----BEGIN KEY2-----\n-----END KEY2-----",
    "DARK_FACTORY_GITOPS_INSTALLATION_ID": "4",
    "DARK_FACTORY_GITOPS_REPOSITORY_SLUG": "small/gitops",
}


def test_without_the_gitops_block_there_is_no_release_executor() -> None:
    runtime = build_runtime(env={}, token_provider=_static_tokens())

    assert runtime.gitops is None
    assert runtime.release_stage_executor(inner=_never_called) is None


def _never_called(context: Any) -> Any:
    raise AssertionError("the inner executor must not be called at build time")


def test_gitops_block_assembles_a_second_independent_adapter() -> None:
    runtime = build_runtime(env=GITOPS_ENV, token_provider=_static_tokens())

    assert isinstance(runtime.gitops, GitHubAdapter)
    assert runtime.gitops is not runtime.github
    assert runtime.gitops_config is not None
    assert runtime.gitops_config.repository_slug == "small/gitops"


def test_release_executor_wraps_the_inner_executor_with_the_gitops_ports() -> None:
    runtime = build_runtime(env=GITOPS_ENV, token_provider=_static_tokens())

    executor = runtime.release_stage_executor(expected_digest="sha256:abc", inner=_never_called)

    assert executor is not None
    gitops = runtime.gitops
    assert gitops is not None
    assert executor._expected_digest == "sha256:abc"
    assert executor._gitops_repository.slug == "small/gitops"
    assert executor._repository is gitops.repository
    assert executor._merge_requests is gitops.pull_requests


def test_release_executor_without_an_inner_executor_refuses() -> None:
    runtime = build_runtime(env=GITOPS_ENV, token_provider=_static_tokens())

    with pytest.raises(RuntimeNotConfiguredError, match="inner executor"):
        runtime.release_stage_executor(inner=None)


def test_incomplete_gitops_block_fails_closed() -> None:
    # Any DARK_FACTORY_GITOPS_* variable makes the block live: a partial one is
    # a misconfiguration (an error), never a silently half-configured promotion.
    with pytest.raises(ValueError, match="DARK_FACTORY_GITOPS_INSTALLATION_ID"):
        build_runtime(
            env={
                "DARK_FACTORY_GITOPS_APP_ID": "3",
                "DARK_FACTORY_GITOPS_REPOSITORY_SLUG": "small/gitops",
            },
            token_provider=_static_tokens(),
        )
