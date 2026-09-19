"""Pure release resolver of the wait-resolution protocol (T-092 S4, ADR-024 §7 S4).

The release stage parked in ``waiting`` — the durable external-wait checkpoint
of ADR-006 p.8 — is resumed by the driver
(:func:`~dark_factory.orchestration.runner.advance_run`) once the release
facts it waits for resolve (ADR-024 §7 S4: digest → GitOps-MR → Argo sync →
smoke → release evidence). This module turns the observed facts
(:class:`~dark_factory.quality.release.decision.ReleaseObservation` — the
release core's own observation, re-exported here for the driver: the digest
observed on the deployment, the raw Argo Application sync/health statuses,
the smoke probes behind the
:class:`~dark_factory.quality.release.probes.SmokeProbe` seam) into the
:class:`StageResult` of that same attempt, deciding through the pure release
core (:func:`~dark_factory.quality.release.decision.evaluate_release`, T034)
and carrying the outcome as the additive ``StageResult.release`` evidence —
the stage-level counterpart of ``RunRecord.release`` (T034, FR-011/FR-013).

Deterministic and pure — no I/O, no provider calls, no clock beyond the
explicit ``now`` parameter (the same discipline as the neighboring
:mod:`~dark_factory.orchestration.stages.gates` resolver). Fail-closed: an
observation that does not carry the full release facts (the deployed digest
and both Argo statuses) never resolves the wait — partial data must not wake a
waiting attempt (ADR-006 p.8), and the builder refuses such an observation so
the driver replays the waiting checkpoint instead. A failed verification
carries the rollback signal: revert the GitOps commit that pinned the digest
(ADR-010, ADR-011 p.6).

The resolution result belongs to the waiting attempt: identity verbatim from
the checkpoint, so the durable store supersedes the waiting checkpoint of the
same attempt (ADR-006 p.8). What the *flow* then decides with the result is
its business, as in ``gates``.
"""

import asyncio
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from typing import Final

from dark_factory.agents.artifacts import ArtifactKind
from dark_factory.changes.enums import (
    Gate,
    GateStatus,
    ReleaseStatus,
    Role,
    Stage,
    StageStatus,
    StopOutcome,
)
from dark_factory.changes.findings import GateResult
from dark_factory.changes.keys import effect_key, operation_key
from dark_factory.changes.next_action import ReleaseAction, StopAction, WaitForCIAction
from dark_factory.changes.refs import ArtifactRef, ChangeRequestRef, RepositoryRef
from dark_factory.changes.run import Change, ChangeRun, StageResult
from dark_factory.orchestration.stages.agent import DEFAULT_TARGET_BRANCH, branch_name
from dark_factory.orchestration.stages.checks import pending_gate_results
from dark_factory.orchestration.stages.context import StageContext
from dark_factory.ports import MergeRequestPort, OpenChangeRequest, RepositoryPort
from dark_factory.quality.release.decision import (
    ReleaseObservation,
    evaluate_release,
    normalize_digest,
)
from dark_factory.quality.release.evidence import build_release_evidence

__all__ = [
    "DIGEST_FILE_TEMPLATE",
    "RELEASE_BRANCH_PREFIX",
    "InnerStageExecutor",
    "ReleaseObservation",
    "ReleaseStageExecutor",
    "build_release_resolution",
    "release_resolved",
]


def _present(value: str | None) -> bool:
    """Presence of an observed value; blank strings count as absent (fail-closed)."""
    return value is not None and value.strip() != ""


def release_resolved(observation: ReleaseObservation | None) -> bool:
    """Whether the observation resolves the external wait of the release stage (T-092 S4).

    The wait resolves only on the full release facts: the digest observed on
    the deployment and both raw Argo Application statuses (sync, health).
    ``None`` (nothing observed) and partial observations are never a
    resolution — partial data must not wake the waiting attempt (fail-closed,
    ADR-006 p.8). The expected digest may be absent: the checkpoint's release
    evidence carries it (T034).
    """
    if observation is None:
        return False
    return (
        _present(observation.observed_digest)
        and _present(observation.argo_sync_raw)
        and _present(observation.argo_health_raw)
    )


