"""Registry of the first-slice skill manifests (T-011).

Eight skills bound to the core roles: product drives intake through the
change request, develop implements and reworks, quality reviews and verifies.
Binding checks (a skill belongs to its role, a profile references only its
own skills) live in the contract builder and the tests, not here — the
registries stay decoupled from each other.
"""

from collections.abc import Mapping
from typing import Final

from dark_factory.agents.artifacts import ArtifactKind
from dark_factory.agents.errors import SkillNotFoundError
from dark_factory.agents.skills.manifest import SkillManifest
from dark_factory.changes.enums import Role

_SKILLS: Final[Mapping[str, SkillManifest]] = {
    "intake": SkillManifest(
        id="intake",
        version="1.0.0",
        role=Role.PRODUCT,
        purpose="Normalize a raw incoming task into structured requirements with explicit scope.",
        inputs=(ArtifactKind.TASK, ArtifactKind.CONTEXT),
        outputs=(ArtifactKind.REQUIREMENTS,),
        instruction=(
            "You normalize an incoming task. Restate the goal in one sentence, split the"
            " work into in-scope and out-of-scope items, name the affected repository"
            " areas and draft acceptance criteria for every requirement. Ask for the"
            " missing decisions instead of assuming them."
        ),
        stop_conditions=(
            "The task is empty or self-contradictory beyond repair.",
            "A scope or priority question blocks every requirement"
            " and the requester is unavailable.",
        ),
    ),
    "requirements-refinement": SkillManifest(
        id="requirements-refinement",
        version="1.0.0",
        role=Role.PRODUCT,
        purpose="Refine draft requirements into unambiguous, testable requirements.",
        inputs=(ArtifactKind.REQUIREMENTS, ArtifactKind.CONTEXT),
        outputs=(ArtifactKind.REQUIREMENTS,),
        instruction=(
            "You refine requirements. Resolve ambiguities, split compound requirements"
            " and make each one verifiable through at least one acceptance criterion."
            " Check the refined set against the constitution and accepted ADRs in the"
            " context and mark every conflict as blocking."
        ),
        stop_conditions=(
            "A requirement conflicts with the constitution or an accepted ADR.",
            "Clarification from the requester is required and unavailable.",
        ),
    ),
    "spec-authoring": SkillManifest(
        id="spec-authoring",
        version="1.0.0",
        role=Role.PRODUCT,
        purpose="Author the specification document from the refined requirements.",
        inputs=(ArtifactKind.REQUIREMENTS, ArtifactKind.CONTEXT),
        outputs=(ArtifactKind.SPEC,),
        instruction=(
            "You author the specification. Write it in the repository's specification"
            " layout: problem, scope and out-of-scope, requirements with acceptance"
            " criteria, and traceability from criteria to scenarios. Keep every"
            " statement testable; a criterion that cannot be checked is a defect."
        ),
        stop_conditions=(
            "An acceptance criterion cannot be made testable.",
            "The specification depends on an undecided ADR or an open architectural question.",
        ),
    ),
    "change-request": SkillManifest(
        id="change-request",
        version="1.0.0",
        role=Role.PRODUCT,
        purpose="Package the change into a reviewable change request.",
        inputs=(ArtifactKind.REQUIREMENTS, ArtifactKind.CONTEXT),
        outputs=(ArtifactKind.CHANGE_REQUEST,),
        instruction=(
            "You package the change request. Summarize the change, the motivation and"
            " the scope, attach the refined requirements with acceptance criteria and"
            " slice the work into an ordered task list with explicit dependencies."
            " The request must be reviewable without reading the original task thread."
        ),
        stop_conditions=(
            "The work cannot be sliced into tasks with clear boundaries.",
            "The change extends beyond the agreed scope and needs a new approval.",
        ),
    ),
    "implementation": SkillManifest(
        id="implementation",
        version="1.0.0",
        role=Role.DEVELOP,
        purpose="Implement the approved specification as code changes in the task branch.",
        inputs=(ArtifactKind.SPEC, ArtifactKind.CONTEXT),
        outputs=(ArtifactKind.CODE,),
        instruction=(
            "You implement the specification. Work in the task branch, follow the"
            " repository's existing patterns and keep the change minimal and focused."
            " Update the tests and documentation the change affects, run the repository"
            " checks (lint, typecheck, tests) and report only verified results."
        ),
        stop_conditions=(
            "An acceptance criterion cannot be met within the approved scope.",
            "The change requires a new dependency, a stack change or an ADR.",
            "A required secret or credential is missing.",
        ),
    ),
    "implementation-rework": SkillManifest(
        id="implementation-rework",
        version="1.0.0",
        role=Role.DEVELOP,
        purpose="Address independent review findings without expanding the scope.",
        inputs=(ArtifactKind.REVIEW_REPORT, ArtifactKind.CONTEXT),
        outputs=(ArtifactKind.CODE,),
        instruction=(
            "You perform rework. Fix exactly the reported findings and re-run the"
            " repository checks. Do not refactor beyond the findings and do not touch"
            " unrelated code; if a fix requires a scope change, stop and escalate."
        ),
        stop_conditions=(
            "A finding requires a specification or scope change.",
            "The rework round limit is exhausted.",
        ),
    ),
    "code-review": SkillManifest(
        id="code-review",
        version="1.0.0",
        role=Role.QUALITY,
        purpose="Independently review the code changes against the specification and standards.",
        inputs=(ArtifactKind.CODE, ArtifactKind.CONTEXT),
        outputs=(ArtifactKind.REVIEW_REPORT,),
        instruction=(
            "You review code independently of its author. Check the diff at the pinned"
            " revision against the specification, the repository standards and the"
            " acceptance criteria. Report every finding with severity, file and line,"
            " and a required action; state explicitly what you verified and how."
        ),
        stop_conditions=(
            "The diff does not match the pinned revision.",
            "Review evidence cannot be produced from the provided context.",
        ),
    ),
    "acceptance-verification": SkillManifest(
        id="acceptance-verification",
        version="1.0.0",
        role=Role.QUALITY,
        purpose="Verify the implementation against the acceptance criteria and issue the verdict.",
        inputs=(ArtifactKind.SPEC, ArtifactKind.CODE, ArtifactKind.CONTEXT),
        outputs=(ArtifactKind.ACCEPTANCE_VERDICT,),
        instruction=(
            "You verify acceptance. For every acceptance criterion run the checks that"
            " prove or refute it and record the evidence. Issue one verdict per"
            " criterion plus an overall verdict; a criterion without evidence is not met."
        ),
        stop_conditions=(
            "An acceptance criterion cannot be verified from the produced evidence.",
            "The verdict depends on a check that cannot run in the current workspace.",
        ),
    ),
}


def get_skill(skill_id: str) -> SkillManifest:
    """Return the skill manifest registered under ``skill_id``.

    Raises ``SkillNotFoundError`` for an unknown id — profiles reference
    skills by id, so a typo must fail loudly.
    """
    try:
        return _SKILLS[skill_id]
    except KeyError:
        raise SkillNotFoundError(f"unknown skill id {skill_id!r}") from None
