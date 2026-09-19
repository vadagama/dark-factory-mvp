"""Architecture decision cards read from ADR documents (M3, T093, ADR-039).

Pure parsing over the text of ``design/decisions/ADR-NNN-<slug>.md``; nothing
here talks to a repository or to the store. The card is a *read model over
the ADR file in git*: the agent writes the document, the operator sees the
proposal, the rationale, the alternatives it rejected, the consequences and
the impact as one card, and never edits a status by hand —

* sections come from the level-2 headings, in Russian or English
  (``## Решение`` / ``## Decision`` → ``proposal``, ``## Обоснование`` /
  ``## Rationale``, ``## Альтернативы`` / ``## Alternatives``,
  ``## Последствия`` / ``## Consequences``, ``## Влияние`` / ``## Impact``);
* alternatives are read from the table ``| Вариант | Плюсы | Минусы | Почему не
  выбран |`` (the first column is the title, the last one the reason it was
  rejected, the middle ones the summary) or from ``### <вариант>`` subsections;
* ``impact`` is the frontmatter list ``impact:``, falling back to the bullet
  list under ``## Влияние`` / ``## Impact``;
* ``document_status`` is what the agent wrote; the derived ``status``
  (``proposed | accepted | needs_revision | superseded``) is computed by
  ``orchestration.decisions`` from the phase decisions and the rework orders —
  a parser cannot know them and must not guess.

A malformed frontmatter never raises: the card carries the error in ``errors``
and the rest of the document is still parsed, so the Console shows the source
instead of an empty phase (ADR-035 p.6).
"""

import re
from collections.abc import Iterable, Sequence
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from dark_factory.changes.conversations import ReworkOrder
from dark_factory.context.sdd.errors import FrontmatterError
from dark_factory.context.sdd.frontmatter import split_frontmatter

__all__ = [
    "DecisionAlternative",
    "DecisionCard",
    "DecisionStatus",
    "parse_decision_card",
]

type DecisionStatus = Literal["proposed", "accepted", "needs_revision", "superseded"]

_SECTION_KEYS: Final[dict[str, str]] = {
    "решение": "proposal",
    "decision": "proposal",
    "обоснование": "rationale",
    "rationale": "rationale",
    "альтернативы": "alternatives",
    "alternatives": "alternatives",
    "последствия": "consequences",
    "consequences": "consequences",
    "влияние": "impact",
    "impact": "impact",
}

_H2: Final[re.Pattern[str]] = re.compile(r"^##\s+(.+?)\s*#*\s*$", flags=re.MULTILINE)
_H3: Final[re.Pattern[str]] = re.compile(r"^###\s+(.+?)\s*#*\s*$", flags=re.MULTILINE)
_H1: Final[re.Pattern[str]] = re.compile(r"^#\s+(.+?)\s*#*\s*$", flags=re.MULTILINE)
_BULLET: Final[re.Pattern[str]] = re.compile(r"^\s*[-*+]\s+(.+?)\s*$", flags=re.MULTILINE)
_TABLE_SEPARATOR: Final[re.Pattern[str]] = re.compile(
    r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$"
)
_REJECTED_LABEL: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?:\*\*)?(?:"
    r"почему не выбран|отклонено|отклонён|отклонен"
    r"|rejected because|why not"
    r")(?:\*\*)?\s*[:—-]\s*(.+)$",
    flags=re.IGNORECASE | re.MULTILINE,
)


class DecisionAlternative(BaseModel):
    """One option the architect considered and did not choose."""

    model_config = ConfigDict(frozen=True)

    title: str = Field(min_length=1)
    summary: str | None = None
    rejected_because: str | None = None


