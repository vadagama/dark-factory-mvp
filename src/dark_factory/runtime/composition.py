"""Assemble adapters from configuration into one :class:`Runtime` (ADR-024 p.5).

One function, one dataclass, no decisions: read the adapter configs from the
environment, build the adapters whose configuration is complete, and expose the
few bindings the working path needs — the harness of an agent stage, the SCM
ports, the SCM-derived revision resolver, the provider-facts observer of the
wait resolution (T-092 S3).

Optional pieces stay absent rather than degrading silently:

- no ``DARK_FACTORY_LLM_*`` → no harness (``harness_of`` raises when asked);
- no ``DARK_FACTORY_GITHUB_*`` → no repository/change-request ports and no
  gate-facts observer (``facts_provider`` is ``None``);
- no ``DARK_FACTORY_GITHUB_REPOSITORY_SLUG`` → no CI stage toggle port
  (``ci_stage_toggles`` is ``None``; T059/ADR-027);
- no ``DARK_FACTORY_PR_TEMPLATE`` → change-request bodies are rendered from the
  packaged default template (``orchestration.stages.pr_description``);
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

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from dark_factory.adapters.harness import HarnessConfig, PydanticAIHarness
from dark_factory.adapters.provisioning import LocalMirror, LocalMirrorConfig
from dark_factory.adapters.scm.github import GitHubAdapter, GitHubConfig, TokenProvider
from dark_factory.adapters.scm.github.config import DEFAULT_API_BASE_URL
from dark_factory.adapters.telemetry import OtlpTelemetryAdapter, TelemetryConfig
from dark_factory.agents.profiles.manifest import AgentProfile
from dark_factory.changes.enums import Provider
from dark_factory.changes.refs import RepositoryRef
from dark_factory.execution import WorktreeExecution, WorktreeExecutionConfig
from dark_factory.orchestration.stages.agent import (
    DEFAULT_BRANCH_PREFIX,
    DEFAULT_TARGET_BRANCH,
    AgentStageExecutor,
    ScmRevision,
)
from dark_factory.orchestration.stages.pr_description import PrDescriptionRenderer
from dark_factory.orchestration.stages.release import (
    InnerStageExecutor,
    ReleaseStageExecutor,
)
from dark_factory.orchestration.stages.tools import ToolFunction
from dark_factory.ports import (
    CiStageTogglePort,
    ExecutionPort,
    HarnessPort,
    MergeRequestPort,
    RepositoryPort,
    RepositoryProvisioningPort,
    TelemetryPort,
)
from dark_factory.runtime.facts import ScmFactsProvider

DEFAULT_BASE_REF: Final[str] = DEFAULT_TARGET_BRANCH
"""Base ref a change starts from when the product repository does not say otherwise."""

GITOPS_APP_ID_ENV_VAR: Final[str] = "DARK_FACTORY_GITOPS_APP_ID"
GITOPS_APP_PRIVATE_KEY_ENV_VAR: Final[str] = "DARK_FACTORY_GITOPS_APP_PRIVATE_KEY"
GITOPS_INSTALLATION_ID_ENV_VAR: Final[str] = "DARK_FACTORY_GITOPS_INSTALLATION_ID"
GITOPS_API_URL_ENV_VAR: Final[str] = "DARK_FACTORY_GITOPS_API_URL"
GITOPS_REPOSITORY_SLUG_ENV_VAR: Final[str] = "DARK_FACTORY_GITOPS_REPOSITORY_SLUG"
"""Environment block of the GitOps repository adapter (T-092 S4, ADR-010/ADR-024 §7 S4).

