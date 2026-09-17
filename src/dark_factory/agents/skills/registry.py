"""Registry of the built-in skill manifests (T-011, T-046, T-047).

Twenty skills: product drives intake through the change request, design
turns the specification into UX artifacts, architect assesses impact and
drafts ADRs, develop implements and reworks, quality reviews and verifies,
security threat-models and analyzes (IAM, secrets, dependencies),
infrastructure designs and implements infrastructure as code, ci_cd delivers
the pipeline and runs the task git cycle, and operation verifies smoke and
analyzes rollback for the Operation verdict of the release gate. Binding
checks (a skill belongs to its role, a profile references only its own
skills) live in the contract builder and the tests, not here — the
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
    "ux-flow": SkillManifest(
        id="ux-flow",
        version="1.0.0",
        role=Role.DESIGN,
        purpose=(
            "Turn specification requirements into user flows, a screen inventory"
            " and screen states mapped onto the UI kit."
        ),
        inputs=(ArtifactKind.SPEC, ArtifactKind.CONTEXT),
        outputs=(ArtifactKind.UX_SPEC,),
        instruction=(
            "You design the UX. Derive the user flows from the specification,"
            " list every screen with its purpose and map each screen onto the"
            " existing UI kit components. For every screen define the loading,"
            " empty and error states; do not invent components the kit does not"
            " have."
        ),
        stop_conditions=(
            "A flow requires a UI-kit pattern that does not exist in the kit.",
            "The specification leaves the screen behaviour undecidable and"
            " clarification is unavailable.",
        ),
    ),
    "accessibility-review": SkillManifest(
        id="accessibility-review",
        version="1.0.0",
        role=Role.DESIGN,
        purpose=(
            "Review the UX specification against WCAG 2.2 AA and the UI kit's"
            " accessibility patterns, recording per-screen accessibility"
            " requirements."
        ),
        inputs=(ArtifactKind.UX_SPEC, ArtifactKind.CONTEXT),
        outputs=(ArtifactKind.UX_SPEC,),
        instruction=(
            "You review accessibility. Check every screen of the UX specification"
            " against WCAG 2.2 AA and the accessibility patterns of the UI kit,"
            " and record concrete per-screen requirements (contrast, focus order,"
            " keyboard access, labels). A screen without recorded requirements is"
            " a finding, not a pass."
        ),
        stop_conditions=(
            "The UX specification has no screen inventory to review.",
            "An accessibility requirement conflicts with an accepted UI-kit or"
            " architecture decision - escalate.",
        ),
    ),
    "impact-analysis": SkillManifest(
        id="impact-analysis",
        version="1.0.0",
        role=Role.ARCHITECT,
        purpose=(
            "Assess the change's impact on architecture boundaries, public"
            " contracts, the data model and NFRs, and produce a verdict on"
            " feasible routes."
        ),
        inputs=(ArtifactKind.REQUIREMENTS, ArtifactKind.SPEC, ArtifactKind.CONTEXT),
        outputs=(ArtifactKind.ARCHITECTURE_REVIEW,),
        instruction=(
            "You assess the architectural impact. Trace the change against the"
            " architecture boundaries, public contracts, data model and NFRs of"
            " the current codebase and accepted ADRs, and state for every route"
            " whether it is feasible and what it costs. Every conclusion must"
            " reference the code or ADR it rests on."
        ),
        stop_conditions=(
            "The impact cannot be assessed from the provided context.",
            "The change conflicts with an accepted ADR - escalate.",
        ),
    ),
    "adr-proposal": SkillManifest(
        id="adr-proposal",
        version="1.0.0",
        role=Role.ARCHITECT,
        purpose=(
            "Draft an ADR for a significant pending decision using the repository"
            " ADR template, with options considered and consequences."
        ),
        inputs=(ArtifactKind.ARCHITECTURE_REVIEW, ArtifactKind.CONTEXT),
        outputs=(ArtifactKind.ADR_PROPOSAL,),
        instruction=(
            "You draft an ADR. Follow the repository ADR template: context,"
            " decision, alternatives with trade-offs and consequences. Ground"
            " the proposal in the impact analysis; the draft is a proposal for"
            " human review, not an accepted decision."
        ),
        stop_conditions=(
            "The pending decision belongs to a human (product priority, budget, risk acceptance).",
            "The impact analysis does not support any option strongly enough to draft one.",
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
    "threat-model": SkillManifest(
        id="threat-model",
        version="1.0.0",
        role=Role.SECURITY,
        purpose=(
            "Threat-model the change: trust boundaries, data flows and access,"
            " and the security requirements the design must carry."
        ),
        inputs=(ArtifactKind.SPEC, ArtifactKind.CONTEXT),
        outputs=(ArtifactKind.SECURITY_REVIEW,),
        instruction=(
            "You threat-model the change. Identify the trust boundaries, data"
            " flows and access paths it creates or touches, enumerate the"
            " plausible threats per boundary and record concrete security"
            " requirements. A boundary without recorded requirements is a"
            " finding, not a pass."
        ),
        stop_conditions=(
            "The specification leaves a trust boundary undecidable and"
            " clarification is unavailable.",
            "A threat requires accepting a product or architecture risk -"
            " escalate to a human decision.",
        ),
    ),
    "security-analysis": SkillManifest(
        id="security-analysis",
        version="1.0.0",
        role=Role.SECURITY,
        purpose=(
            "Analyze the change for security defects: IAM, secrets handling and"
            " dependencies (supply chain), each finding with severity and a"
            " required action."
        ),
        inputs=(ArtifactKind.CODE, ArtifactKind.CONTEXT),
        outputs=(ArtifactKind.SECURITY_REVIEW,),
        instruction=(
            "You analyze security. Check the change at the pinned revision for"
            " IAM over-grants, secrets in code or configuration, unsafe data"
            " handling and dependency risks (known vulnerabilities, unpinned"
            " sources). Report every finding with severity, location and a"
            " required action; name an exposure without reproducing the secret"
            " itself."
        ),
        stop_conditions=(
            "A finding requires a decision outside the reviewed scope - escalate.",
            "Analysis evidence cannot be produced from the provided context.",
        ),
    ),
    "infra-design": SkillManifest(
        id="infra-design",
        version="1.0.0",
        role=Role.INFRASTRUCTURE,
        purpose=(
            "Design the infrastructure change: environments, Helm/Kubernetes"
            " manifests, network and IAM as code, before anything is applied."
        ),
        inputs=(ArtifactKind.SPEC, ArtifactKind.CONTEXT),
        outputs=(ArtifactKind.INFRASTRUCTURE_CHANGE,),
        instruction=(
            "You design the infrastructure change. Translate the specification"
            " into infrastructure as code: name the affected environments, the"
            " Helm and Kubernetes resources, and the network and IAM deltas,"
            " keeping the definitions vendor-neutral where the stack allows."
            " Prefer the smallest reversible change and state what stays manual"
            " and why. The design is a plan for review, not an applied state."
        ),
        stop_conditions=(
            "The design requires cloud resources the current provider setup cannot express.",
            "The design would change the stack or introduce a new platform"
            " component - an ADR is required.",
            "The live environment contradicts the repository state (drift,"
            " access) and the facts cannot be confirmed.",
        ),
    ),
    "infra-change": SkillManifest(
        id="infra-change",
        version="1.0.0",
        role=Role.INFRASTRUCTURE,
        purpose=(
            "Implement the approved infrastructure design as reviewed,"
            " applicable IaC with validation evidence."
        ),
        inputs=(ArtifactKind.INFRASTRUCTURE_CHANGE, ArtifactKind.CONTEXT),
        outputs=(ArtifactKind.INFRASTRUCTURE_CHANGE,),
        instruction=(
            "You implement the infrastructure change. Write the IaC delta in the"
            " repository's existing layout and style, validate it (plan, diff or"
            " template validation) and record the evidence. Keep the change"
            " idempotent and reversible; reference secrets by name from the"
            " secret store and never put credentials into the code."
        ),
        stop_conditions=(
            "The validation cannot run in the current workspace or needs"
            " credentials that are not available.",
            "The change requires a manual console action - stop; it contradicts"
            " infrastructure as code.",
        ),
    ),
    "pipeline-delivery": SkillManifest(
        id="pipeline-delivery",
        version="1.0.0",
        role=Role.CI_CD,
        purpose=(
            "Build and keep the delivery pipeline as code: build, repository"
            " checks, release packaging and promotion."
        ),
        inputs=(ArtifactKind.CODE, ArtifactKind.CONTEXT),
        outputs=(ArtifactKind.PIPELINE_CONFIG,),
        instruction=(
            "You deliver the pipeline. Express build, checks and release"
            " packaging as pipeline code in the repository's existing CI layout,"
            " mirroring the local checks (lint, typecheck, tests). Keep the gates"
            " honest: a check that cannot run in CI needs explicit justification,"
            " never a silent skip, and secrets are referenced by name from the"
            " secret store."
        ),
        stop_conditions=(
            "The pipeline needs an external service or secret that is not provisioned.",
            "A required gate cannot run in CI without being weakened - escalate.",
        ),
    ),
    "task-git-cycle": SkillManifest(
        id="task-git-cycle",
        version="1.0.0",
        role=Role.CI_CD,
        purpose=(
            "Run the git cycle of the task: task branch from the current main,"
            " conventional commits and a merge request with evidence; the merge"
            " itself stays a human decision."
        ),
        inputs=(ArtifactKind.CODE, ArtifactKind.CONTEXT),
        outputs=(ArtifactKind.CHANGE_REQUEST,),
        instruction=(
            "You run the git cycle of the task. Create the task branch from the"
            " current main, keep the commits conventional and focused, push the"
            " branch and open the merge request against main with the evidence"
            " of the checks you ran. Prepare the merge but never perform it:"
            " merging is a human decision (ADR-011)."
        ),
        stop_conditions=(
            "The merge request needs an approval or a decision that belongs to a human.",
            "The task branch cannot be created from a stable main.",
        ),
    ),
    "smoke-verification": SkillManifest(
        id="smoke-verification",
        version="1.0.0",
        role=Role.OPERATION,
        purpose=(
            "Run the operational smoke checks of a released revision and record"
            " the evidence the Operation verdict of the release gate rests on."
        ),
        inputs=(ArtifactKind.SPEC, ArtifactKind.CONTEXT),
        outputs=(ArtifactKind.OPS_VERDICT,),
        instruction=(
            "You verify the release in operation. Run the smoke checks that prove"
            " the released revision serves its basic function and record the"
            " evidence: the checks, their outcomes and the revision they pin. A"
            " check that cannot run or stays inconclusive is a failed verdict,"
            " not a pass."
        ),
        stop_conditions=(
            "The smoke evidence cannot be produced for the pinned revision.",
            "A smoke failure indicates an incident beyond the runbook - escalate.",
        ),
    ),
    "rollback-analysis": SkillManifest(
        id="rollback-analysis",
        version="1.0.0",
        role=Role.OPERATION,
        purpose=(
            "Analyze the rollback path of the release and supply the rollback"
            " evidence for the Operation verdict of the release gate."
        ),
        inputs=(ArtifactKind.CODE, ArtifactKind.CONTEXT),
        outputs=(ArtifactKind.OPS_VERDICT,),
        instruction=(
            "You analyze the rollback. Trace how the released revision would be"
            " undone: data and schema effects, configuration and environment"
            " deltas, and the cost of rolling back versus fixing forward. Record"
            " the evidence the Operation verdict of the release gate rests on;"
            " the verdict machine itself is the deterministic release policy, and"
            " the definition of the human/operational verdict remains a separate"
            " decision (ADR-023)."
        ),
        stop_conditions=(
            "The rollback path cannot be traced from the provided context.",
            "A rollback decision requires a human risk acceptance - stop and ask.",
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
