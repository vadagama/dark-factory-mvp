"""«Помоги сформулировать»: free text → structured brief through the harness (T072).

The operator writes what they want in their own words; the ``product`` role
turns it into the four fields of an :class:`IntakeBrief` (problem, goal,
constraints, out of scope). The original text is kept in ``source_text`` so the
agent's wording never replaces the intent, and the operator can edit the result
before the change is created.

The formulator is fail-open for the *form* and fail-closed for the *claim*:
when the harness is unavailable, refuses, raises or answers something that is
not a brief, the result is still a brief — a ``draft`` with ``error`` naming
the cause — never an invented ``complete`` one. No exception text that could
embed a URL or a credential is echoed (ADR-009): the harness already reduces
failures to type names, and this module keeps that discipline.
"""

import json
import re
from typing import Any, Final

from dark_factory.changes.enums import BriefAuthor, Role, Stage
from dark_factory.changes.intake import IntakeBrief, clean_lines
from dark_factory.ports import HarnessPort, TaskEnvelope

__all__ = ["BRIEF_FIELDS", "INTAKE_RUN_ID", "BriefFormulator", "brief_instruction"]

INTAKE_RUN_ID: Final[str] = "intake"
"""``run_id`` of a formulation envelope: intake happens before any run exists."""

BRIEF_FIELDS: Final[tuple[str, ...]] = ("problem", "goal", "constraints", "out_of_scope")

_JSON_BLOCK: Final[re.Pattern[str]] = re.compile(r"\{.*\}", re.S)


def brief_instruction(source_text: str) -> str:
    """The prompt: derive the four fields, answer with one JSON object, invent nothing."""
    return (
        "You are the product role of a software factory. An operator describes a change"
        " they want in free text. Turn it into a structured brief with exactly these"
        ' JSON keys: "problem" (what hurts today, one paragraph), "goal" (what must'
        ' be true when the change is done, measurable when possible), "constraints"'
        ' (a JSON array of short strings: what must not change), "out_of_scope" (a'
        " JSON array of short strings: what is explicitly left out). Keep the operator's"
        " language. Do not invent facts the text does not support: when a field is not"
        " stated, use null for strings and [] for arrays. Answer with the JSON object"
        " only, no prose.\n\nOperator text:\n"
        f"{source_text.strip()}"
    )


class BriefFormulator:
    """Formulate a brief from free text through one harness call (T072)."""

    def __init__(self, harness: HarnessPort) -> None:
        self._harness = harness

    async def formulate(self, source_text: str, *, change_id: str = INTAKE_RUN_ID) -> IntakeBrief:
        """One agent call; the result is always a brief, a ``draft`` on any failure."""
        text = source_text.strip()
        if not text:
            return IntakeBrief(source_text=None, error="the source text is empty")
        envelope = TaskEnvelope(
            change_id=change_id,
            run_id=INTAKE_RUN_ID,
            stage=Stage.SPECIFICATION,
            role=Role.PRODUCT,
            instruction=brief_instruction(text),
        )
        try:
            result = await self._harness.run_stage(envelope)
        except Exception as exc:
            # The type name only: exception texts can embed URLs or keys (ADR-009).
            return IntakeBrief(
                source_text=text,
                formulated_by=BriefAuthor.AGENT,
                error=f"harness call failed: {type(exc).__name__}",
            )
        if not result.ok:
            return IntakeBrief(
                source_text=text,
                formulated_by=BriefAuthor.AGENT,
                error=f"the agent could not formulate the brief: {result.output or 'no detail'}",
            )
        fields = _parse_fields(result.output)
        if fields is None:
            return IntakeBrief(
                source_text=text,
                formulated_by=BriefAuthor.AGENT,
                error="the agent answer was not a brief (expected one JSON object with"
                " problem, goal, constraints, out_of_scope)",
            )
        return IntakeBrief(
            problem=fields["problem"],
            goal=fields["goal"],
            constraints=fields["constraints"],
            out_of_scope=fields["out_of_scope"],
            source_text=text,
            formulated_by=BriefAuthor.AGENT,
        )


def _parse_fields(output: str) -> dict[str, Any] | None:
    """The four brief fields from the agent answer, or ``None`` when it is not a brief."""
    match = _JSON_BLOCK.search(output)
    if match is None:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not any(key in data for key in BRIEF_FIELDS):
        return None
    return {
        "problem": _text(data.get("problem")),
        "goal": _text(data.get("goal")),
        "constraints": _lines(data.get("constraints")),
        "out_of_scope": _lines(data.get("out_of_scope")),
    }


def _text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _lines(value: object) -> tuple[str, ...]:
    if isinstance(value, list):
        return clean_lines([str(item) for item in value if isinstance(item, str | int | float)])
    if isinstance(value, str):
        return clean_lines(value.splitlines())
    return ()
