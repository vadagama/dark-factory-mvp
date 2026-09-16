"""Unit tests of the PydanticAI harness adapter (T019) — offline, no network.

PydanticAI's ``TestModel``/``FunctionModel`` replace the real LiteLLM endpoint,
so the adapter logic (usage mapping, structured output, role tool restriction,
error and health policy) is exercised without configuration or secrets.
"""

import asyncio
import json
from decimal import Decimal

import pytest
from pydantic import BaseModel
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RequestUsage

from dark_factory.adapters.harness import (
    LLM_API_KEY_ENV_VAR,
    LLM_BASE_URL_ENV_VAR,
    LLM_MODEL_ENV_VAR,
    HarnessConfig,
    PydanticAIHarness,
)
from dark_factory.ports import HarnessPort, Role, Stage, TaskEnvelope, Usage

LLM_ENV_VARS = (LLM_BASE_URL_ENV_VAR, LLM_API_KEY_ENV_VAR, LLM_MODEL_ENV_VAR)
SECRET_API_KEY = "sk-litellm-secret-value"
BASE_URL = "http://litellm.internal:4000/v1"
MODEL = "openai/gpt-4o-mini"


def _config(
    base_url: str = BASE_URL,
    api_key: str = SECRET_API_KEY,
    model: str = MODEL,
) -> HarnessConfig:
    return HarnessConfig(base_url=base_url, api_key=api_key, model=model)


def _envelope(role: Role = Role.DEVELOP) -> TaskEnvelope:
    return TaskEnvelope(
        change_id="chg-001",
        run_id="run-001",
        stage=Stage.CONSTRUCTION,
        role=role,
        instruction="implement the feature",
    )


def _response_with_usage(input_tokens: int, output_tokens: int) -> ModelResponse:
    usage = RequestUsage(input_tokens=input_tokens, output_tokens=output_tokens)
    return ModelResponse(parts=[TextPart("done")], usage=usage)


def test_adapter_satisfies_protocol() -> None:
    harness: HarnessPort = PydanticAIHarness(_config(), model=TestModel())
    assert isinstance(harness, HarnessPort)


def test_run_stage_returns_versioned_text_result() -> None:
    harness = PydanticAIHarness(_config(), model=TestModel())
    result = asyncio.run(harness.run_stage(_envelope()))
    assert result.schema_version == 1
    assert result.ok is True
    assert result.output != ""


