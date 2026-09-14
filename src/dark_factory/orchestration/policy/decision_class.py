"""Decision classifier: Known path / Bounded choice / New path (T-016, ADR-018 p.6).

Classification is a deterministic function of the decision facts: a decision
pinned by an ADR/template/golden path is applied autonomously, a choice among
pre-approved options is made with a recorded rationale, and a new path — new
technology, service boundary, storage, protocol or IAM model — requires a
human and an ADR. A conservative default keeps unclassified decisions on the
safe side of the autonomy boundary.
"""

from dataclasses import dataclass
from typing import Final

from dark_factory.changes.enums import DecisionClass, DecisionSource

_CLASS_ORDER: Final[dict[DecisionClass, int]] = {
    DecisionClass.KNOWN_PATH: 0,
    DecisionClass.BOUNDED_CHOICE: 1,
    DecisionClass.NEW_PATH: 2,
}
"""Ordering used by the monotonicity policy: KNOWN < BOUNDED < NEW."""


class DecisionClassPolicyError(ValueError):
    """A decision-class transition outside the policy was requested."""


@dataclass(frozen=True)
class DecisionFacts:
    """Inputs of the classifier, as observable about one decision (ADR-018 p.6)."""

    pinned_by_adr: bool = False
    """The solution is already fixed by an ADR, a template or a golden path."""
    allowed_options: tuple[str, ...] = ()
    """Pre-approved variants to choose from; empty means none."""
    new_boundary: bool = False
    """Introduces a new technology, service boundary, storage, protocol or IAM model."""


@dataclass(frozen=True)
class DecisionVerdict:
    """Classification result with the obligations it entails (ADR-018 p.6)."""

    decision_class: DecisionClass
    requires_human: bool
    requires_adr: bool
    requires_rationale: bool


_KNOWN_PATH: Final = DecisionVerdict(
    decision_class=DecisionClass.KNOWN_PATH,
    requires_human=False,
    requires_adr=False,
    requires_rationale=False,
)
_BOUNDED_CHOICE: Final = DecisionVerdict(
    decision_class=DecisionClass.BOUNDED_CHOICE,
    requires_human=False,
    requires_adr=False,
    requires_rationale=True,
)
_NEW_PATH: Final = DecisionVerdict(
    decision_class=DecisionClass.NEW_PATH,
    requires_human=True,
    requires_adr=True,
    requires_rationale=False,
)


def classify_decision(facts: DecisionFacts) -> DecisionVerdict:
    """Classify one decision from its facts (deterministic, ADR-018 p.6).

    ``new_boundary`` wins over ``pinned_by_adr``: a boundary touching decision
    stays New path even when some template exists. With no signal at all the
    verdict is conservatively New path — autonomy requires positive evidence,
    not the absence of objection.
    """
    if facts.new_boundary:
        return _NEW_PATH
    if facts.pinned_by_adr:
        return _KNOWN_PATH
    if facts.allowed_options:
        return _BOUNDED_CHOICE
    return _NEW_PATH


def transition_decision_class(
    current: DecisionClass, target: DecisionClass, *, decided_by: DecisionSource
) -> DecisionClass:
    """Apply a decision-class transition under the monotonicity policy.

    An agent may raise the class (towards more caution, e.g. re-classifying a
    choice as New path) but never lower it: only the formal policy or a human
    can demote a decision (ADR-018 p.6). Returns ``target`` when allowed.
    """
    if decided_by is DecisionSource.AGENT and _CLASS_ORDER[target] < _CLASS_ORDER[current]:
        raise DecisionClassPolicyError(
            f"agent cannot lower the decision class {current.value} -> {target.value}: "
            "only the formal policy or a human may lower it (ADR-018 p.6)"
        )
    return target
