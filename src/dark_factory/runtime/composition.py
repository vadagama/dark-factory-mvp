"""Assemble adapters from configuration into one :class:`Runtime` (ADR-024 p.5).

One function, one dataclass, no decisions: read the adapter configs from the
environment, build the adapters whose configuration is complete, and expose the
few bindings the working path needs — the harness of an agent stage, the SCM
ports, the SCM-derived revision resolver.

Optional pieces stay absent rather than degrading silently:

- no ``DARK_FACTORY_LLM_*`` → no harness (``harness_of`` raises when asked);
- no ``DARK_FACTORY_GITHUB_*`` → no repository/change-request ports;
- no ``DARK_FACTORY_GITHUB_REPOSITORY_SLUG`` → no CI stage toggle port
  (``ci_stage_toggles`` is ``None``; T059/ADR-027);
- no workspace configuration (``DARK_FACTORY_WORKSPACE_ROOT`` /
  ``DARK_FACTORY_WORKSPACE_MIRROR_ROOT``) → no execution port → no agent stage
  executor. The isolated-worktree adapter (T-092) mints workspaces from an
  operator-prepared local mirror, because cloning from a provider with
  credentials is a non-goal of the MVP (T-091 pods). An explicitly injected
  ``execution`` wins over the environment, like ``token_provider``/``transport``.

A configuration that is set must also be valid — fail-closed, because a silent
fallback would hide a typo: telemetry raises on an unknown exporter or a
``file`` exporter without a path (``adapters/telemetry/config.py``), and the
workspace configuration raises on a set-but-invalid variable instead of
treating it as an absence (``WorktreeExecutionConfig.from_env``, ADR-009).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from dark_factory.adapters.harness import HarnessConfig, PydanticAIHarness
from dark_factory.adapters.scm.github import GitHubAdapter, GitHubConfig, TokenProvider
from dark_factory.adapters.telemetry import OtlpTelemetryAdapter, TelemetryConfig
from dark_factory.agents.profiles.manifest import AgentProfile
from dark_factory.execution import WorktreeExecution, WorktreeExecutionConfig
from dark_factory.orchestration.stages.agent import (
    DEFAULT_BRANCH_PREFIX,
    DEFAULT_TARGET_BRANCH,
    AgentStageExecutor,
    ScmRevision,
)
from dark_factory.orchestration.stages.tools import ToolFunction
from dark_factory.ports import (
    CiStageTogglePort,
    ExecutionPort,
    HarnessPort,
    MergeRequestPort,
    RepositoryPort,
    TelemetryPort,
)

DEFAULT_BASE_REF: Final[str] = DEFAULT_TARGET_BRANCH
"""Base ref a change starts from when the product repository does not say otherwise."""


class RuntimeNotConfiguredError(RuntimeError):
    """A binding was requested whose configuration is incomplete."""


@dataclass(frozen=True, slots=True)
class Runtime:
    """The assembled adapters of one factory process (ADR-024 p.5).

    Frozen and passive: it holds adapters and the configuration they were built
    from, and offers the bindings of the working path. It owns no run logic —
    runs are driven by ``orchestration.runner``, which depends on the ports this
    runtime hands it.
    """

    telemetry: TelemetryPort
    github: GitHubAdapter | None = None
    github_config: GitHubConfig | None = None
    harness_config: HarnessConfig | None = None
    execution: ExecutionPort | None = None
    base_ref: str = DEFAULT_BASE_REF
    branch_prefix: str = DEFAULT_BRANCH_PREFIX

    @property
    def repository(self) -> RepositoryPort | None:
        """The product repository port, or ``None`` without a configured provider."""
        return None if self.github is None else self.github.repository

    @property
    def merge_requests(self) -> MergeRequestPort | None:
        """The change-request port, or ``None`` without a configured provider."""
        return None if self.github is None else self.github.pull_requests

    @property
    def ci_stage_toggles(self) -> CiStageTogglePort | None:
        """CI stage toggles of the factory repository (T059, ADR-027).

        ``None`` while no repository slug is configured: the console then shows
        the stage catalog with the controls disabled instead of a switchboard
        that cannot reach anything (fail-closed).
        """
        return None if self.github is None else self.github.ci_stage_toggles

    @property
    def ci_repository(self) -> str | None:
        """Slug of the repository whose CI stages the console controls, when configured."""
        return None if self.github_config is None else self.github_config.repository_slug

    def harness_of(self, profile: AgentProfile, tools: Sequence[ToolFunction]) -> HarnessPort:
        """Build the harness of one stage with the role's tools bound (ADR-007 p.3).

        The tools are bound here, not in the executor: a ``HarnessPort`` runs an
        envelope, and the PydanticAI adapter takes its tool set at construction,
        so the composition root is the place that joins a profile's tool names
        (resolved against the stage's workspace) with the harness that runs them.
        """
        if self.harness_config is None:
            raise RuntimeNotConfiguredError(
                "the harness is not configured: set the DARK_FACTORY_LLM_* variables"
            )
        return PydanticAIHarness(self.harness_config, role_tools={profile.role: tools})

    def agent_stage_executor(self) -> AgentStageExecutor | None:
        """The harness-backed stage executor, or ``None`` while a piece is missing.

        Requires the harness, the provider and the execution port; a runtime
        without them cannot run an agent stage, and returning ``None`` says so
        instead of failing later with a less obvious error.
        """
        if self.harness_config is None or self.github is None or self.execution is None:
            return None
        return AgentStageExecutor(
            harness_of=self.harness_of,
            repository=self.github.repository,
            merge_requests=self.github.pull_requests,
            execution=self.execution,
            telemetry=self.telemetry,
            target_branch=self.base_ref,
            branch_prefix=self.branch_prefix,
        )

    def revision_of(self) -> ScmRevision | None:
        """SCM-derived input revision resolver (ADR-006 p.4), if a provider is configured.

        The driver consumes this to key a stage by the product revision it starts
        from instead of the change snapshot's digest, so rework advances the
        revision and a reworked stage is a new operation, not a resumed one.
        """
        if self.github is None:
            return None
        return ScmRevision(
            self.github.repository, base_ref=self.base_ref, branch_prefix=self.branch_prefix
        )

    async def aclose(self) -> None:
        """Release the resources of the assembled adapters (HTTP pool, tracer provider)."""
        if self.github is not None:
            await self.github.aclose()
        if isinstance(self.telemetry, OtlpTelemetryAdapter):
            self.telemetry.shutdown()


def build_runtime(
    *,
    env: Mapping[str, str] | None = None,
    execution: ExecutionPort | None = None,
    token_provider: TokenProvider | None = None,
    transport: Any | None = None,
    base_ref: str = DEFAULT_BASE_REF,
    branch_prefix: str = DEFAULT_BRANCH_PREFIX,
) -> Runtime:
    """Assemble a :class:`Runtime` from ``env`` (default: the process environment).

    ``execution`` supplies the isolated-workspace port; an explicit injection
    wins over the environment, which otherwise builds the real worktree adapter
    from the ``DARK_FACTORY_WORKSPACE_*`` variables (``from_env`` returning
    ``None`` leaves the port absent). ``token_provider`` and ``transport``
    replace the GitHub App flow and HTTP transport (the contract emulator).
    Secrets are read from ``env`` only and never stored beyond the adapter that
    needs them (ADR-009).
    """
    telemetry = OtlpTelemetryAdapter(TelemetryConfig.from_env(env))
    github_config = GitHubConfig.from_env(env)
    github = (
        GitHubAdapter(github_config, token_provider=token_provider, transport=transport)
        if github_config is not None
        else None
    )
    if execution is None:
        workspace_config = WorktreeExecutionConfig.from_env(env)
        execution = None if workspace_config is None else WorktreeExecution(workspace_config)
    return Runtime(
        telemetry=telemetry,
        github=github,
        github_config=github_config,
        harness_config=HarnessConfig.from_env(env),
        execution=execution,
        base_ref=base_ref,
        branch_prefix=branch_prefix,
    )
