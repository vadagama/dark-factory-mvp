"""PydanticAI implementation of ``HarnessPort`` (T019, docs T-010).

``run_stage`` executes one agent call through a PydanticAI ``Agent`` built from
the environment configuration: the LLM endpoint is the existing LiteLLM proxy
(OpenAI-compatible, ADR-002 §6), the model name comes from the config (never
hardcoded) and ``envelope.instruction`` is both the system prompt and the user
prompt — the minimal envelope carries nothing else (T-011 extends it).

Error policy: ``run_stage`` never raises. Deterministic stage steps must
survive a disabled or unreachable LLM (US1 scenario 3, R-17), so every failure
— missing configuration, timeouts, connection errors — is translated into
``AgentResult(ok=False)``. The output carries only the exception type name:
exception texts can embed request URLs or credentials, so they are never
surfaced (ADR-009).

Usage accounting: PydanticAI token usage maps onto the core ``Usage``
(``prompt_tokens=input_tokens``, ``completion_tokens=output_tokens``,
``total_tokens=input+output``). ``cost`` is PydanticAI's best-effort value
(genai-prices) and stays ``None`` when the proxied model cannot be priced;
budget enforcement is the budget coordinator's concern (T-062), and LiteLLM
may report cost in addition later.
"""

from collections.abc import Callable, Mapping, Sequence
from typing import Any, TypeVar
from urllib.parse import urlparse

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import RunUsage

from dark_factory.adapters.harness.config import (
    LLM_API_KEY_ENV_VAR,
    LLM_BASE_URL_ENV_VAR,
    LLM_MODEL_ENV_VAR,
    HarnessConfig,
)
from dark_factory.ports import AgentResult, HarnessPort, HealthStatus, Role, TaskEnvelope, Usage

type ToolFunction = Callable[..., Any]
"""Tool accepted by ``Agent(tools=...)``: a plain callable (a typed ``Tool`` fits too)."""

type RoleTools = Mapping[Role, Sequence[ToolFunction]]
"""Tool sets per agent role; T-011 fills them from the role profiles."""

TOutput = TypeVar("TOutput", bound=BaseModel)


class PydanticAIHarness(HarnessPort):
    """``HarnessPort`` adapter executing agent calls through PydanticAI.

    The agent is built per call, so construction and import never fail on
    missing configuration — the gap is reported lazily by ``health()`` and
    ``run_stage()`` (US1 scenario 3). Tools are restricted by role: with the
    default empty mapping the agent runs without tools until T-011 supplies
    the sets from the role profiles. ``model`` overrides the configured LLM
    endpoint; it exists for tests (PydanticAI ``TestModel``/``FunctionModel``)
    and offline wiring, not for production use.
    """

    def __init__(
        self,
        config: HarnessConfig | None = None,
        *,
        role_tools: RoleTools | None = None,
        model: Model | None = None,
    ) -> None:
        self._config = config
        self._role_tools: RoleTools = {} if role_tools is None else role_tools
        self._model = model

    async def run_stage(
        self,
        envelope: TaskEnvelope,
        /,
        *,
        output_type: type[TOutput] | None = None,
    ) -> AgentResult:
        """Run one agent call; never raises — failures come back as ``ok=False``.

        ``output_type`` requests a structured answer (a pydantic model); the
        value is serialized into ``AgentResult.output`` (``str``) until the
        contract carries structured payloads (T-011). With ``None`` the agent
        answers in plain text.
        """
        model = self._model_for_run()
        if model is None:
            return AgentResult(
                ok=False,
                output="LLM is not configured; set the DARK_FACTORY_LLM_* variables"
                " (see harness health for the missing ones)",
            )
        try:
            agent = Agent(
                model,
                output_type=str if output_type is None else output_type,
                system_prompt=envelope.instruction,
                tools=list(self._role_tools.get(envelope.role, ())),
            )
            result = await agent.run(envelope.instruction)
        except Exception as exc:
            # Boundary translation: no exception may escape into the
            # deterministic Flow (US1 scenario 3). Only the type name is
            # surfaced — exception texts can embed URLs or credentials (ADR-009).
            return AgentResult(ok=False, output=f"harness call failed: {type(exc).__name__}")
        return AgentResult(
            ok=True,
            output=_render_output(result.output),
            usage=_map_usage(result.usage),
        )

    async def health(self, /) -> HealthStatus:
        """Offline configuration probe (``factory doctor`` style); no network call.

        When the harness was built unconfigured, the detail names the missing
        environment variables. For a present config the values are validated
        without being echoed: the api key and any credentials embedded in the
        base URL never reach the detail (ADR-009).
        """
        if self._config is None:
            detail = "LLM is not configured"
            missing = HarnessConfig.missing_env_vars()
            if missing:
                detail = f"{detail}: missing {', '.join(missing)}"
            return HealthStatus(healthy=False, detail=detail)
        config = self._config
        if not config.model.strip():
            return HealthStatus(healthy=False, detail=f"{LLM_MODEL_ENV_VAR} must not be blank")
        if not config.api_key.strip():
            return HealthStatus(healthy=False, detail=f"{LLM_API_KEY_ENV_VAR} must not be blank")
        if not _is_valid_url(config.base_url):
            return HealthStatus(healthy=False, detail=f"{LLM_BASE_URL_ENV_VAR} is not a valid URL")
        return HealthStatus(
            healthy=True,
            detail=(
                f"LLM endpoint configured: model={config.model},"
                f" endpoint={_masked_url(config.base_url)} (api key is not shown)"
            ),
        )

    def _model_for_run(self) -> Model | None:
        """The model of the next call: the injected override or the configured endpoint."""
        if self._model is not None:
            return self._model
        config = self._config
        if config is None:
            return None
        provider = OpenAIProvider(base_url=config.base_url, api_key=config.api_key)
        return OpenAIChatModel(config.model, provider=provider)


def _is_valid_url(value: str) -> bool:
    """True when ``value`` parses into a scheme and a host (offline check)."""
    parsed = urlparse(value)
    return bool(parsed.scheme) and parsed.hostname is not None


def _masked_url(value: str) -> str:
    """Scheme, host and port of ``value``; path, userinfo and query are dropped."""
    parsed = urlparse(value)
    netloc = parsed.hostname or ""
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    return f"{parsed.scheme}://{netloc}"


def _render_output(output: Any) -> str:
    """Text of an agent result; structured outputs serialize until T-011 widens the contract."""
    if isinstance(output, BaseModel):
        return output.model_dump_json()
    return str(output)


def _map_usage(usage: RunUsage) -> Usage:
    """Map PydanticAI run usage onto the core ``Usage`` value type.

    ``total_tokens`` is computed: PydanticAI reports input and output tokens
    separately (cache and audio tokens are subsets/additions the minimal core
    value type does not carry yet). ``cost`` passes through unchanged — a
    best-effort USD value or ``None`` when the model cannot be priced.
    """
    return Usage(
        prompt_tokens=usage.input_tokens,
        completion_tokens=usage.output_tokens,
        total_tokens=usage.input_tokens + usage.output_tokens,
        cost=usage.cost,
    )
