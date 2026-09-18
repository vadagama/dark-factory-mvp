"""Harness-backed stage executor: one stage attempt ran by a role agent (T-092 S2).

The deterministic executor (``orchestration.stages.executor``) folds machine
checks into a decision and never calls the harness (ADR-003). This module is the
other half of the ``StageExecutor`` seam (``orchestration.runner``): it runs the
stage through the LLM harness with the tools of the stage's role profile, in an
isolated workspace, and publishes the produced work as one commit on a task
branch plus a change request for CI to judge (TD-024).

What one attempt does, in order:

1. resolve the role of the stage and its profile (ADR-007; the release stage has
   no core profile yet, so it stops honestly instead of inventing one);
2. prepare the isolated workspace of the pinned input revision through
   ``ExecutionPort`` (FR-001: the revision is fixed before any work starts);
3. bind the profile's tools to that workspace (``WorkspaceTools``): only the
   names the profile declares, so role isolation is enforced by construction;
4. run one agent call with the skill's instruction as the prompt;
5. on success: read the changed file set back from the workspace
   (``collect_changes``), ensure the task branch at the pinned revision and
   publish the file set as one commit+push effect (``publish_commit``), then
   open the change request with the commit as its head — its description is
   Markdown rendered from the packaged template
   (``templates/pr-description.md``), not the raw tracker text; every effect
   under its deterministic idempotency key (FR-017, per-effect ledger of ADR-006 p.3);
6. an attempt whose workspace holds no changes stops in ``blocked`` before any
   external effect: "no changes" is a stage decision, never a silent empty
   change request; the review/verification stage is the one exception - it
   judges the open change request the change already has (ADR-005 p.2) and
   writes no file by design, so it publishes that request and parks on it;
7. on failure: stop the stage in ``blocked`` with a human-readable reason — a
   retryable attempt (ADR-006 p.7), not a papered-over success.

The result is ``waiting``: no gate is evaluated here. Machine gates run on the
final SHA in CI (FR-009, SC-004), so the attempt reports the required gates as
``pending`` and names the change request it waits on. Evaluating them is slice
S3; claiming them here would be the one thing this slice must not do.

Error policy: an unreachable harness, SCM or workspace never escapes into the
flow — it becomes a ``blocked`` attempt with the exception *type* only, because
provider exception texts can embed URLs or credentials (ADR-009). The async
ports are driven from the synchronous ``StageExecutor`` seam through a private
event loop (``asyncio.run``): the driver owns a transaction, not an event loop,
and slice S2 must not reshape the driver (ADR-024 p.5).
"""

import asyncio
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager, nullcontext
from typing import Final

from dark_factory.agents.artifacts import ArtifactKind
from dark_factory.agents.errors import ProfileNotFoundError
from dark_factory.agents.profiles.manifest import AgentProfile
from dark_factory.agents.profiles.registry import get_profile
from dark_factory.agents.skills.manifest import SkillManifest
from dark_factory.agents.skills.registry import get_skill
from dark_factory.changes.enums import ChangeRequestStatus, Role, Stage, StageStatus, StopOutcome
from dark_factory.changes.keys import effect_key, operation_key
from dark_factory.changes.next_action import (
    StopAction,
    WaitForCIAction,
    WaitForInputAction,
)
from dark_factory.changes.refs import ArtifactRef, ChangeRequestRef, RepositoryRef
from dark_factory.changes.run import Change, StageResult
from dark_factory.orchestration.stages.checks import pending_gate_results
from dark_factory.orchestration.stages.context import StageContext
from dark_factory.orchestration.stages.pr_description import PrDescriptionRenderer
from dark_factory.orchestration.stages.tools import ToolFunction, WorkspaceTools
from dark_factory.ports import (
    ExecutionPort,
    HarnessPort,
    MergeRequestPort,
    OpenChangeRequest,
    RepositoryPort,
    TaskEnvelope,
    TelemetryPort,
    Usage,
    WorkspaceHandle,
    WorkspaceRequest,
)

STAGE_ROLE: Final[Mapping[Stage, Role]] = {
    Stage.SPECIFICATION: Role.PRODUCT,
    Stage.PLANNING: Role.PRODUCT,
    Stage.CONSTRUCTION: Role.DEVELOP,
    Stage.REVIEW_VERIFICATION: Role.QUALITY,
    Stage.RELEASE: Role.CI_CD,
}
"""Role that owns each stage (ADR-007, ADR-005).

Specification and planning are product work (requirements and the proposed
change), construction is development, review/verification is quality, and
release is delivery. ``ci_cd`` has no core profile yet, so the release stage
stops in ``blocked`` until its profile lands — the honest outcome, not a
fabricated agent.
"""

