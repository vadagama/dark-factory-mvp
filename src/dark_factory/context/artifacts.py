"""Document artifacts of a ChangeSet as a read model over git (T082/T083, ADR-035).

Pure functions and value types: nothing here talks to a repository. The
service that reads the tree, the revisions and the contents through
``RepositoryPort`` is ``orchestration.artifacts``; this module holds what can
be decided from a path and a text alone —

* the **kind** of an artifact from its path inside the ChangeSet directory
  (``spec | design | adr | ui | plan | other``, ADR-035 p.2);
* the **anchors** a document exposes: the frontmatter ``id``, stable element
  ids (``REQ-001``, ``AC-2``, ``ADR-003``, ...) and heading slugs — the ids a
  comment or a question binds to (ADR-034 p.1, ADR-020 stable ids);
* the **properties** of a document: its YAML frontmatter as data, with the
  system identifiers protected from accidental edits (ADR-035 p.5);
* the **diff** between two revisions of a text (ADR-035 p.2/p.8).

Frontmatter parsing reuses ``context.sdd.frontmatter``; unknown keys and the
body are carried verbatim (ADR-035 p.6).
"""

import difflib
import re
from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Any, Final

import yaml
from pydantic import BaseModel, ConfigDict, Field

from dark_factory.context.sdd.errors import FrontmatterError
from dark_factory.context.sdd.frontmatter import split_frontmatter

__all__ = [
    "CHANGES_ROOT",
    "PROTECTED_PROPERTIES",
    "ArtifactDiff",
    "ArtifactDocument",
    "ArtifactKind",
    "ArtifactNode",
    "ArtifactRevision",
    "ArtifactTree",
    "DocumentProperties",
    "ProtectedPropertyError",
    "anchors_of",
    "apply_properties",
    "classify_path",
    "document_properties",
    "heading_slug",
    "is_changeset_artifact",
    "resolve_anchor",
    "unified_diff",
]

CHANGES_ROOT: Final[str] = ".factory/changes"
"""Root of every ChangeSet directory in the product repository (ADR-020, changeset.md)."""

PROTECTED_PROPERTIES: Final[tuple[str, ...]] = ("schema", "id", "type", "product", "change")
"""Frontmatter keys the editor must not change (ADR-035 p.5): the system identifiers."""

_TEXT_SUFFIXES: Final[frozenset[str]] = frozenset({".md", ".yaml", ".yml", ".json", ".txt"})


class ArtifactKind(StrEnum):
    """Operator-facing kind of a ChangeSet artifact (ADR-035 p.2)."""

    SPEC = "spec"
    DESIGN = "design"
    ADR = "adr"
    UI = "ui"
    PLAN = "plan"
    OTHER = "other"


class ProtectedPropertyError(ValueError):
    """An edit tried to change a protected frontmatter key (ADR-035 p.5)."""


def is_changeset_artifact(path: str) -> bool:
    """Whether ``path`` is a text artifact inside the ChangeSet root."""
    normalized = path.strip("/")
    if not normalized.startswith(f"{CHANGES_ROOT}/"):
        return False
    suffix = normalized.rsplit(".", 1)
    return len(suffix) == 2 and f".{suffix[1].lower()}" in _TEXT_SUFFIXES


def classify_path(path: str) -> ArtifactKind:
    """Kind of an artifact from its path relative to the repository root.

    The ChangeSet layout (changeset.md, ``packs/product-baseline/changeset``)
    puts requirements under ``spec/``, ADRs under ``design/decisions/`` (or a
    top-level ``decisions/``), the design overview under ``design/``, UI specs
    under ``ui/`` and the task graph under ``tasks/``. ``intent.md`` and
    ``change.yaml`` belong to the spec side. Anything else is ``other`` —
    never guessed into a phase.
    """
    normalized = path.strip("/")
    parts = normalized.split("/")
    if normalized.startswith(f"{CHANGES_ROOT}/") and len(parts) >= 5:
        # .factory/changes/<year>/<CHG-...>/<segment>/...
        parts = parts[4:]
    if not parts:
        return ArtifactKind.OTHER
    head = parts[0].lower()
    name = parts[-1]
    if re.match(r"^ADR-\d+", name, flags=re.IGNORECASE):
        return ArtifactKind.ADR
    if head in {"spec", "requirements", "scenarios", "capabilities"} or name.lower() in {
        "intent.md",
        "change.yaml",
    }:
        return ArtifactKind.SPEC
    if head == "decisions" or (head == "design" and len(parts) > 1 and parts[1] == "decisions"):
        return ArtifactKind.ADR
    if head in {"ui", "screens"} or (
        head == "design" and len(parts) > 1 and parts[1].lower() in {"ui", "screens"}
    ):
        # ``design/ui/scenarios/SCN-*.md`` and ``design/ui/screens/SCR-*.md`` are the
        # interface phase (M3, ADR-039), not the architecture overview.
        return ArtifactKind.UI
    if head == "design":
        return ArtifactKind.DESIGN
    if head in {"tasks", "plan", "verification"}:
        return ArtifactKind.PLAN
    return ArtifactKind.OTHER


