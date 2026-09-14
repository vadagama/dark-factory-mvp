"""Branch protection requirements as data, validated deterministically (T-026, docs T-032)."""

from dataclasses import replace

from dark_factory.rules.merge_protection import (
    DEFAULT_MERGE_PROTECTION,
    MergeProtectionPolicy,
    ObservedBranchProtection,
    protection_violations,
)


def _compliant() -> ObservedBranchProtection:
    """Provider settings that satisfy the default MVP policy."""
    return ObservedBranchProtection(
        protected_branch=True,
        required_approving_reviews=1,
        required_status_checks=True,
        allowed_merge_methods=frozenset({"squash"}),
        dismiss_stale_approvals=True,
    )


def test_default_policy_encodes_the_mvp_requirements() -> None:
    policy = DEFAULT_MERGE_PROTECTION
    assert policy.protected_branch is True
    assert policy.required_approving_reviews == 1
    assert policy.required_status_checks is True
    assert policy.allowed_merge_methods == frozenset({"squash"})
    assert policy.dismiss_stale_approvals is True


def test_compliant_settings_produce_no_violations() -> None:
    assert protection_violations(_compliant()) == []


def test_unprotected_branch_is_flagged() -> None:
    observed = replace(_compliant(), protected_branch=False)
    violations = protection_violations(observed)
    assert [violation.rule for violation in violations] == ["branch_not_protected"]
    assert "not protected" in violations[0].reason


def test_insufficient_required_approvals_are_flagged() -> None:
    observed = replace(_compliant(), required_approving_reviews=0)
    assert [violation.rule for violation in protection_violations(observed)] == [
        "insufficient_required_approvals"
    ]


def test_higher_approval_requirement_is_honored() -> None:
    policy = MergeProtectionPolicy(required_approving_reviews=2)
    observed = replace(_compliant(), required_approving_reviews=1)
    assert [violation.rule for violation in protection_violations(observed, policy=policy)] == [
        "insufficient_required_approvals"
    ]
    strict = replace(_compliant(), required_approving_reviews=2)
    assert protection_violations(strict, policy=policy) == []


def test_missing_required_checks_are_flagged() -> None:
    observed = replace(_compliant(), required_status_checks=False)
    violations = protection_violations(observed)
    assert [violation.rule for violation in violations] == ["required_checks_missing"]
    assert "FR-011" in violations[0].reason


def test_extra_merge_methods_bypassing_squash_only_are_flagged() -> None:
    observed = replace(_compliant(), allowed_merge_methods=frozenset({"squash", "merge"}))
    assert [violation.rule for violation in protection_violations(observed)] == [
        "non_squash_merge_allowed"
    ]


def test_missing_squash_method_is_flagged() -> None:
    observed = replace(_compliant(), allowed_merge_methods=frozenset({"merge"}))
    assert [violation.rule for violation in protection_violations(observed)] == [
        "non_squash_merge_allowed"
    ]


def test_undismissed_stale_approvals_are_flagged() -> None:
    observed = replace(_compliant(), dismiss_stale_approvals=False)
    violations = protection_violations(observed)
    assert [violation.rule for violation in violations] == ["stale_approvals_not_dismissed"]
    assert "ADR-009" in violations[0].reason


def test_all_violations_accumulate_in_a_deterministic_order() -> None:
    observed = ObservedBranchProtection(
        protected_branch=False,
        required_approving_reviews=0,
        required_status_checks=False,
        allowed_merge_methods=frozenset(),
        dismiss_stale_approvals=False,
    )
    assert [violation.rule for violation in protection_violations(observed)] == [
        "branch_not_protected",
        "insufficient_required_approvals",
        "required_checks_missing",
        "non_squash_merge_allowed",
        "stale_approvals_not_dismissed",
    ]


def test_relaxed_policy_requires_less() -> None:
    """A policy without a requirement does not flag its absence."""
    policy = MergeProtectionPolicy(
        protected_branch=False,
        required_approving_reviews=0,
        required_status_checks=False,
        allowed_merge_methods=frozenset(),
        dismiss_stale_approvals=False,
    )
    observed = ObservedBranchProtection(
        protected_branch=False,
        required_approving_reviews=0,
        required_status_checks=False,
        allowed_merge_methods=frozenset({"merge", "rebase"}),
        dismiss_stale_approvals=False,
    )
    assert protection_violations(observed, policy=policy) == []
