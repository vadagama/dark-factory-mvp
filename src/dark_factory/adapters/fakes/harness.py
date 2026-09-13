"""In-memory fake of the harness port (PydanticAI adapter arrives with T-010)."""

from dark_factory.ports import AgentResult, HarnessPort, HealthStatus, TaskEnvelope


class FakeHarness(HarnessPort):
    """In-memory ``HarnessPort`` returning a deterministic canned result.

    The output is derived from the envelope, so equal envelopes produce equal
    results and tests can assert determinism. ``usage`` stays ``None``: the real
    harness reports actual token usage (T-010).
    """

    async def run_stage(self, envelope: TaskEnvelope, /) -> AgentResult:
        return AgentResult(
            ok=True,
            output=f"fake:{envelope.role.value}:{envelope.stage.value}:{envelope.instruction}",
        )

    async def health(self, /) -> HealthStatus:
        return HealthStatus(healthy=True, detail="in-memory fake harness")