class ArtifactNode(BaseModel):
    """One artifact of the tree: its path, kind and the revision it was listed at."""

    model_config = ConfigDict(frozen=True)

    path: str = Field(min_length=1)
    kind: ArtifactKind
    revision: str | None = None
    """Head of the change branch the tree was read at (the artifact's current revision)."""


class ArtifactTree(BaseModel):
    """Read model of the ChangeSet artifacts of a change (ADR-035 p.2).

    ``revision`` is the branch head the tree was listed at; ``None`` with an
    empty ``nodes`` means the change has no branch yet — distinguishable from
    a branch without artifacts (``revision`` set, ``nodes`` empty).
    """

    model_config = ConfigDict(frozen=True)

    change_id: str = Field(min_length=1)
    branch: str = Field(min_length=1)
    revision: str | None = None
    nodes: tuple[ArtifactNode, ...] = ()

    @property
    def exists(self) -> bool:
        return self.revision is not None


class DocumentProperties(BaseModel):
    """Frontmatter as data (ADR-035 p.5): the values and which keys are protected."""

    model_config = ConfigDict(frozen=True)

    values: dict[str, Any] = Field(default_factory=dict)
    protected: tuple[str, ...] = PROTECTED_PROPERTIES

    @property
    def editable(self) -> dict[str, Any]:
        return {key: value for key, value in self.values.items() if key not in self.protected}


class ArtifactDocument(BaseModel):
    """One artifact at one revision: content, parsed properties, body and anchors."""

    model_config = ConfigDict(frozen=True)

    path: str = Field(min_length=1)
    kind: ArtifactKind
    revision: str = Field(min_length=1)
    """The revision the content was read at — a commit SHA, never ``latest``."""
    content: str
    properties: DocumentProperties | None = None
    body: str = ""
    anchors: tuple[str, ...] = ()
    frontmatter_error: str | None = None
    """Set when the document has a ``---`` block that is not a YAML mapping."""


class ArtifactRevision(BaseModel):
    """One commit that touched an artifact (ADR-035 p.1: revision = commit)."""

    model_config = ConfigDict(frozen=True)

    revision: str = Field(min_length=1)
    message: str = ""
    author: str | None = None
    authored_at: datetime | None = None


class ArtifactDiff(BaseModel):
    """Unified diff of one artifact between two revisions."""

    model_config = ConfigDict(frozen=True)

    path: str = Field(min_length=1)
    from_revision: str = Field(min_length=1)
    to_revision: str = Field(min_length=1)
    unified: str
    added: int = 0
    removed: int = 0

    @property
    def is_empty(self) -> bool:
        return not self.unified


# --- anchors ------------------------------------------------------------------


_STABLE_ID: Final[re.Pattern[str]] = re.compile(
    r"\b(?:"
    r"(?:REQ|AC|SCN|SCR|CAP|ADR|TASK|EVD|FR|NFR|US|Q)-[0-9]+(?:[.-][A-Za-z0-9]+)*"
    r"|EL-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*"
    r"|S[0-9]+"
    r")\b"
)
"""Stable element ids the ChangeSet layout uses (ADR-020 stable ids; packs REQ-/AC-/ADR-).

Since M3 (ADR-039) the UI spec adds screen ids (``SCR-001``), element ids
(``EL-display``, ``EL-btn-percent``) and scenario steps (``S1``, ``S12``, whole
words only) — the anchors a comment on a screen element binds to.
"""

_HEADING: Final[re.Pattern[str]] = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$", flags=re.MULTILINE)
_EXPLICIT_ANCHOR: Final[re.Pattern[str]] = re.compile(r"\{#([A-Za-z0-9_.:-]+)\}")
_HTML_ANCHOR: Final[re.Pattern[str]] = re.compile(r'<a\s+(?:id|name)="([^"]+)"')
_FENCE: Final[re.Pattern[str]] = re.compile(r"```.*?```", flags=re.DOTALL)


def heading_slug(text: str) -> str:
    """GitHub-style heading slug: lowercase, punctuation dropped, spaces to hyphens.

    Explicit ``{#id}`` markers are removed first — they are anchors of their
    own. Unicode letters are kept (Russian headings must stay addressable).
    """
    cleaned = _EXPLICIT_ANCHOR.sub("", text).strip().lower()
    cleaned = re.sub(r"[^\w\s-]", "", cleaned, flags=re.UNICODE)
    cleaned = re.sub(r"\s+", "-", cleaned).strip("-")
    return cleaned


