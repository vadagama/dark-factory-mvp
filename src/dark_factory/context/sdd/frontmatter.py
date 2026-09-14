"""YAML frontmatter of OKF nodes: parse, validate, render (ADR-020 §7-8).

Rule: every self-addressable SDD artifact carries the minimal frontmatter
(``schema``, ``id``, ``type``, ``title``, ``product``, ``status``, ``change``);
generated views and auxiliary docs may omit it.
"""

from typing import Any, Final

import yaml

from dark_factory.context.sdd.errors import FrontmatterError
from dark_factory.context.sdd.models import Frontmatter

_DELIMITER: Final = "---"

MINIMAL_FRONTMATTER_FIELDS: Final[tuple[str, ...]] = (
    "schema",
    "id",
    "type",
    "title",
    "product",
    "status",
    "change",
)


def split_frontmatter(text: str) -> tuple[dict[str, Any] | None, str]:
    """Split a markdown document into (raw frontmatter mapping | None, body).

    A document without a leading ``---`` block has no frontmatter (allowed for
    generated views); a ``---`` block that is not valid YAML mapping is an
    error.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != _DELIMITER:
        return None, text
    end = None
    for index in range(1, len(lines)):
        if lines[index].strip() == _DELIMITER:
            end = index
            break
    if end is None:
        raise FrontmatterError("unterminated frontmatter block")
    raw = yaml.safe_load("\n".join(lines[1:end]))
    if raw is None:
        return {}, "\n".join(lines[end + 1 :])
    if not isinstance(raw, dict):
        raise FrontmatterError("frontmatter must be a YAML mapping")
    return raw, "\n".join(lines[end + 1 :])


def validate_frontmatter(data: dict[str, Any]) -> Frontmatter:
    """Validate raw frontmatter against the minimal field set (§8)."""
    missing = [name for name in MINIMAL_FRONTMATTER_FIELDS if data.get(name) in (None, "")]
    if missing:
        raise FrontmatterError(f"frontmatter is missing required fields: {', '.join(missing)}")
    return Frontmatter.model_validate(data)


def parse_frontmatter(text: str) -> Frontmatter | None:
    """Parse frontmatter of a markdown document; ``None`` when it has none."""
    raw, _body = split_frontmatter(text)
    if raw is None:
        return None
    return validate_frontmatter(raw)


def render_document(frontmatter: Frontmatter, body: str) -> str:
    """Render frontmatter + body into canonical markdown (frontmatter first)."""
    payload = frontmatter.model_dump(mode="json", exclude_none=True)
    block = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True).strip()
    return f"{_DELIMITER}\n{block}\n{_DELIMITER}\n\n{body.strip()}\n"


def with_status(frontmatter: Frontmatter, status: str) -> Frontmatter:
    """Copy of ``frontmatter`` with a new status (extras preserved)."""
    return frontmatter.model_copy(update={"status": status})
