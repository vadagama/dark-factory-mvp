"""Merge policy of the Factory Flow: who may merge, and when (T-026, docs T-032).

Deterministic, pure-domain decision whether a change request may be merged:
no harness/LLM calls, no I/O, no database (same discipline as the neighboring
policies). The flow engine consults it when a ``MergeAction`` result is applied
(``dark_factory.orchestration.flow``); the reconciler (T-063) can evaluate the
same policy standalone from observed provider state.

MVP rules (ADR-011 p.2: autonomous implementation with a human release gate):

- agent jobs never merge (FR-004, FR-023: agent pods carry no merge/deploy
  authority; a merge requested by an agent is a policy violation);
- the expected SHA must be unchanged — the observed head must still equal the
  SHA the merge was prepared against, otherwise the operation stops (FR-011);
- every gate required by the route/stage must be satisfied by a result
  evaluated at the final SHA: a result bound to an older or unknown SHA is
  stale and does not satisfy (T-032: required checks on the merged SHA; a new
  SHA invalidates previous passes, ADR-009 p.7);
- for R2+ every human control point the class makes mandatory must be approved
  at the final SHA (ADR-023 p.4/p.5): a dangerous change is blocked instead of
  being carried on an unapproved control point. Below R2 the check is silent and
  the policy is unchanged —
- a human decision on the merge authorization gate must be bound to the final
  SHA (version-bound approval, ADR-009 p.7) and the last such decision in list
  order must be an approval; a decision bound to an earlier SHA never
  authorizes a new head.

Given these preconditions, the merge is performed by a human (MVP default) or
— only for risk classes explicitly listed in the policy — by the trusted
finalizer (FR-010). The default policy lists no classes: absent or ambiguous
policy means manual mode (FR-010), and auto-merge stays disabled until T-085.
The merge method is squash-only (T-032), declared here and enforced on the
provider by ``dark_factory.rules.merge_protection``; this module never
executes a merge itself.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal

from dark_factory.changes.enums import (
    DecisionOutcome,
    DecisionSource,
    Gate,
    RiskClass,
    Route,
    Stage,
)
from dark_factory.changes.findings import Decision, GateResult
from dark_factory.changes.risk import is_r2_or_higher
from dark_factory.orchestration.policy.risk import missing_control_points
from dark_factory.rules.gates import unsatisfied_gates

type MergeExecutor = Literal["human", "trusted_finalizer", "agent"]
"""Who performs the merge (FR-010, FR-023): the human, the trusted finalizer
job, or an agent job. Agent jobs have no merge authority."""

type MergeDecisionKind = Literal[
    "blocked",
    "manual_merge_required",
    "human_merge_authorized",
    "finalizer_merge_allowed",
]
"""Outcome of one merge-policy evaluation (T-026).

