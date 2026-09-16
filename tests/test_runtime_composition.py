"""Tests of the runtime composition root (T-092 S2, ADR-024 p.5).

The composition root is configuration in, adapters out: these tests pin that a
missing configuration yields an *absent* piece (never a fallback), that a
complete one assembles the bindings of the working path, and that the layer
holds no behaviour of its own.
"""

import asyncio

import pytest

from dark_factory.adapters.fakes import FakeExecution
from dark_factory.adapters.harness import PydanticAIHarness
from dark_factory.adapters.scm.github import GitHubAdapter, StaticTokenProvider
from dark_factory.adapters.telemetry import OtlpTelemetryAdapter
from dark_factory.agents.profiles.registry import DEVELOP_PROFILE
from dark_factory.orchestration.stages.agent import AgentStageExecutor, ScmRevision
from dark_factory.orchestration.stages.tools import WorkspaceTools
from dark_factory.ports import WorkspaceRequest
from dark_factory.runtime import RuntimeNotConfiguredError, build_runtime
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


def test_unconfigured_runtime_is_valid_but_has_no_provider_or_harness() -> None:
    runtime = build_runtime(env={}, token_provider=_static_tokens())

    assert isinstance(runtime.telemetry, OtlpTelemetryAdapter)  # console default, always present
    assert runtime.github is None
    assert runtime.repository is None
    assert runtime.merge_requests is None
    assert runtime.harness_config is None
    assert runtime.agent_stage_executor() is None
    assert runtime.revision_of() is None


def test_configured_runtime_assembles_the_provider_ports() -> None:
    runtime = build_runtime(env=GITHUB_ENV, token_provider=_static_tokens())

    assert isinstance(runtime.github, GitHubAdapter)
    assert runtime.repository is runtime.github.repository
    assert runtime.merge_requests is runtime.github.pull_requests
    assert runtime.revision_of() is not None


def test_complete_runtime_builds_the_agent_stage_executor() -> None:
    runtime = build_runtime(
        env={**LLM_ENV, **GITHUB_ENV},
        execution=FakeExecution(),
        token_provider=_static_tokens(),
    )

    executor = runtime.agent_stage_executor()
    assert isinstance(executor, AgentStageExecutor)


def test_executor_needs_the_execution_port_as_well() -> None:
    # The isolated-workspace adapter does not exist yet: a runtime without it
    # cannot run an agent stage and says so instead of failing later.
    runtime = build_runtime(env={**LLM_ENV, **GITHUB_ENV}, token_provider=_static_tokens())
    assert runtime.agent_stage_executor() is None


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
