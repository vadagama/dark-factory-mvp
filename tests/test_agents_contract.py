"""Contract tests of ``AgentProfile -> TaskEnvelope -> AgentResult`` (T-011, ADR-007 p.3)."""

from datetime import UTC, datetime
from typing import Any

import pytest

from dark_factory.agents import build_envelope, get_profile, get_skill, validate_agent_result
from dark_factory.changes.enums import Role, Stage
from dark_factory.context.bundle import ContextBundle, ContextSource, SourceKind, build_bundle
from dark_factory.ports import AgentResult, TaskEnvelope, Usage

RETRIEVED_AT = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)


def _bundle() -> ContextBundle:
    return build_bundle(
        change_id="chg-001",
        run_id="run-001",
        sources=[
            ContextSource(
                kind=SourceKind.REPO,
                location="src/dark_factory",
                revision="abc123",
                content_hash="a" * 64,
                retrieved_at=RETRIEVED_AT,
            )
        ],
    )


def _args(**overrides: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "profile": get_profile(Role.DEVELOP),
        "skill": get_skill("implementation"),
        "bundle": _bundle(),
        "change_id": "chg-001",
        "run_id": "run-001",
        "stage": Stage.CONSTRUCTION,
        "instruction": "implement the feature",
    }
    args.update(overrides)
    return args


def test_envelope_fixes_role_skill_and_context() -> None:
    envelope = build_envelope(**_args())
    assert envelope.schema_version == 1
    assert envelope.role == Role.DEVELOP
    assert envelope.skill_id == "implementation"
    assert envelope.bundle_hash == _bundle().bundle_hash
    assert envelope.change_id == "chg-001"
    assert envelope.run_id == "run-001"
    assert envelope.stage == Stage.CONSTRUCTION
    assert envelope.instruction == "implement the feature"


def test_envelope_rejects_a_skill_of_another_role() -> None:
    with pytest.raises(ValueError, match="belongs to role"):
        build_envelope(**_args(skill=get_skill("code-review")))


def test_envelope_rejects_an_unbound_skill_of_the_same_role() -> None:
    unbound = get_skill("implementation").model_copy(update={"id": "extra-skill"})
    with pytest.raises(ValueError, match="not bound"):
        build_envelope(**_args(skill=unbound))


def test_envelope_rejects_a_bundle_of_another_change() -> None:
    with pytest.raises(ValueError, match="change"):
        build_envelope(**_args(change_id="chg-999"))


def test_envelope_rejects_a_bundle_of_another_run() -> None:
    with pytest.raises(ValueError, match="run"):
        build_envelope(**_args(run_id="run-999"))


def test_successful_result_with_output_is_valid() -> None:
    validate_agent_result(AgentResult(ok=True, output="specification written"))


def test_successful_result_must_carry_non_empty_output() -> None:
    with pytest.raises(ValueError, match="non-empty output"):
        validate_agent_result(AgentResult(ok=True, output=""))
    with pytest.raises(ValueError, match="non-empty output"):
        validate_agent_result(AgentResult(ok=True, output="   "))


def test_failed_result_may_carry_any_output() -> None:
    validate_agent_result(AgentResult(ok=False))
    validate_agent_result(AgentResult(ok=False, output="harness call failed: RuntimeError"))


def test_result_usage_is_optional() -> None:
    usage = Usage(prompt_tokens=12, completion_tokens=7, total_tokens=19)
    validate_agent_result(AgentResult(ok=True, output="done", usage=usage))


def test_minimal_envelope_without_new_fields_still_validates() -> None:
    envelope = TaskEnvelope(
        change_id="chg-001",
        run_id="run-001",
        stage=Stage.CONSTRUCTION,
        role=Role.DEVELOP,
        instruction="implement the feature",
    )
    assert envelope.skill_id is None
    assert envelope.bundle_hash is None
    assert envelope.schema_version == 1


def test_minimal_result_still_validates() -> None:
    result = AgentResult(ok=True, output="done")
    assert result.usage is None
    assert result.schema_version == 1
    validate_agent_result(result)
