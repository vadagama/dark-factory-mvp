"""Branch protection requirements for the merge gate (T-026, docs T-032).

The requirements are data: what the provider's branch protection / rulesets
(GitHub) or protected branches (GitLab) must enforce for the factory's merge
policy to hold. ``protection_violations`` validates observed provider settings
against the required policy and returns the violations deterministically —
no network calls; the provider adapter (T-030) supplies the observed values.

MVP requirements (T-032, ADR-011 p.2): a protected branch, at least one
required human approval, required status checks on the merged (final) SHA,
squash-only merging and dismissal of stale approvals — the provider-side
analog of version-bound approvals (ADR-009 p.7): a new push invalidates the
approvals a merge could otherwise rely on.
"""

from dataclasses import dataclass
from typing import Final, Literal

type MergeProtectionRule = Literal[
    "branch_not_protected",
    "insufficient_required_approvals",
    "required_checks_missing",
    "non_squash_merge_allowed",
    "stale_approvals_not_dismissed",
]
"""One unmet branch-protection requirement (T-032, ADR-011 p.2)."""

_SQUASH_ONLY: Final[frozenset[str]] = frozenset({"squash"})


@dataclass(frozen=True)
class MergeProtectionPolicy:
    """Required branch-protection settings, frozen data (T-032)."""

    protected_branch: bool = True
    """The target branch rejects direct pushes and force pushes."""

    required_approving_reviews: int = 1
    """Approving human reviews the provider must require before merge (MVP: 1)."""

    required_status_checks: bool = True
    """Required status checks are enforced at merge time, i.e. on the final SHA
    (T-032; the flow re-verifies the same SHA via the merge policy, FR-011)."""

    allowed_merge_methods: frozenset[str] = _SQUASH_ONLY
    """Merge methods the provider may offer; MVP mandates squash-only (T-032).
    The declared set is exhaustive: any provider method outside it is a
    bypass of the merge policy."""

    dismiss_stale_approvals: bool = True
    """A new push dismisses approvals — the provider-side analog of invalidating
    a version-bound approval with a new SHA (ADR-009 p.7, FR-011)."""


DEFAULT_MERGE_PROTECTION: Final[MergeProtectionPolicy] = MergeProtectionPolicy()
"""MVP branch-protection requirements (T-032, ADR-011 p.2)."""


@dataclass(frozen=True)
class ObservedBranchProtection:
    """Branch-protection settings as observed on the provider (plain data).

    All fields are required: the observing adapter states explicitly what it
    saw instead of inheriting defaults (a half-filled observation must not
    look compliant).
    """

    protected_branch: bool
    required_approving_reviews: int
    required_status_checks: bool
    allowed_merge_methods: frozenset[str]
    dismiss_stale_approvals: bool


@dataclass(frozen=True)
class MergeProtectionViolation:
    """One branch-protection requirement the observed settings do not meet."""

    rule: MergeProtectionRule
    reason: str


def protection_violations(
    observed: ObservedBranchProtection,
    *,
    policy: MergeProtectionPolicy = DEFAULT_MERGE_PROTECTION,
) -> list[MergeProtectionViolation]:
    """Violations of ``policy`` in ``observed``; empty list means compliant.

    Deterministic: at most one violation per rule, reported in the fixed rule
    order of ``MergeProtectionRule``.
    """
    violations: list[MergeProtectionViolation] = []
    if policy.protected_branch and not observed.protected_branch:
        violations.append(
            MergeProtectionViolation(
                rule="branch_not_protected",
                reason="the target branch is not protected (T-032, ADR-011 p.2)",
            )
        )
    if observed.required_approving_reviews < policy.required_approving_reviews:
        violations.append(
            MergeProtectionViolation(
                rule="insufficient_required_approvals",
                reason=(
                    f"branch requires {observed.required_approving_reviews} approving "
                    f"review(s), the policy requires {policy.required_approving_reviews} "
                    "(T-032)"
                ),
            )
        )
    if policy.required_status_checks and not observed.required_status_checks:
        violations.append(
            MergeProtectionViolation(
                rule="required_checks_missing",
                reason=(
                    "required status checks are not enforced on the branch, so checks on "
                    "the final SHA are not guaranteed (T-032, FR-011)"
                ),
            )
        )
    if policy.allowed_merge_methods and observed.allowed_merge_methods != (
        policy.allowed_merge_methods
    ):
        violations.append(
            MergeProtectionViolation(
                rule="non_squash_merge_allowed",
                reason=(
                    f"provider-allowed merge methods {sorted(observed.allowed_merge_methods)} "
                    "differ from the policy's "
                    f"{sorted(policy.allowed_merge_methods)} (T-032: squash-only)"
                ),
            )
        )
    if policy.dismiss_stale_approvals and not observed.dismiss_stale_approvals:
        violations.append(
            MergeProtectionViolation(
                rule="stale_approvals_not_dismissed",
                reason=(
                    "stale approvals survive new pushes: a new SHA would inherit approvals "
                    "made for an older one (ADR-009 p.7, FR-011)"
                ),
            )
        )
    return violations
