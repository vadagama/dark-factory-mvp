"""The ``AgentProfile -> TaskEnvelope -> AgentResult`` contract (ADR-007 p.3).

``build_envelope`` turns a validated (profile, skill) pair plus the run
identity into a ``TaskEnvelope`` that pins role, skill and the ``ContextBundle``
snapshot the agent must work from (FR-001). ``validate_agent_result`` enforces
the result side: a successful call must carry non-empty output, ``usage``
stays optional.
"""

from dark_factory.agents.profiles.manifest import AgentProfile
from dark_factory.agents.skills.manifest import SkillManifest
from dark_factory.changes.enums import Stage
from dark_factory.context.bundle import ContextBundle
from dark_factory.ports.agents import AgentResult, TaskEnvelope


def build_envelope(
    *,
    profile: AgentProfile,
    skill: SkillManifest,
    bundle: ContextBundle,
    change_id: str,
    run_id: str,
    stage: Stage,
    instruction: str,
) -> TaskEnvelope:
    """Build the envelope of one agent call, fixing role, skill and context.

    Raises ``ValueError`` when the skill belongs to another role, is not bound
    to the profile, or the bundle belongs to another change/run.
    """
    if skill.role != profile.role:
        raise ValueError(
            f"skill {skill.id!r} belongs to role {skill.role.value!r}, not {profile.role.value!r}"
        )
    if skill.id not in profile.skills:
        raise ValueError(f"skill {skill.id!r} is not bound to profile {profile.role.value!r}")
    if bundle.change_id != change_id:
        raise ValueError(f"bundle belongs to change {bundle.change_id!r}, not {change_id!r}")
    if bundle.run_id != run_id:
        raise ValueError(f"bundle belongs to run {bundle.run_id!r}, not {run_id!r}")
    return TaskEnvelope(
        change_id=change_id,
        run_id=run_id,
        stage=stage,
        role=profile.role,
        skill_id=skill.id,
        bundle_hash=bundle.bundle_hash,
        instruction=instruction,
    )


def validate_agent_result(result: AgentResult) -> None:
    """Validate an ``AgentResult`` against the result-side contract.

    A successful call must carry non-empty output; a failed call may carry any
    output (failure detail). ``usage`` is optional either way. Raises
    ``ValueError`` on violation.
    """
    if result.ok and not result.output.strip():
        raise ValueError("successful AgentResult must carry non-empty output")