class DecisionCard(BaseModel):
    """One ADR as the operator sees it in the architecture phase (contract m3 §2).

    ``status`` is the *derived* status; the parser leaves it ``proposed`` and
    the service overrides it. ``pending_alternative`` and
    ``affected_artifacts`` are filled by the service as well — they need the
    rework orders and the revisions. ``errors`` (additive to the contract)
    carries what could not be parsed.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    title: str
    status: DecisionStatus = "proposed"
    document_status: str | None = None
    revision: str | None = None
    proposal: str | None = None
    rationale: str | None = None
    consequences: str | None = None
    alternatives: tuple[DecisionAlternative, ...] = ()
    impact: tuple[str, ...] = ()
    pending_alternative: ReworkOrder | None = None
    affected_artifacts: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


def _stem(path: str) -> str:
    name = path.rstrip("/").rsplit("/", 1)[-1]
    return name.rsplit(".", 1)[0] if "." in name else name


def _clean(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _sections(body: str) -> dict[str, str]:
    """Text under each level-2 heading, keyed by the normalized heading."""
    matches = list(_H2.finditer(body))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        key = _SECTION_KEYS.get(match.group(1).strip().lower())
        if key is not None and key not in sections:
            sections[key] = body[start:end].strip()
    return sections


def _split_row(line: str) -> list[str]:
    cells = line.strip()
    if cells.startswith("|"):
        cells = cells[1:]
    if cells.endswith("|"):
        cells = cells[:-1]
    return [cell.strip() for cell in cells.split("|")]


def _table_alternatives(text: str) -> list[DecisionAlternative]:
    rows = [line for line in text.splitlines() if line.strip().startswith("|")]
    if len(rows) < 2:
        return []
    data = [row for row in rows[1:] if not _TABLE_SEPARATOR.match(row.strip())]
    alternatives: list[DecisionAlternative] = []
    for row in data:
        cells = _split_row(row)
        title = _clean(cells[0]) if cells else None
        if title is None:
            continue
        rejected = _clean(cells[-1]) if len(cells) >= 2 else None
        middle = [cell for cell in (_clean(c) for c in cells[1:-1]) if cell]
        alternatives.append(
            DecisionAlternative(
                title=title, summary="; ".join(middle) or None, rejected_because=rejected
            )
        )
    return alternatives


def _subsection_alternatives(text: str) -> list[DecisionAlternative]:
    matches = list(_H3.finditer(text))
    alternatives: list[DecisionAlternative] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        chunk = text[start:end].strip()
        rejected_match = _REJECTED_LABEL.search(chunk)
        rejected = _clean(rejected_match.group(1)) if rejected_match else None
        summary_text = _REJECTED_LABEL.sub("", chunk).strip() if rejected_match else chunk
        alternatives.append(
            DecisionAlternative(
                title=match.group(1).strip(),
                summary=_clean(summary_text),
                rejected_because=rejected,
            )
        )
    return alternatives


def _alternatives(text: str | None) -> tuple[DecisionAlternative, ...]:
    if not text:
        return ()
    found = _table_alternatives(text) or _subsection_alternatives(text)
    return tuple(found)


def _impact_of(raw: dict[str, Any] | None, section: str | None) -> tuple[str, ...]:
    value = raw.get("impact") if raw else None
    if isinstance(value, str):
        items: Iterable[Any] = value.split(",")
    elif isinstance(value, Sequence) and not isinstance(value, str | bytes):
        items = value
    else:
        items = ()
    listed = [item for item in (_clean(v) for v in items) if item]
    if listed:
        return tuple(dict.fromkeys(listed))
    if section:
        bullets = [item for item in (_clean(m) for m in _BULLET.findall(section)) if item]
        return tuple(dict.fromkeys(bullets))
    return ()


def parse_decision_card(path: str, content: str, *, revision: str | None = None) -> DecisionCard:
    """Read one ADR document into a card; never raises on a malformed document."""
    errors: list[str] = []
    raw: dict[str, Any] | None
    try:
        raw, body = split_frontmatter(content)
    except FrontmatterError as error:
        errors.append(f"frontmatter: {error}")
        raw, body = None, content
    sections = _sections(body)
    stem = _stem(path)
    identifier = _clean(raw.get("id")) if raw else None
    title = _clean(raw.get("title")) if raw else None
    if title is None:
        heading = _H1.search(body)
        title = heading.group(1).strip() if heading else stem
    document_status = _clean(raw.get("status")) if raw else None
    if raw and raw.get("type") not in (None, "adr"):
        errors.append(f"type: expected 'adr', found {raw.get('type')!r}")
    return DecisionCard(
        id=identifier or stem,
        path=path,
        title=title,
        document_status=document_status,
        revision=revision,
        proposal=_clean(sections.get("proposal")),
        rationale=_clean(sections.get("rationale")),
        consequences=_clean(sections.get("consequences")),
        alternatives=_alternatives(sections.get("alternatives")),
        impact=_impact_of(raw, sections.get("impact")),
        errors=tuple(errors),
    )
