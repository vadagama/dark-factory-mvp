"""Design-side facts read from the ChangeSet documents (M3, T097, ADR-039).

Pure functions over document text; nothing here talks to a repository. The
one fact the phase gate of the interface phase needs from the architecture
round is whether the change has a user interface at all: the architect states
it in the frontmatter of ``design/overview.md`` —

.. code-block:: yaml

    ui: required | not_required
    ui_reason: <the stated ground, mandatory for not_required>

The value is the agent's *proposal* (ADR-018: a human gate is never closed by
an agent). The operator confirms the skip with a ``waived`` decision of the
interface phase, so the gate reads "not required: <reason>" with its source
and offers exactly that action (ADR-032 p.5, ADR-033 p.4).
"""

from typing import Final, Literal

from pydantic import BaseModel, ConfigDict

from dark_factory.context.sdd.errors import FrontmatterError
from dark_factory.context.sdd.frontmatter import split_frontmatter

__all__ = ["DESIGN_OVERVIEW_PATH", "UiRequirement", "UiRequirementSource", "ui_requirement_of"]

DESIGN_OVERVIEW_PATH: Final[str] = "design/overview.md"
"""Path of the architecture overview inside the ChangeSet directory (changeset.md)."""

type UiRequirementSource = Literal["route", "agent", "operator", "default"]


class UiRequirement(BaseModel):
    """Whether the interface phase applies to the change, and who says so (T097).

    ``route`` — the route requires no ``ui`` gate at all (``quick``); ``agent``
    — the architect proposed ``ui: not_required`` (or ``required``) in the
    design overview; ``operator`` — a ``waived`` decision of the interface
    phase is recorded; ``default`` — nothing was stated, the phase applies.
    """

    model_config = ConfigDict(frozen=True)

    required: bool
    source: UiRequirementSource
    reason: str | None = None


def ui_requirement_of(overview: str | None) -> UiRequirement | None:
    """The architect's statement about the UI in ``design/overview.md``, if any.

    ``None`` when the overview is absent, has no frontmatter, has no ``ui`` key
    or is malformed — the caller falls back to the default (the phase applies);
    a broken document never silently waives a human gate.
    """
    if overview is None:
        return None
    try:
        raw, _body = split_frontmatter(overview)
    except FrontmatterError:
        return None
    if not raw:
        return None
    value = raw.get("ui")
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    reason = raw.get("ui_reason")
    reason_text = str(reason).strip() if reason is not None and str(reason).strip() else None
    if normalized in {"not_required", "none", "no", "false"}:
        return UiRequirement(required=False, source="agent", reason=reason_text)
    if normalized in {"required", "yes", "true"}:
        return UiRequirement(required=True, source="agent", reason=reason_text)
    return None
