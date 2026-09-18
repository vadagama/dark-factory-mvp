"""Markdown body of a change request, rendered from an editable template (T-094).

The body a change request is opened with is not a string in code: it is rendered
from the Markdown template packaged next to this module
(``templates/pr-description.md``). The template is data — editing the ``.md``
file reshapes what every factory-opened pull request says, without touching the
executor. Placeholders are written ``{{name}}`` and are substituted from the
stage context plus the publish arguments (branch, target, commit).

Construction is fail-closed: a template that references an unknown placeholder,
or a template file that cannot be read, raises :class:`PrTemplateError` — a
``ValueError`` — when the renderer is built (composition time), never mid-publish.
``DARK_FACTORY_PR_TEMPLATE`` overrides the packaged default with a file of the
operator's choosing. The provider adapter appends the change-id marker to the
body itself (``change_id_from_body`` reads the first marker), so the rendered
description stays marker-free.
"""

import os
import re
from collections.abc import Mapping
from importlib.resources import files
from pathlib import Path
from typing import Final

from dark_factory.changes.enums import Role
from dark_factory.orchestration.stages.context import StageContext

PR_TEMPLATE_ENV_VAR: Final[str] = "DARK_FACTORY_PR_TEMPLATE"
"""Environment variable overriding the packaged change-request description template."""

KNOWN_PLACEHOLDERS: Final[frozenset[str]] = frozenset(
    {
        "attempt",
        "change_id",
        "commit_sha",
        "description",
        "external_ref",
        "repository",
        "risk_class",
        "role",
        "run_id",
        "source_branch",
        "stage",
        "target_branch",
        "title",
    }
)
"""Names a template may reference; anything else fails construction (fail-closed)."""

_PLACEHOLDER: Final = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")

_EMPTY_DESCRIPTION: Final[str] = "_The tracker did not provide a description._"
"""Substituted for ``{{description}}`` when the tracker carried no task text."""

_EMPTY_EXTERNAL_REF: Final[str] = "n/a"
"""Substituted for ``{{external_ref}}`` when the change has no tracker reference."""


class PrTemplateError(ValueError):
    """A description template is broken: an unknown placeholder or an unreadable file."""


class PrDescriptionRenderer:
    """Renders the Markdown body of a change request from its template.

    The template is validated once — every ``{{name}}`` it references must be a
    known placeholder — and ``render`` substitutes a total value mapping, so a
    well-formed renderer cannot fail at publish time and two calls with the same
    context produce the same body.
    """

    def __init__(self, template: str) -> None:
        unknown = sorted(set(_PLACEHOLDER.findall(template)) - KNOWN_PLACEHOLDERS)
        if unknown:
            raise PrTemplateError(
                "the change-request description template references unknown"
                f" placeholder(s): {', '.join(unknown)}"
                f" (known: {', '.join(sorted(KNOWN_PLACEHOLDERS))})"
            )
        self._template = template

    @classmethod
    def default(cls) -> "PrDescriptionRenderer":
        """The renderer over the packaged template (``templates/pr-description.md``)."""
        resource = files("dark_factory.orchestration.stages") / "templates" / "pr-description.md"
        try:
            return cls(resource.read_text(encoding="utf-8"))
        except OSError as exc:
            raise PrTemplateError(
                f"the packaged change-request description template is unreadable: {exc}"
            ) from exc

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "PrDescriptionRenderer":
        """The renderer over the template ``DARK_FACTORY_PR_TEMPLATE`` points at.

        A missing or blank variable selects the packaged default; a set variable
        names a template file on disk, and a file that cannot be read fails
        closed with :class:`PrTemplateError` naming the variable and the path —
        a set-but-broken override is a misconfiguration, not an absence.
        """
        source = os.environ if env is None else env
        path = (source.get(PR_TEMPLATE_ENV_VAR) or "").strip()
        if not path:
            return cls.default()
        try:
            return cls(Path(path).read_text(encoding="utf-8"))
        except OSError as exc:
            raise PrTemplateError(
                f"{PR_TEMPLATE_ENV_VAR} points to an unreadable template file: {path} ({exc})"
            ) from exc

    def render(
        self,
        context: StageContext,
        *,
        role: Role,
        source_branch: str,
        target_branch: str,
        commit_sha: str,
    ) -> str:
        """The change-request body of this stage attempt, every placeholder filled.

        The value mapping is total — every known placeholder has a value — and
        the substitution goes through a replacement function, so tracker texts
        holding backslashes are inserted literally (no ``re`` escape expansion).
        """
        change = context.change
        values: dict[str, str] = {
            "change_id": change.id,
            "title": change.title,
            "description": change.description or _EMPTY_DESCRIPTION,
            "stage": context.stage.value,
            "role": role.value,
            "attempt": str(context.attempt_number),
            "repository": change.product.slug,
            "source_branch": source_branch,
            "target_branch": target_branch,
            "commit_sha": commit_sha,
            "risk_class": change.risk_class.value,
            "external_ref": change.external_ref or _EMPTY_EXTERNAL_REF,
            "run_id": context.run_id,
        }
        return _PLACEHOLDER.sub(lambda match: values[match.group(1)], self._template)
