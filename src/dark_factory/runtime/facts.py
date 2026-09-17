"""The SCM-backed facts provider of the wait-resolution protocol (T-092 S3, ADR-006 p.8).

A stage parked in ``waiting`` — the durable external-wait checkpoint — is
resumed by the driver
(:func:`dark_factory.orchestration.runner.advance_run`) once the facts it
waits for resolve. The driver stays port-free (ADR-024 p.5): it consumes only
the value-level
:class:`~dark_factory.orchestration.stages.gates.GateObservation`, and this
module is the composition-root seam that observes it from the provider ports —
``MergeRequestPort.observe`` for the change request (live head, merge state,
reviews) and ``PipelinePort`` for the pipeline verdict at the observed head
SHA.

Like ``ScmRevision`` (``orchestration.stages.agent``) the provider is
synchronous because the driver is (``advance_run`` runs inside a database
transaction); the async ports are driven through a private loop. The
observation is read-only, and its failures are the caller's: a provider error
surfaces to the driver as it is and aborts the advance with nothing written,
while an absent change request is the port's ``None`` — nothing observed, the
wait stays parked (the checkpoint is durable, ADR-006 p.8).
"""

import asyncio
from collections.abc import Mapping, Sequence
from typing import Final

from dark_factory.changes.enums import (
    ChangeRequestStatus,
    DecisionOutcome,
    DecisionSource,
    Gate,
    Stage,
)
from dark_factory.changes.findings import Decision
from dark_factory.changes.run import Change, ChangeRun
from dark_factory.orchestration.stages.gates import GateObservation
from dark_factory.ports import (
    MergeRequestPort,
    PipelinePort,
    PipelineStatus,
    ReviewObservation,
)

_APPROVAL_OUTCOMES: Final[Mapping[str, DecisionOutcome]] = {
    "approved": DecisionOutcome.APPROVED,
    "changes_requested": DecisionOutcome.REJECTED,
}
"""Review states that are human decisions; ``commented``, ``dismissed`` and
``pending`` decide nothing and are not carried."""


class ScmFactsProvider:
    """The provider facts of one waiting stage attempt, observed from the ports (T-092 S3).

    The composition root binds this over the configured provider's
    ``MergeRequestPort``/``PipelinePort``; without a provider there is no
    observer and the driver replays the waiting checkpoint — the same
    fail-closed absence as ``repository``/``merge_requests``.

    The change request is the change snapshot's ``change_request``, falling
    back to the FR-011 cold lookup by ``change_id``: the checkpoint may
    outlive the process that opened the request. The pipeline verdict is read
    at the observed head SHA — the SHA the machine gates bind to (FR-009) —
    and a request whose head the provider does not report yields no pipeline
    fact at all. Reviews become version-bound human decisions (ADR-009 p.7)
    for the flow's control-point checks (T-080) and the merge authorization
    (ADR-011 p.2).
    """

    def __init__(self, merge_requests: MergeRequestPort, pipelines: PipelinePort) -> None:
        self._merge_requests = merge_requests
        self._pipelines = pipelines

    def __call__(self, run: ChangeRun, stage: Stage, change: Change) -> GateObservation | None:
        """Observe the external facts ``change``'s waiting ``stage`` waits for.

        Synchronous because the driver is; the async ports are driven through
        a private loop, like the executor's own seam. ``run`` and ``stage``
        are part of the driver's protocol and are not consulted: the facts
        depend on the change's request and its pipeline alone.
        """
        return asyncio.run(self._observe(change))

    async def _observe(self, change: Change) -> GateObservation | None:
        ref = change.change_request
        if ref is None:
            ref = await self._merge_requests.find_existing(change.product, change.id)
        if ref is None:
            return None
        observed = await self._merge_requests.observe(ref)
        pipeline: PipelineStatus | None = None
        if observed.head_sha is not None:
            pipeline = await self._pipelines.status(change.product, observed.head_sha)
        return GateObservation(
            head_sha=observed.head_sha,
            merged=observed.status is ChangeRequestStatus.MERGED,
            pipeline_status=None if pipeline is None else pipeline.status,
            approvals=self._approvals(observed.reviews),
        )

    @staticmethod
    def _approvals(reviews: Sequence[ReviewObservation]) -> tuple[Decision, ...]:
        """The reviews that are human decisions, version-bound to their SHA.

        ``approved`` and ``changes_requested`` both decide the review gate;
        the merge policy weighs the latest version-bound one (ADR-009 p.7), so
        both are carried and the policy — not this seam — decides. A review
        without a SHA or a submission instant binds to nothing and authorizes
        nothing: it is dropped rather than guessed. The decision id is
        namespaced by the provider review id, so re-observing the same review
        yields the same decision (replay-dedup in the decision store).
        """
        decisions: list[Decision] = []
        for review in reviews:
            outcome = _APPROVAL_OUTCOMES.get(review.state)
            if outcome is None or review.commit_sha is None or review.submitted_at is None:
                continue
            decisions.append(
                Decision(
                    id=f"review:{review.review_id}",
                    gate=Gate.REVIEW,
                    outcome=outcome,
                    decided_by=DecisionSource.HUMAN,
                    decided_at=review.submitted_at,
                    commit_sha=review.commit_sha,
                )
            )
        return tuple(decisions)