STAGE_SKILL: Final[Mapping[Stage, str]] = {
    Stage.SPECIFICATION: "spec-authoring",
    Stage.PLANNING: "change-request",
    Stage.CONSTRUCTION: "implementation",
    Stage.REVIEW_VERIFICATION: "code-review",
}
"""Skill whose instruction becomes the prompt of the stage's call (T-011).

The stage is the unit of work and the skill is the unit of instruction, so the
mapping lives with the executor: the calling side owns the "skill → envelope
prompt" link (docs/descriptions/agents.md §6).
"""

DEFAULT_TARGET_BRANCH: Final[str] = "main"
"""Base branch a change request targets (ADR-011: merge — human decision on ``main``)."""

DEFAULT_BRANCH_PREFIX: Final[str] = "factory"
"""Prefix of the task branch the executor ensures; the change id completes it."""

type HarnessFactory = Callable[[AgentProfile, Sequence[ToolFunction]], HarnessPort]
"""Builds the harness of one stage with the tools of the stage's role bound.

The ``HarnessPort`` contract runs an envelope, not a tool set, and the PydanticAI
adapter binds tools at construction (``role_tools``); the executor therefore
builds the harness of an attempt through this seam instead of mutating a shared
one. Composition supplies the production factory (``PydanticAIHarness`` with
``role_tools={profile.role: tools}``); tests supply a fake.
"""

_EFFECT_WORKSPACE: Final[str] = "workspace"
_EFFECT_BRANCH: Final[str] = "branch"
_EFFECT_COMMIT: Final[str] = "commit"
_EFFECT_CHANGE_REQUEST: Final[str] = "change_request"
_EFFECT_COLLECT_CHANGES: Final[str] = "collect_changes"


def branch_name(change_id: str, *, prefix: str = DEFAULT_BRANCH_PREFIX) -> str:
    """Deterministic task branch of a change: ``<prefix>/<slug(change_id)>``.

    The slug keeps only git-ref-safe characters, so a change id that carries
    separators (``chg:product:0001``) still maps onto one stable branch name —
    the same address every attempt of the change resolves to (FR-017).
    """
    slug = "".join(char if char.isalnum() or char in "._-" else "-" for char in change_id)
    slug = slug.strip("-.") or "change"
    return f"{prefix}/{slug}"