def anchors_of(content: str) -> tuple[str, ...]:
    """Every id a comment or a question may bind to in ``content``, in document order.

    The frontmatter ``id`` (when present), stable element ids anywhere in the
    body, explicit ``{#id}`` / ``<a id>`` anchors and heading slugs; fenced
    code is skipped so an example id in a code block does not become an
    anchor. Duplicates keep their first position.
    """
    found: list[str] = []
    try:
        raw, body = split_frontmatter(content)
    except FrontmatterError:
        raw, body = None, content
    if raw is not None:
        identifier = raw.get("id")
        if isinstance(identifier, str) and identifier.strip():
            found.append(identifier.strip())
        # UI specs declare their elements and steps in the frontmatter (M3): the
        # ``EL-*`` / ``S<n>`` / ``SCR-*`` ids there are anchors too.
        found.extend(_STABLE_ID.findall(_frontmatter_block(content)))
    text = _FENCE.sub("", body)
    for match in _HEADING.finditer(text):
        found.extend(_EXPLICIT_ANCHOR.findall(match.group(2)))
        slug = heading_slug(match.group(2))
        if slug:
            found.append(slug)
    found.extend(_STABLE_ID.findall(text))
    found.extend(_EXPLICIT_ANCHOR.findall(text))
    found.extend(_HTML_ANCHOR.findall(text))
    seen: set[str] = set()
    ordered: list[str] = []
    for anchor in found:
        if anchor not in seen:
            seen.add(anchor)
            ordered.append(anchor)
    return tuple(ordered)


def _frontmatter_block(content: str) -> str:
    """The raw text between the ``---`` delimiters, or ``""`` without a block."""
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return ""
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "\n".join(lines[1:index])
    return ""


def resolve_anchor(content: str | None, anchor_id: str | None) -> bool:
    """Whether ``anchor_id`` still names an element of ``content``.

    ``None`` content is an absent artifact — nothing resolves there. A ``None``
    anchor id addresses the whole document and resolves whenever the document
    exists (ADR-034 p.1: only a *lost* fragment is detached).
    """
    if content is None:
        return False
    if anchor_id is None:
        return True
    return anchor_id in anchors_of(content)


# --- properties ---------------------------------------------------------------


def document_properties(content: str) -> tuple[DocumentProperties | None, str, str | None]:
    """``(properties, body, error)`` of a markdown document.

    A document without frontmatter has ``None`` properties and the whole text
    as body; a broken ``---`` block yields ``None`` properties, the *whole*
    text as body (nothing is dropped) and the error text — the Console shows
    the source (ADR-035 p.6).
    """
    try:
        raw, body = split_frontmatter(content)
    except FrontmatterError as error:
        return None, content, str(error)
    if raw is None:
        return None, body, None
    return DocumentProperties(values=dict(raw)), body, None


def apply_properties(
    content: str, updates: Mapping[str, Any], *, protected: tuple[str, ...] = PROTECTED_PROPERTIES
) -> str:
    """Return ``content`` with the frontmatter values in ``updates`` replaced.

    Only editable keys may change: an update that would alter a protected key
    raises :class:`ProtectedPropertyError` (ADR-035 p.5) — the same value
    re-submitted is fine. Keys keep their order, unknown keys are preserved
    verbatim, ``None`` removes an editable key, and a document without
    frontmatter gets one from the updates. The body is untouched.
    """
    raw, body = split_frontmatter(content)
    if content.endswith("\n") and not body.endswith("\n"):
        # ``split_frontmatter`` joins lines and drops the final newline; the
        # round-trip must not change the body (ADR-035 p.6).
        body += "\n"
    values: dict[str, Any] = dict(raw or {})
    for key, value in updates.items():
        if key in protected and values.get(key) != value:
            raise ProtectedPropertyError(
                f"property {key!r} is a system identifier and cannot be changed here"
            )
        if value is None:
            values.pop(key, None)
        else:
            values[key] = value
    if not values:
        return body
    block = yaml.safe_dump(values, sort_keys=False, allow_unicode=True).strip()
    separator = "" if body.startswith("\n") or not body else "\n"
    return f"---\n{block}\n---\n{separator}{body}"


# --- diff ---------------------------------------------------------------------


def unified_diff(
    before: str, after: str, *, path: str, from_revision: str, to_revision: str
) -> ArtifactDiff:
    """Unified diff between two texts of the same artifact (ADR-035 p.2)."""
    lines = list(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"{path}@{from_revision}",
            tofile=f"{path}@{to_revision}",
        )
    )
    added = sum(1 for line in lines if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in lines if line.startswith("-") and not line.startswith("---"))
    return ArtifactDiff(
        path=path,
        from_revision=from_revision,
        to_revision=to_revision,
        unified="".join(lines),
        added=added,
        removed=removed,
    )