def test_run_stage_sends_instruction_as_system_and_user_prompt() -> None:
    captured: list[list[ModelMessage]] = []

    def capture(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        captured.append(list(messages))
        usage = RequestUsage(input_tokens=1, output_tokens=1)
        return ModelResponse(parts=[TextPart("done")], usage=usage)

    harness = PydanticAIHarness(_config(), model=FunctionModel(capture))
    result = asyncio.run(harness.run_stage(_envelope()))
    assert result.ok is True
    assert result.output == "done"
    parts = captured[0][0].parts
    instruction = "implement the feature"
    assert any(isinstance(part, SystemPromptPart) and part.content == instruction for part in parts)
    assert any(isinstance(part, UserPromptPart) and part.content == instruction for part in parts)


def test_run_stage_maps_usage_from_the_model() -> None:
    model = FunctionModel(lambda messages, info: _response_with_usage(12, 7))
    harness = PydanticAIHarness(_config(), model=model)
    result = asyncio.run(harness.run_stage(_envelope()))
    assert result.usage == Usage(
        prompt_tokens=12,
        completion_tokens=7,
        total_tokens=19,
        cost=None,
    )


def test_run_stage_maps_cost_when_the_model_reports_it() -> None:
    def priced(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        usage = RequestUsage(input_tokens=10, output_tokens=5, cost=Decimal("0.25"))
        return ModelResponse(parts=[TextPart("done")], usage=usage)

    harness = PydanticAIHarness(_config(), model=FunctionModel(priced))
    result = asyncio.run(harness.run_stage(_envelope()))
    assert result.usage is not None
    assert result.usage.cost == Decimal("0.25")


def _capturing_json_model(payload: str) -> tuple[FunctionModel, list[AgentInfo]]:
    """A model answering with ``payload`` text, recording the ``AgentInfo`` of each call.

    Prompted structured output arrives as text, exactly as this model replies; the
    recorded ``AgentInfo`` shows which tools (including output tools) were declared.
    """
    calls: list[AgentInfo] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls.append(info)
        return ModelResponse(
            parts=[TextPart(payload)], usage=RequestUsage(input_tokens=5, output_tokens=7)
        )

    return FunctionModel(respond), calls


def test_run_stage_supports_structured_output() -> None:
    class Verdict(BaseModel):
        approved: bool
        score: int

    model, _ = _capturing_json_model('{"approved": true, "score": 7}')
    harness = PydanticAIHarness(_config(), model=model)
    result = asyncio.run(harness.run_stage(_envelope(role=Role.QUALITY), output_type=Verdict))
    assert result.ok is True
    payload = json.loads(result.output)
    assert isinstance(payload["approved"], bool)
    assert isinstance(payload["score"], int)


def test_structured_output_uses_the_prompted_mode_not_a_forced_tool() -> None:
    """A structured answer must not force ``tool_choice`` (TD-015).

    The ``ToolOutput`` default registers an output tool and sends
    ``tool_choice: "required"``; an endpoint whose thinking mode is active
    rejects that (DeepSeek: "Thinking mode does not support this tool_choice").
    ``PromptedOutput`` asks for JSON in the answer instead, so no output tool is
    registered and the value still parses into the requested model.
    """

    class Verdict(BaseModel):
        approved: bool
        reason: str

    model, calls = _capturing_json_model('{"approved": true, "reason": "trivial"}')
    harness = PydanticAIHarness(_config(), model=model)
    result = asyncio.run(harness.run_stage(_envelope(role=Role.QUALITY), output_type=Verdict))
    assert result.ok is True
    assert json.loads(result.output)["reason"] == "trivial"
    assert calls, "the model must have been called"
    assert [tool.name for tool in calls[0].function_tools] == []


def test_run_stage_has_no_tools_by_default() -> None:
    model = TestModel()
    harness = PydanticAIHarness(_config(), model=model)
    asyncio.run(harness.run_stage(_envelope()))
    parameters = model.last_model_request_parameters
    assert parameters is not None
    assert [tool.name for tool in parameters.function_tools] == []


def test_run_stage_passes_only_the_role_tool_set() -> None:
    def add(a: int, b: int) -> int:
        """Add two integers."""
        return a + b

    model = TestModel()
    harness = PydanticAIHarness(_config(), model=model, role_tools={Role.DEVELOP: [add]})
    asyncio.run(harness.run_stage(_envelope(role=Role.DEVELOP)))
    parameters = model.last_model_request_parameters
    assert parameters is not None
    assert [tool.name for tool in parameters.function_tools] == ["add"]


def test_run_stage_does_not_leak_tools_to_other_roles() -> None:
    def add(a: int, b: int) -> int:
        """Add two integers."""
        return a + b

    model = TestModel()
    harness = PydanticAIHarness(_config(), model=model, role_tools={Role.DEVELOP: [add]})
    asyncio.run(harness.run_stage(_envelope(role=Role.QUALITY)))
    parameters = model.last_model_request_parameters
    assert parameters is not None
    assert [tool.name for tool in parameters.function_tools] == []


def test_run_stage_without_config_reports_failure_and_does_not_raise() -> None:
    harness = PydanticAIHarness()
    result = asyncio.run(harness.run_stage(_envelope()))
    assert result.ok is False
    assert "not configured" in result.output


def test_run_stage_maps_model_failure_to_failed_result_without_details() -> None:
    def fail(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise RuntimeError("boom: secret http://user:pass@litellm.internal")

    harness = PydanticAIHarness(_config(), model=FunctionModel(fail))
    result = asyncio.run(harness.run_stage(_envelope()))
    assert result.ok is False
    assert result.usage is None
    assert "RuntimeError" in result.output
    assert "boom" not in result.output


def test_health_is_unhealthy_without_config(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in LLM_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    health = asyncio.run(PydanticAIHarness().health())
    assert health.healthy is False
    detail = health.detail or ""
    assert all(name in detail for name in LLM_ENV_VARS)


def test_health_is_healthy_with_full_config() -> None:
    health = asyncio.run(PydanticAIHarness(_config()).health())
    assert health.healthy is True
    detail = health.detail or ""
    assert MODEL in detail
    assert "http://litellm.internal:4000" in detail
    assert "/v1" not in detail
    assert SECRET_API_KEY not in detail


def test_health_masks_credentials_embedded_in_base_url() -> None:
    config = _config(base_url="http://user:pass@litellm.internal:4000/v1")
    health = asyncio.run(PydanticAIHarness(config).health())
    assert health.healthy is True
    detail = health.detail or ""
    assert "http://litellm.internal:4000" in detail
    assert "user" not in detail and "pass" not in detail


def test_health_never_reports_the_api_key() -> None:
    unconfigured = asyncio.run(PydanticAIHarness().health())
    configured = asyncio.run(PydanticAIHarness(_config()).health())
    assert SECRET_API_KEY not in (unconfigured.detail or "")
    assert SECRET_API_KEY not in (configured.detail or "")


def test_health_reports_blank_api_key_without_echoing_it() -> None:
    config = _config(api_key="   ")
    health = asyncio.run(PydanticAIHarness(config).health())
    assert health.healthy is False
    assert LLM_API_KEY_ENV_VAR in (health.detail or "")


def test_health_reports_invalid_base_url_without_echoing_it() -> None:
    config = _config(base_url="not-a-url/%user:pass")
    health = asyncio.run(PydanticAIHarness(config).health())
    assert health.healthy is False
    detail = health.detail or ""
    assert LLM_BASE_URL_ENV_VAR in detail
    assert "user" not in detail and "pass" not in detail


def test_from_env_reads_strips_and_returns_config() -> None:
    env = {
        LLM_BASE_URL_ENV_VAR: f"  {BASE_URL} ",
        LLM_API_KEY_ENV_VAR: f" {SECRET_API_KEY} ",
        LLM_MODEL_ENV_VAR: f" {MODEL} ",
    }
    assert HarnessConfig.from_env(env) == _config()


def test_from_env_returns_none_when_any_var_is_missing_or_blank() -> None:
    assert HarnessConfig.from_env({}) is None
    incomplete = {
        LLM_BASE_URL_ENV_VAR: BASE_URL,
        LLM_API_KEY_ENV_VAR: "   ",
        LLM_MODEL_ENV_VAR: MODEL,
    }
    assert HarnessConfig.from_env(incomplete) is None


def test_missing_env_vars_lists_missing_names_in_order() -> None:
    assert HarnessConfig.missing_env_vars({}) == LLM_ENV_VARS
    partial = {LLM_BASE_URL_ENV_VAR: BASE_URL, LLM_MODEL_ENV_VAR: MODEL}
    assert HarnessConfig.missing_env_vars(partial) == (LLM_API_KEY_ENV_VAR,)
