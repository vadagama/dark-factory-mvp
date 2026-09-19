"""«Помоги сформулировать»: free text → brief through the harness (T072)."""

import asyncio

import pytest

from dark_factory.adapters.fakes import FakeHarness
from dark_factory.changes.enums import BriefAuthor, BriefStatus, Role, Stage
from dark_factory.changes.intake import IntakeBrief
from dark_factory.orchestration.intake import INTAKE_RUN_ID, BriefFormulator, brief_instruction
from dark_factory.ports import AgentResult, HealthStatus, TaskEnvelope

SOURCE = "Login takes 3 seconds. Make it under 300 ms without a new database. Mobile app is out."


class ScriptedHarness:
    """Harness stand-in answering with a scripted output or raising."""

    def __init__(
        self, output: str | None = None, *, ok: bool = True, error: Exception | None = None
    ) -> None:
        self.output = output
        self.ok = ok
        self.error = error
        self.envelopes: list[TaskEnvelope] = []

    async def run_stage(self, envelope: TaskEnvelope, /) -> AgentResult:
        self.envelopes.append(envelope)
        if self.error is not None:
            raise self.error
        return AgentResult(ok=self.ok, output=self.output or "")

    async def health(self, /) -> HealthStatus:
        return HealthStatus(healthy=True, detail="scripted")


def _formulate(harness: ScriptedHarness | FakeHarness, text: str = SOURCE) -> IntakeBrief:
    return asyncio.run(BriefFormulator(harness).formulate(text))


def test_a_json_answer_becomes_a_complete_brief() -> None:
    harness = ScriptedHarness(
        'Here you go:\n{"problem": "Login takes 3 seconds", "goal": "Login under 300 ms",'
        ' "constraints": ["no new database", ""], "out_of_scope": ["mobile app"]}'
    )
    brief = _formulate(harness)
    assert brief.status is BriefStatus.COMPLETE
    assert brief.problem == "Login takes 3 seconds"
    assert brief.goal == "Login under 300 ms"
    assert brief.constraints == ("no new database",)
    assert brief.out_of_scope == ("mobile app",)
    assert brief.source_text == SOURCE, "the operator's wording is kept"
    assert brief.formulated_by is BriefAuthor.AGENT
    assert brief.error is None


def test_the_envelope_is_the_product_role_at_intake() -> None:
    harness = ScriptedHarness('{"problem": "p", "goal": "g"}')
    _formulate(harness)
    envelope = harness.envelopes[0]
    assert envelope.role is Role.PRODUCT
    assert envelope.stage is Stage.SPECIFICATION
    assert envelope.run_id == INTAKE_RUN_ID
    assert envelope.instruction == brief_instruction(SOURCE)
    assert SOURCE in envelope.instruction


def test_a_partial_answer_is_a_draft_with_what_it_has() -> None:
    brief = _formulate(ScriptedHarness('{"problem": "p", "goal": null, "constraints": "a\\nb"}'))
    assert brief.status is BriefStatus.DRAFT
    assert brief.problem == "p"
    assert brief.goal is None
    assert brief.constraints == ("a", "b")
    assert brief.error is None


def test_a_non_brief_answer_is_a_draft_with_an_observable_error() -> None:
    brief = _formulate(ScriptedHarness("I cannot help with that."))
    assert brief.status is BriefStatus.DRAFT
    assert brief.problem is None
    assert brief.error is not None and "was not a brief" in brief.error
    assert brief.source_text == SOURCE


def test_a_refusing_harness_keeps_the_draft_and_the_reason() -> None:
    brief = _formulate(
        ScriptedHarness("LLM is not configured; set the DARK_FACTORY_LLM_* variables", ok=False)
    )
    assert brief.status is BriefStatus.DRAFT
    assert brief.error == (
        "the agent could not formulate the brief: LLM is not configured; set the"
        " DARK_FACTORY_LLM_* variables"
    )


def test_a_raising_harness_never_echoes_the_exception_text() -> None:
    brief = _formulate(
        ScriptedHarness(error=RuntimeError("https://user:hunter2@llm.example/v1 refused"))
    )
    assert brief.status is BriefStatus.DRAFT
    assert brief.error == "harness call failed: RuntimeError"
    assert "hunter2" not in (brief.error or "")


def test_empty_text_is_refused_before_the_harness() -> None:
    harness = ScriptedHarness('{"problem": "p", "goal": "g"}')
    brief = _formulate(harness, "   ")
    assert brief.error == "the source text is empty"
    assert harness.envelopes == [], "no agent call for nothing"


def test_the_fake_harness_yields_a_draft_not_an_invented_brief() -> None:
    # The contract fake echoes the envelope: not a brief, so the result is an
    # honest draft — the fixture path of the API and CLI tests.
    brief = _formulate(FakeHarness())
    assert brief.status is BriefStatus.DRAFT
    assert brief.error is not None


@pytest.mark.parametrize("text", ["x", "make it faster"])
def test_the_instruction_carries_the_operator_text_verbatim(text: str) -> None:
    assert brief_instruction(f"  {text}  ").endswith(text)