def build_release_resolution(
    *,
    run: ChangeRun,
    change: Change,
    checkpoint: StageResult,
    observation: ReleaseObservation,
    input_revision: str,
    attempt_number: int,
    now: datetime,
) -> StageResult:
    """Turn a resolved release observation into the final result of the waiting attempt.

    The result carries the waiting attempt's identity verbatim from the
    checkpoint — stage, run, change, attempt number, pinned input revision —
    so the durable store supersedes the waiting checkpoint of the same attempt
    (ADR-006 p.8). ``run``/``change`` and the ``attempt_number``/
    ``input_revision`` arguments exist for driver uniformity with
    :func:`~dark_factory.orchestration.stages.gates.build_gate_resolution`;
    the identity comes from the checkpoint.

    The expected digest falls back to the checkpoint's release evidence when
    the observation does not name one — the promotion pinned it there (T034,
    FR-011). The decision core (``quality.release.decision``) then rules in
    its fixed order: ``released`` succeeds the stage with the release action
    and the release gate passed at the observed digest, anything else blocks
    the attempt with the decision's diagnostics and the rollback signal
    (ADR-010, ADR-011 p.6). The outcome is persisted as the additive
    ``StageResult.release`` evidence via
    :func:`~dark_factory.quality.release.evidence.build_release_evidence`,
    stamped with ``observation.verified_at`` or ``now``.

    Raises ``ValueError`` when the observation does not resolve the wait
    (the driver replays the waiting checkpoint instead).
    """
    if not release_resolved(observation):
        raise ValueError(
            f"observation does not resolve the external wait of stage "
            f"{checkpoint.stage.value}; the driver replays the waiting checkpoint instead"
        )

    if observation.expected_digest is None and checkpoint.release is not None:
        observation = replace(observation, expected_digest=checkpoint.release.expected_digest)

    decision = evaluate_release(observation)
    release = build_release_evidence(
        observation,
        decision,
        verified_at=observation.verified_at if observation.verified_at is not None else now,
        application=observation.application,
    )
    gate_sha = normalize_digest(observation.observed_digest)
    if decision.status is ReleaseStatus.RELEASED:
        return StageResult(
            stage=checkpoint.stage,
            run_id=checkpoint.run_id,
            change_id=checkpoint.change_id,
            attempt_number=checkpoint.attempt_number,
            input_revision=checkpoint.input_revision,
            status=StageStatus.SUCCEEDED,
            next_action=ReleaseAction(
                reason="release verified: digest unchanged, argo synced and healthy, smoke passed"
            ),
            gate_results=[
                GateResult(
                    gate=Gate.RELEASE,
                    status=GateStatus.PASSED,
                    sha=gate_sha,
                    summary="release verification passed",
                )
            ],
            release=release,
            produced_at=now,
        )
    reason = decision.reason or "release verification failed (T034)"
    if decision.rollback_signal is not None:
        reason = f"{reason}; rollback: {decision.rollback_signal}"
    return StageResult(
        stage=checkpoint.stage,
        run_id=checkpoint.run_id,
        change_id=checkpoint.change_id,
        attempt_number=checkpoint.attempt_number,
        input_revision=checkpoint.input_revision,
        status=StageStatus.BLOCKED,
        next_action=StopAction(outcome=StopOutcome.BLOCKED, reason=reason),
        gate_results=[
            GateResult(
                gate=Gate.RELEASE,
                status=GateStatus.FAILED,
                sha=gate_sha,
                summary=decision.reason,
            )
        ],
        release=release,
        produced_at=now,
    )


_EFFECT_GITOPS_BRANCH: Final[str] = "gitops_branch"
_EFFECT_GITOPS_COMMIT: Final[str] = "gitops_commit"
_EFFECT_GITOPS_CHANGE_REQUEST: Final[str] = "gitops_change_request"
"""Effect types of the GitOps promotion, namespaced apart from the product
repository's effects (``stages.agent``) so one operation can never collide its
promotion with a publication (FR-017, ADR-006 p.3)."""

