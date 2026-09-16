"""Live LLM smoke of the harness adapter against the configured endpoint (TD-015).

Runs only when the endpoint is configured through the adapter's own variables:

- ``DARK_FACTORY_LLM_BASE_URL`` — OpenAI-compatible base URL of the endpoint;
- ``DARK_FACTORY_LLM_API_KEY`` — its API key (a secret, never logged);
- ``DARK_FACTORY_LLM_MODEL`` — model name served by the endpoint.

Without them (the default in CI) these tests skip: the HTTP path of the adapter
cannot be exercised offline, and the unit/contract suites keep the offline
guarantees. Locally the variables live in the repository ``.env``, which nothing
loads automatically — export them for the run, e.g.
``set -a; . ./.env; set +a; uv run pytest tests/integration/test_harness_live_llm.py``.

The structured-output case is the regression test for TD-015: the adapter used
to let PydanticAI force ``tool_choice: "required\"``, which a model with an active
thinking mode rejects with ``Thinking mode does not support this tool_choice``.
Every assertion also checks that neither the API key nor the base URL leaks into
the result (ADR-009).
"""

import asyncio
import json

import pytest
from pydantic import BaseModel

from dark_factory.adapters.harness import HarnessConfig, PydanticAIHarness
from dark_factory.ports import Role, Stage, TaskEnvelope


class Verdict(BaseModel):
    """Minimal structured answer: one flag and one line of rationale."""

    approved: bool
    reason: str


def _harness() -> PydanticAIHarness:
    config = HarnessConfig.from_env()
    if config is None:
        pytest.skip(
            "LLM endpoint is not configured; set "
            f"{', '.join(HarnessConfig.missing_env_vars())} to run the live smoke"
        )
    return PydanticAIHarness(config)


def _envelope(role: Role = Role.QUALITY) -> TaskEnvelope:
    return TaskEnvelope(
        change_id="chg-harness-live",
        run_id="run-harness-live",
        stage=Stage.REVIEW_VERIFICATION,
        role=role,
        instruction="Approve this documentation-only change and give a one-line reason.",
    )


def _assert_no_secret_leak(result_output: str) -> None:
    config = HarnessConfig.from_env()
    assert config is not None
    assert config.api_key not in result_output
    assert config.base_url not in result_output


def test_live_plain_text_call_returns_output_and_usage() -> None:
    result = asyncio.run(_harness().run_stage(_envelope()))

    assert result.ok is True, result.output
    assert result.output.strip()
    assert result.usage is not None
    assert result.usage.total_tokens
    assert result.usage.total_tokens > 0
    _assert_no_secret_leak(result.output)


def test_live_structured_call_survives_an_active_thinking_mode() -> None:
    """The prompted output mode must work while the endpoint reasons (TD-015)."""
    result = asyncio.run(_harness().run_stage(_envelope(), output_type=Verdict))

    assert result.ok is True, result.output
    payload = json.loads(result.output)
    assert isinstance(payload["approved"], bool)
    assert isinstance(payload["reason"], str)
    assert payload["reason"].strip()
    assert result.usage is not None
    assert result.usage.total_tokens
    _assert_no_secret_leak(result.output)