The GitOps repository carries its own GitHub App credentials — the promotion
writes there under a separate identity from the product repository. The block
is self-contained: any ``DARK_FACTORY_GITOPS_*`` variable set requires all four
mandatory ones (app id, private key, installation id, repository slug), and a
set-but-blank mandatory variable is a misconfiguration, not an absence —
construction fails closed with a ``ValueError`` that names the variable, never
the value (ADR-009)."""

_GITOPS_REQUIRED_ENV_VARS: Final[tuple[str, ...]] = (
    GITOPS_APP_ID_ENV_VAR,
    GITOPS_APP_PRIVATE_KEY_ENV_VAR,
    GITOPS_INSTALLATION_ID_ENV_VAR,
    GITOPS_REPOSITORY_SLUG_ENV_VAR,
)


class GitOpsNotConfiguredError(ValueError):
    """The ``DARK_FACTORY_GITOPS_*`` block is set but incomplete (fail-closed, ADR-009)."""


@dataclass(frozen=True, slots=True)
class GitOpsConfig:
    """Adapter configuration of the GitOps repository the release stage promotes into."""

    api_base_url: str = DEFAULT_API_BASE_URL
    app_id: str | None = None
    installation_id: str | None = None
    private_key: str | None = None
    repository_slug: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "GitOpsConfig | None":
        """Config read from ``env``; ``None`` when the whole block is absent.

        Any set ``DARK_FACTORY_GITOPS_*`` variable makes the block live: the
        mandatory four must then be non-blank (a set-but-blank one is a
        misconfiguration, not an absence — fail closed, ADR-009). The optional
        API URL defaults to the public GitHub API.
        """
        source = os.environ if env is None else env
        names = (*_GITOPS_REQUIRED_ENV_VARS, GITOPS_API_URL_ENV_VAR)
        if not any(source.get(name) is not None for name in names):
            return None
        missing = [
            name for name in _GITOPS_REQUIRED_ENV_VARS if not (source.get(name) or "").strip()
        ]
        if missing:
            raise GitOpsNotConfiguredError(
                f"the GitOps repository is misconfigured: set {', '.join(missing)}"
                " (the DARK_FACTORY_GITOPS_* block is all-or-nothing)"
            )
        return cls(
            api_base_url=(source.get(GITOPS_API_URL_ENV_VAR) or DEFAULT_API_BASE_URL).strip(),
            app_id=(source.get(GITOPS_APP_ID_ENV_VAR) or "").strip(),
            installation_id=(source.get(GITOPS_INSTALLATION_ID_ENV_VAR) or "").strip(),
            private_key=source.get(GITOPS_APP_PRIVATE_KEY_ENV_VAR) or "",
            repository_slug=(source.get(GITOPS_REPOSITORY_SLUG_ENV_VAR) or "").strip(),
        )


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
    gitops: GitHubAdapter | None = None
    gitops_config: GitOpsConfig | None = None
    gitops_base_ref: str = DEFAULT_TARGET_BRANCH
    harness_config: HarnessConfig | None = None
    execution: ExecutionPort | None = None
    provisioning: RepositoryProvisioningPort | None = None
    """Repository-provisioning port of product validation (T066, ADR-031);
    ``None`` while no mirror root is configured."""
    descriptions: PrDescriptionRenderer | None = None
    """Renderer of change-request bodies; ``None`` lets the executor fall back
    to the packaged default template (``templates/pr-description.md``)."""
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
            descriptions=self.descriptions,
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

    def facts_provider(self) -> ScmFactsProvider | None:
        """The provider-facts observer of the wait resolution (T-092 S3), or ``None``.

        The driver consumes this to resume a stage parked in ``waiting`` (the
        durable external-wait checkpoint, ADR-006 p.8) once the observed
        provider facts resolve its wait. ``None`` while no provider is
        configured: without the request and pipeline ports a wait cannot be
        observed, and replaying the checkpoint is the honest answer (the
        scheduled reconciler pass is the other resolver).
        """
        if self.github is None:
            return None
        return ScmFactsProvider(self.github.pull_requests, self.github.pipelines)

    def release_stage_executor(
        self,
        *,
        expected_digest: str | None = None,
        inner: InnerStageExecutor | None = None,
    ) -> ReleaseStageExecutor | None:
        """The GitOps promotion executor of the release stage (T-092 S4), or ``None``.

        Requires the GitOps adapter; without ``DARK_FACTORY_GITOPS_*`` the
        release stage stays with ``inner`` — the honest pre-S4 behavior. The
        expected digest comes from the ``run advance`` flags (the XOR of
        ``--expected-digest``/``--digest-json``, resolved by the CLI); without
        one a fresh release attempt blocks honestly before any effect instead
        of promoting an invented release.
        """
        if self.gitops is None or self.gitops_config is None:
            return None
        if inner is None:
            raise RuntimeNotConfiguredError(
                "the release stage needs an inner executor: configure the harness"
                " and the execution port, or run the deterministic path"
            )
        return ReleaseStageExecutor(
            inner,
            repository=self.gitops.repository,
            merge_requests=self.gitops.pull_requests,
            gitops_repository=RepositoryRef(
                provider=Provider.GITHUB,
                slug=self.gitops_config.repository_slug or "",
            ),
            expected_digest=expected_digest,
            target_branch=self.gitops_base_ref,
        )

    async def aclose(self) -> None:
        """Release the resources of the assembled adapters (HTTP pool, tracer provider)."""
        if self.github is not None:
            await self.github.aclose()
        if self.gitops is not None:
            await self.gitops.aclose()
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
    descriptions: PrDescriptionRenderer | None = None,
) -> Runtime:
    """Assemble a :class:`Runtime` from ``env`` (default: the process environment).

    ``execution`` supplies the isolated-workspace port; an explicit injection
    wins over the environment, which otherwise builds the real worktree adapter
    from the ``DARK_FACTORY_WORKSPACE_*`` variables (``from_env`` returning
    ``None`` leaves the port absent). ``token_provider`` and ``transport``
    replace the GitHub App flow and HTTP transport (the contract emulator).
    ``descriptions`` supplies the change-request body renderer; an explicit
    injection wins over the environment (``DARK_FACTORY_PR_TEMPLATE``).
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
    gitops_config = GitOpsConfig.from_env(env)
    gitops = (
        GitHubAdapter(
            GitHubConfig(
                api_base_url=gitops_config.api_base_url,
                app_id=gitops_config.app_id,
                installation_id=gitops_config.installation_id,
                private_key=gitops_config.private_key,
            ),
            token_provider=token_provider,
            transport=transport,
        )
        if gitops_config is not None
        else None
    )
    if execution is None:
        workspace_config = WorktreeExecutionConfig.from_env(env)
        execution = None if workspace_config is None else WorktreeExecution(workspace_config)
    provisioning_config = LocalMirrorConfig.from_env(env)
    provisioning = None if provisioning_config is None else LocalMirror(provisioning_config)
    return Runtime(
        telemetry=telemetry,
        github=github,
        github_config=github_config,
        gitops=gitops,
        gitops_config=gitops_config,
        harness_config=HarnessConfig.from_env(env),
        execution=execution,
        provisioning=provisioning,
        descriptions=(
            descriptions if descriptions is not None else PrDescriptionRenderer.from_env(env)
        ),
        base_ref=base_ref,
        branch_prefix=branch_prefix,
    )
