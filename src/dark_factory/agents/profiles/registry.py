"""Registry of the built-in agent profiles (ADR-007).

Profiles are typed Python constants — the codebase has no manifest-file
infrastructure. The core MVP ships exactly product, develop and quality
(ADR-007 p.4); the other six roles get profiles in T-046/T-047 and are an
explicit error here until then.
"""

from collections.abc import Mapping
from typing import Final

from dark_factory.agents.artifacts import ArtifactKind
from dark_factory.agents.errors import ProfileNotFoundError
from dark_factory.agents.profiles.manifest import AgentProfile
from dark_factory.changes.enums import Role

PRODUCT_PROFILE: Final[AgentProfile] = AgentProfile(
    role=Role.PRODUCT,
    name="Product",
    version="1.0.0",
    description=(
        "Turns an incoming task into refined requirements and a testable"
        " specification, and packages the change for review."
    ),
    inputs=(ArtifactKind.TASK, ArtifactKind.CONTEXT),
    outputs=(ArtifactKind.REQUIREMENTS, ArtifactKind.SPEC, ArtifactKind.CHANGE_REQUEST),
    tools=("read_file", "search_repo"),
    constraints=(
        "Scope the change to what the task requires; list everything else as out of scope.",
        "Every requirement carries at least one acceptance criterion.",
        "Never modify code, infrastructure or pipeline configuration directly.",
    ),
    stop_conditions=(
        "The task contradicts the constitution or an accepted ADR - escalate.",
        "Scope or priority questions stay unanswered after clarification - stop and ask.",
        "The change needs an architectural decision that has not been made - escalate.",
    ),
    skills=("intake", "requirements-refinement", "spec-authoring", "change-request"),
)

DEVELOP_PROFILE: Final[AgentProfile] = AgentProfile(
    role=Role.DEVELOP,
    name="Develop",
    version="1.0.0",
    description=(
        "Implements the approved specification as code changes and addresses"
        " independent review findings."
    ),
    inputs=(ArtifactKind.SPEC, ArtifactKind.REVIEW_REPORT, ArtifactKind.CONTEXT),
    outputs=(ArtifactKind.CODE,),
    tools=("read_file", "write_file", "apply_patch", "run_command"),
    constraints=(
        "Implement only the approved specification; expand scope only through a new task.",
        "Follow the existing patterns, structure and dependencies of the repository.",
        "Run the repository checks (lint, typecheck, tests) and report only verified results.",
    ),
    stop_conditions=(
        "An acceptance criterion cannot be met within the approved scope - escalate.",
        "The implementation requires a stack, dependency or architecture change (new ADR) - stop.",
        "A required secret or credential is missing - never guess it, stop and ask.",
        "The rework round limit is exhausted - escalate to the Flow.",
    ),
    skills=("implementation", "implementation-rework"),
)

QUALITY_PROFILE: Final[AgentProfile] = AgentProfile(
    role=Role.QUALITY,
    name="Quality",
    version="1.0.0",
    description=(
        "Reviews code changes independently of their author and verifies the"
        " implementation against the acceptance criteria."
    ),
    inputs=(ArtifactKind.SPEC, ArtifactKind.CODE, ArtifactKind.CONTEXT),
    outputs=(ArtifactKind.REVIEW_REPORT, ArtifactKind.ACCEPTANCE_VERDICT),
    tools=("read_file", "run_tests", "run_command"),
    constraints=(
        "Review and verify independently; never fix findings in the reviewed change yourself.",
        "Ground every verdict in evidence: checks, test runs and the pinned revision.",
        "Report each finding with severity, location and a required action.",
    ),
    stop_conditions=(
        "The change under review does not match the pinned revision - stop.",
        "Acceptance criteria cannot be verified from the produced evidence - fail the verdict.",
        "A blocker finding touches areas outside the reviewed scope - escalate.",
    ),
    skills=("code-review", "acceptance-verification"),
)

_PROFILES: Final[Mapping[Role, AgentProfile]] = {
    Role.PRODUCT: PRODUCT_PROFILE,
    Role.DEVELOP: DEVELOP_PROFILE,
    Role.QUALITY: QUALITY_PROFILE,
}

CORE_ROLES: Final[tuple[Role, ...]] = (Role.PRODUCT, Role.DEVELOP, Role.QUALITY)
"""The core MVP roles that ship with profiles (ADR-007 p.4)."""


def get_profile(role: Role) -> AgentProfile:
    """Return the built-in profile of ``role``.

    Raises ``ProfileNotFoundError`` for a role without a profile: the core MVP
    ships product, develop and quality; the other six roles join in
    T-046/T-047 and deliberately have no profiles yet.
    """
    try:
        return _PROFILES[role]
    except KeyError:
        raise ProfileNotFoundError(f"no agent profile for role {role.value!r}") from None