class AgentStageExecutor:
    """``StageExecutor`` that runs a stage attempt through the harness (T-092 S2).

    All port dependencies are injected (ADR-015 p.3): the executor knows the
    contracts, never an SDK. ``profile_of`` and ``skill_of`` are seams for tests
    and for the profile expansion of T-046/T-047; by default they are the
    built-in registries. ``descriptions`` is the renderer that shapes the
    published change-request body; by default the packaged template
    (``templates/pr-description.md``).
    """

    def __init__(
        self,
        *,
        harness_of: HarnessFactory,
        repository: RepositoryPort,
        merge_requests: MergeRequestPort,
        execution: ExecutionPort,
        telemetry: TelemetryPort | None = None,
        descriptions: PrDescriptionRenderer | None = None,
        target_branch: str = DEFAULT_TARGET_BRANCH,
        branch_prefix: str = DEFAULT_BRANCH_PREFIX,
        profile_of: Callable[[Role], AgentProfile] = get_profile,
        skill_of: Callable[[str], SkillManifest] = get_skill,
    ) -> None:
        self._harness_of = harness_of
        self._repository = repository
        self._merge_requests = merge_requests
        self._execution = execution
        self._telemetry = telemetry
        self._descriptions = (
            descriptions if descriptions is not None else PrDescriptionRenderer.default()
        )
        self._target_branch = target_branch
        self._branch_prefix = branch_prefix
        self._profile_of = profile_of
        self._skill_of = skill_of

    # --- StageExecutor seam ------------------------------------------------

    def __call__(self, context: StageContext) -> StageResult:
        """Synchronous ``StageExecutor`` seam: run one attempt (blocking).

        The driver is synchronous and owns a database transaction, so the async
        ports are driven here through a private loop. Calling this from within a
        running loop is a programming error and fails loudly (``asyncio.run``).
        """
        return asyncio.run(self.execute(context))

    async def execute(self, context: StageContext) -> StageResult:
        """Run one stage attempt asynchronously (the real implementation)."""
        role = STAGE_ROLE[context.stage]
        try:
            profile = self._profile_of(role)
        except ProfileNotFoundError as exc:
            return self._blocked(context, f"stage {context.stage.value}: {exc}")
        skill_id = STAGE_SKILL.get(context.stage)
        if skill_id is None:
            return self._blocked(
                context,
                f"stage {context.stage.value}: no skill is mapped to this stage"
                f" (role {role.value!r})",
            )
        if context.input_revision is None:
            return self._blocked(
                context,
                f"stage {context.stage.value}: the attempt has no pinned input revision",
            )
        try:
            return await self._attempt(context, profile, self._skill_of(skill_id))
        except Exception as exc:
            return self._blocked(
                context,
                f"stage {context.stage.value}: agent stage failed at the boundary"
                f" ({type(exc).__name__})",
            )

    # --- one attempt -------------------------------------------------------

    async def _attempt(
        self, context: StageContext, profile: AgentProfile, skill: SkillManifest
    ) -> StageResult:
        """The harness call, then the branch and change request it produced."""
        identity = operation_key(context.run_id, context.stage, context.input_revision or "")
        workspace = await self._execution.prepare_workspace(
            WorkspaceRequest(
                repository=context.change.product,
                revision=context.input_revision or "",
                change_id=context.change.id,
            ),
            idempotency_key=effect_key(identity, _EFFECT_WORKSPACE, context.change.id),
        )
        tools = WorkspaceTools(self._execution, workspace).tools_for(profile)
        harness = self._harness_of(profile, tools)
        envelope = TaskEnvelope(
            change_id=context.change.id,
            run_id=context.run_id,
            stage=context.stage,
            role=profile.role,
            instruction=self._instruction(context, skill),
            skill_id=skill.id,
        )
        with self._span(context, profile):
            agent_result = await harness.run_stage(envelope)
        if not agent_result.ok:
            return self._blocked(
                context,
                f"stage {context.stage.value}: harness did not produce a result"
                " (see harness health for the missing configuration)",
                usage=agent_result.usage,
            )

        published = await self._publish(context, identity, workspace)
        if published is None:
            return self._blocked(
                context,
                f"stage {context.stage.value}: the attempt produced no changes to publish",
                usage=agent_result.usage,
            )
        artifacts, change_request = published
        return self._waiting(
            context, artifacts=artifacts, change_request=change_request, usage=agent_result.usage
        )

    async def _publish(
        self, context: StageContext, identity: str, workspace: WorkspaceHandle
    ) -> tuple[list[ArtifactRef], ChangeRequestRef] | None:
        """Publish the workspace changes as one commit; open the change request.

        Every effect is keyed deterministically (FR-017): a retry of the same
        logical operation resolves to the same commit, the same branch and the
        same change request instead of minting a second one (ADR-006 p.3). The
        order matters for the "no changes" decision: the file set is collected
        first, so an attempt with nothing to publish stops before the branch,
        the commit or the change request exists. ``None`` is that decision; the
        caller turns it into a retryable ``blocked`` attempt - except for the
        review/verification stage, which writes nothing by design and falls back
        to the request under review (:meth:`_published_under_review`).
        """
        repository = context.change.product
        branch = branch_name(context.change.id, prefix=self._branch_prefix)
        changes = await self._execution.collect_changes(
            workspace,
            idempotency_key=effect_key(identity, _EFFECT_COLLECT_CHANGES, context.change.id),
        )
        if not changes:
            return await self._published_under_review(context)
        await self._repository.ensure_branch(
            repository,
            branch,
            from_revision=context.input_revision or "",
            idempotency_key=effect_key(identity, _EFFECT_BRANCH, branch),
        )
        commit_sha = await self._repository.publish_commit(
            repository,
            branch,
            changes,
            message=f"factory: {context.change.id} ({context.stage.value})",
            idempotency_key=effect_key(identity, _EFFECT_COMMIT, branch),
        )
        description = self._descriptions.render(
            context,
            role=STAGE_ROLE[context.stage],
            source_branch=branch,
            target_branch=self._target_branch,
            commit_sha=commit_sha,
        )
        change_request = await self._merge_requests.find_existing(repository, context.change.id)
        if change_request is not None and change_request.status is not ChangeRequestStatus.OPEN:
            # A merged or closed request cannot carry the attempt's commit: no
            # open request stays on the branch, so CI never runs on the final
            # SHA and the parked stage can never resolve (found in T-043
            # increment 1, where the operator's merge of the specification
            # request is the human decision per ADR-011). The attempt opens a
            # fresh request instead; ``open`` is idempotent by head branch.
            change_request = None
        if change_request is None:
            change_request = await self._merge_requests.open(
                OpenChangeRequest(
                    repository=repository,
                    change_id=context.change.id,
                    source_branch=branch,
                    target_branch=self._target_branch,
                    title=context.change.title,
                    description=description,
                    head_sha=commit_sha,
                ),
                idempotency_key=effect_key(identity, _EFFECT_CHANGE_REQUEST, branch),
            )
        artifacts = [
            ArtifactRef(
                artifact_type=ArtifactKind.CHANGE_REQUEST.value,
                uri=change_request.url or f"{repository.slug}#{change_request.number}",
                revision=commit_sha,
                producer=STAGE_ROLE[context.stage].value,
            )
        ]
        return artifacts, change_request

    async def _published_under_review(
        self, context: StageContext
    ) -> tuple[list[ArtifactRef], ChangeRequestRef] | None:
        """The request a review stage judges when its agent changed nothing.

        Review/verification completes through ``merge`` (ADR-005 p.2) and so
        carries a change request (ADR-011), but it reviews the request the change
        already has and legitimately writes no file: the "no changes" decision
        must not block it. That open request is the published target; the
        artifact names the pinned input revision - the revision the reviewed
        request's CI ran on - because ``ChangeRequestRef`` carries no head SHA
        of its own. A merged or closed request is nothing under review, and a
        stage other than review/verification never takes this path: both stay an
        honest ``None`` for the caller to turn into a retryable ``blocked``
        attempt.
        """
        if context.stage is not Stage.REVIEW_VERIFICATION:
            return None
        repository = context.change.product
        change_request = await self._merge_requests.find_existing(repository, context.change.id)
        if change_request is None or change_request.status is not ChangeRequestStatus.OPEN:
            return None
        artifacts = [
            ArtifactRef(
                artifact_type=ArtifactKind.CHANGE_REQUEST.value,
                uri=change_request.url or f"{repository.slug}#{change_request.number}",
                revision=context.input_revision,
                producer=STAGE_ROLE[context.stage].value,
            )
        ]
        return artifacts, change_request

    # --- results -----------------------------------------------------------

    def _instruction(self, context: StageContext, skill: SkillManifest) -> str:
        """The prompt of one call: the skill's instruction plus the change facts."""
        lines = [
            skill.instruction,
            "",
            f"Change: {context.change.title}",
            f"Stage: {context.stage.value}",
            f"Attempt: {context.attempt_number}",
        ]
        if context.change.description:
            lines.append(f"Description: {context.change.description}")
        return "\n".join(lines)

    def _span(self, context: StageContext, profile: AgentProfile) -> AbstractContextManager[object]:
        """Telemetry span of one agent call, or a no-op when telemetry is absent."""
        if self._telemetry is None:
            return nullcontext()
        return self._telemetry.span(
            "factory.stage.agent",
            change_id=context.change.id,
            run_id=context.run_id,
            stage=context.stage.value,
            role=profile.role.value,
        )

    def _waiting(
        self,
        context: StageContext,
        *,
        artifacts: Sequence[ArtifactRef],
        change_request: ChangeRequestRef,
        usage: Usage | None = None,
    ) -> StageResult:
        """The stage produced its work and parks on its external wait (FR-009, SC-004).

        The wait follows the stage's gate character (``rules.gates``): a stage
        whose required gates are all human — the design stage on the base gate
        set — parks for the human decision, a stage with machine gates waits
        for CI on the final SHA. The machine set is the risk-aware one
        (``context.required_gates - context.human_gates``, ADR-028 p.3): a
        risk-widened human gate (``planning`` at R2) must park for the human,
        or the stage would wait for a pipeline verdict that can never resolve
        it. The flow transition table owns which waits a stage may take;
        producing an action outside it would crash the advance instead of
        parking the attempt (found in T-043 increment 1).
        """
        machine_gates = context.required_gates - context.human_gates
        produced = (
            f"stage {context.stage.value} produced {change_request.repository.slug}"
            f"#{change_request.number} at {self._branch(context)}"
        )
        action: WaitForCIAction | WaitForInputAction
        if machine_gates:
            action = WaitForCIAction(
                reason=f"{produced}; machine gates run on the final SHA in CI (FR-009)",
                change_request=change_request,
            )
        else:
            action = WaitForInputAction(
                reason=f"{produced}; the stage's gates are human decisions (ADR-018)"
            )
        return self._result(
            context,
            status=StageStatus.WAITING,
            next_action=action,
            artifacts=list(artifacts),
            usage=usage,
        )

    def _blocked(
        self, context: StageContext, reason: str, *, usage: Usage | None = None
    ) -> StageResult:
        """The attempt stopped before it could produce a result (retryable).

        ``blocked`` is a stop condition, not a failure terminal (ADR-018 p.5),
        and it is retryable: the next advance runs the next attempt of the same
        operation (ADR-006 p.7).
        """
        return self._result(
            context,
            status=StageStatus.BLOCKED,
            next_action=StopAction(outcome=StopOutcome.BLOCKED, reason=reason),
            usage=usage,
        )

    def _result(
        self,
        context: StageContext,
        *,
        status: StageStatus,
        next_action: StopAction | WaitForCIAction | WaitForInputAction,
        artifacts: Sequence[ArtifactRef] = (),
        usage: Usage | None = None,
    ) -> StageResult:
        """StageResult skeleton: identity from the context, gates pending (FR-009).

        No gate is evaluated on this path, so the required gates of the stage are
        reported ``pending`` exactly as the deterministic executor does — a
        ``pending`` result never satisfies a gate in the flow.
        """
        return StageResult(
            stage=context.stage,
            run_id=context.run_id,
            change_id=context.change.id,
            attempt_number=context.attempt_number,
            input_revision=context.input_revision,
            status=status,
            next_action=next_action,
            artifacts=list(artifacts),
            gate_results=pending_gate_results(context.required_gates),
            usage=usage,
        )

    def _branch(self, context: StageContext) -> str:
        return branch_name(context.change.id, prefix=self._branch_prefix)