``blocked`` marks a policy violation (agent executor, SHA drift, stale or
failed gates) and stops the run; ``manual_merge_required`` parks it in
Waiting for the human merge (ADR-011 p.2); the two authorized kinds advance
the flow to the release stage.
"""

_NO_AUTO_MERGE: Final[frozenset[RiskClass]] = frozenset()
_SQUASH_ONLY: Final[frozenset[str]] = frozenset({"squash"})


@dataclass(frozen=True)
class MergePolicy:
    """Frozen configuration of the merge policy (T-026, FR-010)."""

    auto_merge_risk_classes: frozenset[RiskClass] = _NO_AUTO_MERGE
    """Risk classes the trusted finalizer may merge automatically. Empty means
    manual mode (FR-010); auto-merge for R0/R1 is a separate, policy-controlled
    task (T-085, ADR-011 p.3), so MVP ships this empty."""

    merge_methods: frozenset[str] = _SQUASH_ONLY
    """Declared merge methods; MVP mandates squash (T-032). Provider-side
    enforcement lives in ``dark_factory.rules.merge_protection``."""

    merge_authorization_gate: Gate = Gate.REVIEW
    """Human gate carrying merge authorization (ADR-011 p.2: review carries
    the human-confirmed merge; ``rules.gates.HUMAN_GATES``)."""


DEFAULT_MERGE_POLICY: Final[MergePolicy] = MergePolicy()
"""MVP default: manual mode — every merge waits for a version-bound human
approval (ADR-011 p.2, FR-010)."""


@dataclass(frozen=True)
class MergeRequestContext:
    """Facts of one merge request as observed by the caller (T-026).

    ``expected_sha`` is the SHA the merge was prepared against; ``head_sha``
    is the change request head observed now (FR-011 compares the two). The
    flow engine anchors ``route``, ``stage`` and ``gate_results`` to the run
    and the merge-carrying ``StageResult``; a standalone caller (reconciler,
    T-063) provides them from observed state.
    """

    executor: MergeExecutor
    risk_class: RiskClass
    route: Route
    stage: Stage
    expected_sha: str | None
    head_sha: str | None
    human_approvals: Sequence[Decision] = ()
    gate_results: Sequence[GateResult] = ()


@dataclass(frozen=True)
class MergeDecision:
    """Frozen outcome of one merge-policy evaluation (T-026).

    ``reason`` carries human-readable diagnostics for blocked and manual
    outcomes. ``merge_method`` is the single method the finalizer is
    authorized to use when the policy declares exactly one (MVP: squash).
    """

    kind: MergeDecisionKind
    reason: str | None = None
    merge_method: str | None = None


def evaluate_merge(
    context: MergeRequestContext, *, policy: MergePolicy = DEFAULT_MERGE_POLICY
) -> MergeDecision:
    """Decide one merge request against ``policy`` (deterministic, T-026).

    Checks run in a fixed order and the first triggered outcome wins: agent
    executor, SHA immutability (FR-011), gates at the final SHA (T-032), the
    version-bound human approval (ADR-009 p.7), then the executor and
    risk-class authorization (FR-010).
    """
    if context.executor == "agent":
        return MergeDecision(
            kind="blocked",
            reason=(
                "merge executor 'agent' is not permitted: agent jobs have no merge "
                "authority (FR-004, FR-023)"
            ),
        )
    if context.expected_sha is None or context.head_sha is None:
        return MergeDecision(
            kind="blocked",
            reason="cannot verify SHA immutability: expected or head SHA unknown (FR-011)",
        )
    if context.expected_sha != context.head_sha:
        return MergeDecision(
            kind="blocked",
            reason=(
                f"expected SHA {context.expected_sha} does not match the observed head "
                f"{context.head_sha}: merge stopped (FR-011)"
            ),
        )
    unsatisfied = _gates_not_satisfied_at_head(context, context.expected_sha)
    if unsatisfied:
        names = ", ".join(gate.value for gate in unsatisfied)
        return MergeDecision(
            kind="blocked",
            reason=(
                f"required gates not satisfied at the final SHA {context.expected_sha}: "
                f"{names} (T-032; a new SHA invalidates previous passes, ADR-009 p.7)"
            ),
        )
    if is_r2_or_higher(context.risk_class):
        # R2+ obligations (ADR-023 p.5): the human control points of the merge
        # stage must be approved at the final SHA. Below R2 the policy is
        # unchanged — a missing approval stays a request for the human merge.
        missing = missing_control_points(
            context.route,
            context.stage,
            context.risk_class,
            context.human_approvals,
            sha=context.expected_sha,
        )
        if missing:
            names = ", ".join(sorted(point.value for point in missing))
            return MergeDecision(
                kind="blocked",
                reason=(
                    f"risk class {context.risk_class.value} requires human control points "
                    f"approved at the final SHA {context.expected_sha}: {names} "
                    "(ADR-023 p.4/p.5)"
                ),
            )
    latest = _latest_human_decision(context, policy, context.expected_sha)
    if latest is None:
        return MergeDecision(
            kind="manual_merge_required",
            reason=(
                f"no human approval on gate '{policy.merge_authorization_gate.value}' bound "
                f"to the final SHA {context.expected_sha} (ADR-009 p.7, FR-010)"
            ),
        )
    if latest.outcome is not DecisionOutcome.APPROVED:
        return MergeDecision(
            kind="manual_merge_required",
            reason=(
                f"latest human decision on gate '{policy.merge_authorization_gate.value}' at "
                f"the final SHA {context.expected_sha} is '{latest.outcome.value}', not an "
                "approval (ADR-011 p.2)"
            ),
        )
    if context.executor == "human":
        return MergeDecision(kind="human_merge_authorized")
    if context.risk_class in policy.auto_merge_risk_classes:
        return MergeDecision(kind="finalizer_merge_allowed", merge_method=_single_method(policy))
    return MergeDecision(
        kind="manual_merge_required",
        reason=(
            f"auto-merge is not enabled for risk class {context.risk_class.value}: "
            "manual mode (FR-010; ADR-011 p.2, T-085)"
        ),
    )


def _gates_not_satisfied_at_head(context: MergeRequestContext, sha: str) -> list[Gate]:
    """Required gates without a satisfying result evaluated at ``sha``.

    Only results whose ``sha`` equals the final SHA participate: a result
    bound to an older or unknown SHA is stale (T-032, ADR-009 p.7). The last
    result per gate wins, as in ``rules.gates``.
    """
    at_head = [item for item in context.gate_results if item.sha == sha]
    return unsatisfied_gates(context.route, context.stage, at_head)


def _latest_human_decision(
    context: MergeRequestContext, policy: MergePolicy, sha: str
) -> Decision | None:
    """Last human decision on the merge gate bound to ``sha``, in list order.

    A new SHA invalidates previous decisions (ADR-009 p.7): only decisions
    whose ``commit_sha`` equals ``sha`` participate, and the last one in list
    order supersedes earlier ones — the same rule the gate results follow.
    Decisions made by policy or by an agent never satisfy a human gate
    (FR-010, ADR-011 p.2).
    """
    candidates = [
        decision
        for decision in context.human_approvals
        if decision.decided_by is DecisionSource.HUMAN
        and decision.gate is policy.merge_authorization_gate
        and decision.commit_sha == sha
    ]
    return candidates[-1] if candidates else None


def _single_method(policy: MergePolicy) -> str | None:
    """The declared merge method when exactly one is configured, else ``None``."""
    if len(policy.merge_methods) == 1:
        return next(iter(policy.merge_methods))
    return None