RELEASE_BRANCH_PREFIX: Final[str] = "factory-release"
"""Prefix of the promotion branch the executor ensures in the GitOps repository."""

DIGEST_FILE_TEMPLATE: Final[str] = "releases/{run_id}/digest"
"""Path of the promoted file: one digest pin per run (ADR-010, ADR-024 §7 S4).

Run ids are path-safe by construction (``generated_run_id`` mints
``run_<hex>``), so the template needs no slug of its own."""

type InnerStageExecutor = Callable[[StageContext], StageResult]
"""The executor the release executor delegates every non-release stage to."""


class ReleaseStageExecutor:
    """``StageExecutor`` that promotes the expected digest and parks the stage (T-092 S4).

    The release stage is not agent work (ADR-024 §7 S4): one attempt promotes
    the expected immutable digest into the **GitOps repository** — the durable
    promotion of ADR-010 — and parks the stage in ``waiting`` until the rollout
    resolves (Argo sync/health, smoke, digest immutability). The promotion runs
    through the same ports an agent stage publishes with (ADR-015 p.3), under
    deterministic effect keys, so a retry of the operation re-plays into the
    same branch, the same commit and the same change request instead of minting
    a second promotion (FR-017, ADR-006 p.3).

    What one release attempt does, in order:

    1. refuse honestly when no expected digest is configured — promoting
       "something" would be an invented release (fail-closed, ADR-011 p.6);
       this stops *before any external effect*;
    2. resolve the GitOps repository's target-branch head, ensure the promotion
       branch there and publish **one** commit that pins the digest file
       (``releases/<run_id>/digest``); the commit carries the invisible
       idempotency marker the ``RepositoryPort`` adapter embeds, so the dedup
       survives a cold adapter (FR-017);
    3. find the run's promotion change request or open it with the commit as
       its head — the MR is the human-reviewable promotion record (ADR-010);
    4. report ``waiting`` with the change request named: the rollout (Argo
       sync/health) and the smoke probes are observed later, through the
       driver's release facts — never by this executor.

    Every stage other than :attr:`Stage.RELEASE` delegates to ``inner`` (the
    composition root passes the harness-backed executor, or the deterministic
    path): this class owns only the release promotion.

    Error policy: an unreachable SCM or change-request provider never escapes
    into the flow — it becomes a ``blocked`` attempt with the exception *type*
    only, because provider exception texts can embed URLs or credentials
    (ADR-009). The async ports are driven from the synchronous ``StageExecutor``
    seam through a private event loop (``asyncio.run``), like the harness-backed
    executor; calling this from within a running loop is a programming error and
    fails loudly.
    """

    def __init__(
        self,
        inner: InnerStageExecutor,
        *,
        repository: RepositoryPort,
        merge_requests: MergeRequestPort,
        gitops_repository: RepositoryRef,
        expected_digest: str | None = None,
        target_branch: str = DEFAULT_TARGET_BRANCH,
    ) -> None:
        self._inner = inner
        self._repository = repository
        self._merge_requests = merge_requests
        self._gitops_repository = gitops_repository
        self._expected_digest = expected_digest
        self._target_branch = target_branch

    def __call__(self, context: StageContext) -> StageResult:
        """Synchronous ``StageExecutor`` seam: delegate or promote (blocking)."""
        if context.stage is not Stage.RELEASE:
            return self._inner(context)
        return asyncio.run(self._promote(context))

    async def _promote(self, context: StageContext) -> StageResult:
        """Pin the digest in the GitOps repository and park the stage for the rollout.

        Every effect is keyed deterministically (FR-017): the digest is part of
        the commit key, so a re-promotion of a *different* digest lands a new
        commit on the same branch while a retry of the same promotion replays
        into the recorded commit and change request (ADR-006 p.3).
        """
        digest = normalize_digest(self._expected_digest)
        if digest is None:
            return self._blocked(
                context,
                "stage release: no expected digest is configured — pass --expected-digest"
                " or --digest-json; promoting without one would be an invented release",
            )
        identity = operation_key(context.run_id, context.stage, context.input_revision or "")
        branch = branch_name(context.run_id, prefix=RELEASE_BRANCH_PREFIX)
        try:
            base = await self._repository.get_revision(self._gitops_repository, self._target_branch)
            await self._repository.ensure_branch(
                self._gitops_repository,
                branch,
                from_revision=base,
                idempotency_key=effect_key(identity, _EFFECT_GITOPS_BRANCH, branch),
            )
            commit_sha = await self._repository.publish_commit(
                self._gitops_repository,
                branch,
                {DIGEST_FILE_TEMPLATE.format(run_id=context.run_id): f"{digest}\n".encode()},
                message=f"factory release {context.run_id}: pin digest {digest}",
                idempotency_key=effect_key(identity, _EFFECT_GITOPS_COMMIT, f"{branch}:{digest}"),
            )
            change_request = await self._merge_requests.find_existing(
                self._gitops_repository, context.run_id
            )
            if change_request is None:
                change_request = await self._merge_requests.open(
                    OpenChangeRequest(
                        repository=self._gitops_repository,
                        change_id=context.run_id,
                        source_branch=branch,
                        target_branch=self._target_branch,
                        title=f"factory release {context.run_id}",
                        description=(
                            f"Pins the immutable image digest {digest} of run {context.run_id}"
                            f" (change {context.change.id}) for rollout (ADR-010)."
                        ),
                        head_sha=commit_sha,
                    ),
                    idempotency_key=effect_key(identity, _EFFECT_GITOPS_CHANGE_REQUEST, branch),
                )
        except Exception as exc:
            return self._blocked(
                context,
                f"stage {context.stage.value}: release promotion failed at the boundary"
                f" ({type(exc).__name__})",
            )
        return self._waiting(
            context,
            branch=branch,
            digest=digest,
            commit_sha=commit_sha,
            change_request=change_request,
        )

    def _waiting(
        self,
        context: StageContext,
        *,
        branch: str,
        digest: str,
        commit_sha: str,
        change_request: ChangeRequestRef,
    ) -> StageResult:
        """The digest is pinned; the stage waits for the rollout to resolve (ADR-006 p.8).

        No gate is evaluated here: the release gate is decided later, from the
        observed release facts (the rollout's Argo statuses, the smoke probes,
        FR-011/FR-013), so the required gates are reported ``pending`` exactly
        as the other executors report them.
        """
        return StageResult(
            stage=context.stage,
            run_id=context.run_id,
            change_id=context.change.id,
            attempt_number=context.attempt_number,
            input_revision=context.input_revision,
            status=StageStatus.WAITING,
            next_action=WaitForCIAction(
                reason=(
                    f"stage {context.stage.value} pinned {digest} in"
                    f" {self._gitops_repository.slug}#{change_request.number} ({branch})"
                    " at the promotion commit; waiting for the Argo sync and the smoke probes"
                ),
                change_request=change_request,
            ),
            artifacts=[
                ArtifactRef(
                    artifact_type=ArtifactKind.CHANGE_REQUEST.value,
                    uri=change_request.url
                    or f"{self._gitops_repository.slug}#{change_request.number}",
                    revision=commit_sha,
                    producer=Role.CI_CD.value,
                )
            ],
            gate_results=pending_gate_results(context.required_gates),
        )

    def _blocked(self, context: StageContext, reason: str) -> StageResult:
        """The attempt stopped before it could promote (retryable, ADR-018 p.5)."""
        return StageResult(
            stage=context.stage,
            run_id=context.run_id,
            change_id=context.change.id,
            attempt_number=context.attempt_number,
            input_revision=context.input_revision,
            status=StageStatus.BLOCKED,
            next_action=StopAction(outcome=StopOutcome.BLOCKED, reason=reason),
            gate_results=pending_gate_results(context.required_gates),
        )