class ScmRevision:
    """Sync revision resolver: the product commit a stage starts from (ADR-006 p.4).

    ``input_revision`` of a stage is the revision of the work it consumes. Using
    the change snapshot's digest (the S1 fallback) makes a reworked stage collide
    with its own earlier operation, because rework does not change the snapshot;
    the product commit does change. This resolver reads the task branch head —
    the revision rework actually advanced — and falls back to the change's base
    ref while the branch does not exist yet.

    It is synchronous because the driver is (``advance_run`` runs inside a
    database transaction); the async ``RepositoryPort`` is driven through a
    private loop, like the executor's own seam.
    """

    def __init__(
        self,
        repository: RepositoryPort,
        *,
        base_ref: str = DEFAULT_TARGET_BRANCH,
        branch_prefix: str = DEFAULT_BRANCH_PREFIX,
    ) -> None:
        self._repository = repository
        self._base_ref = base_ref
        self._branch_prefix = branch_prefix

    def __call__(self, change: Change, stage: Stage) -> str:
        """Resolve the revision ``change`` executes ``stage`` from.

        The task branch head wins once the branch exists — it is the revision
        rework actually advanced — and the change's base ref is the answer while
        the branch does not exist yet. A provider failure that is *not* a missing
        ref is not masked (:meth:`_known_revision`).
        """
        return asyncio.run(self._resolve(change))

    async def _resolve(self, change: Change) -> str:
        repository = change.product
        head = await self._known_revision(
            repository, branch_name(change.id, prefix=self._branch_prefix)
        )
        if head is not None:
            return head
        base = await self._known_revision(repository, self._base_ref)
        return base if base is not None else self._base_ref

    async def _known_revision(self, repository: RepositoryRef, ref: str) -> str | None:
        """Revision of ``ref``, or ``None`` when the repository does not have that ref.

        ``RepositoryPort.get_revision`` reports a missing ref as ``KeyError`` —
        the contract the fake and the GitHub adapter share (a 404 is "absent",
        not a failure). Every other exception propagates: masking a rejected
        token or an unreachable provider would key the operation by a guessed
        revision instead of failing the advance (ADR-006 p.3).
        """
        try:
            return await self._repository.get_revision(repository, ref)
        except KeyError:
            return None
