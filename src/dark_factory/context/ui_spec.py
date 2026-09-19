"""UI specification of a change read from markdown nodes (M3, T094, ADR-039).

Pure parsing over ``design/ui/scenarios/SCN-*.md`` and
``design/ui/screens/SCR-*.md`` — one markdown document with YAML frontmatter
per node — into the shape ``GET /changes/{id}/ui`` serves (contract m3 §2):

* a **scenario** has ordered steps (``S1``, ``S2``, ...) that may point at a
  screen; ``screens`` lists the distinct screens the steps visit;
* a **screen** has a route, an optional preview URL, its five states
  (``loading | empty | error | success | access``), its elements (``EL-*``,
  the anchors of element comments) and the UIKit components they use;
* **links** are derived from the screens' ``transitions`` (id ``from->to``,
  with ``#n`` when the same pair repeats);
* **components** aggregate ``element.component`` over the screens.

A state that is not declared is simply absent from ``states`` — the Console
shows the gap rather than a green default. ``preview_url``: an absolute URL is
kept as is, a relative one is joined with ``dev_url`` when the product declares
one, otherwise it stays relative — a host is never invented. Malformed nodes
are reported in ``errors`` with their path and skipped; nothing raises.
"""

import re
from collections.abc import Mapping, Sequence
from typing import Any, Final, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

from dark_factory.context.sdd.errors import FrontmatterError
from dark_factory.context.sdd.frontmatter import split_frontmatter

__all__ = [
    "STATE_KINDS",
    "UiComponentUse",
    "UiElement",
    "UiLink",
    "UiScenario",
    "UiScreen",
    "UiSpec",
    "UiState",
    "UiStateKind",
    "UiStep",
    "build_ui_spec",
    "parse_ui_scenario",
    "parse_ui_screen",
    "resolve_preview_url",
]

type UiStateKind = Literal["loading", "empty", "error", "success", "access"]

STATE_KINDS: Final[tuple[str, ...]] = get_args(UiStateKind.__value__)
"""The five states every screen declares (contract m3 §1), in display order."""

_ABSOLUTE_URL: Final[re.Pattern[str]] = re.compile(r"^https?://", flags=re.IGNORECASE)
_HEADING: Final[re.Pattern[str]] = re.compile(r"^#{1,6}\s+.*$", flags=re.MULTILINE)
_FENCE: Final[re.Pattern[str]] = re.compile(r"```.*?```", flags=re.DOTALL)


class UiStep(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    text: str
    screen: str | None = None


class UiScenario(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    title: str
    summary: str | None = None
    steps: tuple[UiStep, ...] = ()
    screens: tuple[str, ...] = ()


class UiState(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: UiStateKind
    description: str | None = None


class UiElement(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    kind: str | None = None
    label: str | None = None
    component: str | None = None


class UiScreen(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    title: str
    purpose: str | None = None
    route: str | None = None
    preview_url: str | None = None
    states: tuple[UiState, ...] = ()
    elements: tuple[UiElement, ...] = ()
    components: tuple[str, ...] = ()


class UiLink(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    from_screen: str
    to_screen: str
    trigger: str | None = None
    condition: str | None = None


class UiComponentUse(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    screens: tuple[str, ...] = ()


class UiSpec(BaseModel):
    """Scenarios, screens, links and components of a change (the pure part of UiSpecView)."""

    model_config = ConfigDict(frozen=True)

    scenarios: tuple[UiScenario, ...] = ()
    screens: tuple[UiScreen, ...] = ()
    links: tuple[UiLink, ...] = ()
    components: tuple[UiComponentUse, ...] = ()
    errors: tuple[str, ...] = ()


class _Transition(BaseModel):
    model_config = ConfigDict(frozen=True)

    from_screen: str
    to_screen: str
    trigger: str | None = None
    condition: str | None = None


def _clean(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _stem(path: str) -> str:
    name = path.rstrip("/").rsplit("/", 1)[-1]
    return name.rsplit(".", 1)[0] if "." in name else name


def _first_paragraph(body: str) -> str | None:
    text = _HEADING.sub("", _FENCE.sub("", body))
    for chunk in re.split(r"\n\s*\n", text):
        lines = [line.strip() for line in chunk.splitlines() if line.strip()]
        if lines:
            return " ".join(lines)
    return None


def resolve_preview_url(value: object, dev_url: str | None) -> str | None:
    """Absolute as is; relative joined with ``dev_url`` when known; else as written."""
    text = _clean(value)
    if text is None:
        return None
    if _ABSOLUTE_URL.match(text):
        return text
    if dev_url and dev_url.strip():
        return f"{dev_url.strip().rstrip('/')}/{text.lstrip('/')}"
    return text


def _frontmatter(path: str, content: str, errors: list[str]) -> tuple[dict[str, Any], str] | None:
    try:
        raw, body = split_frontmatter(content)
    except FrontmatterError as error:
        errors.append(f"{path}: frontmatter: {error}")
        return None
    if raw is None:
        errors.append(f"{path}: no frontmatter — the node has no id")
        return None
    return raw, body


def _identity(path: str, raw: Mapping[str, Any], errors: list[str]) -> tuple[str, str] | None:
    identifier = _clean(raw.get("id"))
    if identifier is None:
        errors.append(f"{path}: frontmatter has no id")
        return None
    title = _clean(raw.get("title")) or _stem(path)
    return identifier, title


def parse_ui_scenario(path: str, content: str, *, errors: list[str]) -> UiScenario | None:
    """One ``SCN-*`` node; ``None`` (with an error appended) when it has no usable frontmatter."""
    parsed = _frontmatter(path, content, errors)
    if parsed is None:
        return None
    raw, body = parsed
    identity = _identity(path, raw, errors)
    if identity is None:
        return None
    identifier, title = identity
    steps: list[UiStep] = []
    raw_steps = raw.get("steps") or ()
    if not isinstance(raw_steps, Sequence) or isinstance(raw_steps, str | bytes):
        errors.append(f"{path}: steps must be a list")
        raw_steps = ()
    for index, item in enumerate(raw_steps, start=1):
        if isinstance(item, Mapping):
            text = _clean(item.get("text"))
            if text is None:
                errors.append(f"{path}: step {index} has no text")
                continue
            steps.append(
                UiStep(
                    id=_clean(item.get("id")) or f"S{index}",
                    text=text,
                    screen=_clean(item.get("screen")),
                )
            )
        elif isinstance(item, str) and item.strip():
            steps.append(UiStep(id=f"S{index}", text=item.strip()))
        else:
            errors.append(f"{path}: step {index} is not a mapping")
    screens = tuple(dict.fromkeys(step.screen for step in steps if step.screen is not None))
    return UiScenario(
        id=identifier,
        path=path,
        title=title,
        summary=_first_paragraph(body),
        steps=tuple(steps),
        screens=screens,
    )


def _states(path: str, raw: Mapping[str, Any], errors: list[str]) -> tuple[UiState, ...]:
    declared = raw.get("states")
    pairs: list[tuple[str, object]] = []
    if isinstance(declared, Mapping):
        pairs = [(str(kind), description) for kind, description in declared.items()]
    elif isinstance(declared, Sequence) and not isinstance(declared, str | bytes):
        for item in declared:
            if isinstance(item, Mapping) and item.get("kind") is not None:
                pairs.append((str(item["kind"]), item.get("description")))
            else:
                errors.append(f"{path}: state entry {item!r} needs a kind")
    elif declared is not None:
        errors.append(f"{path}: states must be a mapping kind -> description")
    states: list[UiState] = []
    seen: set[str] = set()
    for kind, description in pairs:
        normalized = kind.strip().lower()
        if normalized not in STATE_KINDS:
            errors.append(f"{path}: unknown state kind {kind!r}")
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        states.append(UiState(kind=normalized, description=_clean(description)))
    states.sort(key=lambda state: STATE_KINDS.index(state.kind))
    return tuple(states)


def _elements(path: str, raw: Mapping[str, Any], errors: list[str]) -> tuple[UiElement, ...]:
    declared = raw.get("elements") or ()
    if not isinstance(declared, Sequence) or isinstance(declared, str | bytes):
        errors.append(f"{path}: elements must be a list")
        return ()
    elements: list[UiElement] = []
    for index, item in enumerate(declared, start=1):
        if not isinstance(item, Mapping):
            errors.append(f"{path}: element {index} is not a mapping")
            continue
        identifier = _clean(item.get("id"))
        if identifier is None:
            errors.append(f"{path}: element {index} has no id")
            continue
        elements.append(
            UiElement(
                id=identifier,
                kind=_clean(item.get("kind")),
                label=_clean(item.get("label")),
                component=_clean(item.get("component")),
            )
        )
    return tuple(elements)


def _transitions(
    path: str, screen_id: str, raw: Mapping[str, Any], errors: list[str]
) -> list[_Transition]:
    declared = raw.get("transitions") or ()
    if not isinstance(declared, Sequence) or isinstance(declared, str | bytes):
        errors.append(f"{path}: transitions must be a list")
        return []
    transitions: list[_Transition] = []
    for index, item in enumerate(declared, start=1):
        if not isinstance(item, Mapping):
            errors.append(f"{path}: transition {index} is not a mapping")
            continue
        target = _clean(item.get("to"))
        if target is None:
            errors.append(f"{path}: transition {index} has no target screen (to)")
            continue
        transitions.append(
            _Transition(
                from_screen=screen_id,
                to_screen=target,
                trigger=_clean(item.get("trigger")),
                condition=_clean(item.get("condition")),
            )
        )
    return transitions


def parse_ui_screen(
    path: str, content: str, *, dev_url: str | None = None, errors: list[str]
) -> tuple[UiScreen, list[_Transition]] | None:
    """One ``SCR-*`` node and its outgoing transitions; ``None`` without usable frontmatter."""
    parsed = _frontmatter(path, content, errors)
    if parsed is None:
        return None
    raw, body = parsed
    identity = _identity(path, raw, errors)
    if identity is None:
        return None
    identifier, title = identity
    elements = _elements(path, raw, errors)
    screen = UiScreen(
        id=identifier,
        path=path,
        title=title,
        purpose=_first_paragraph(body),
        route=_clean(raw.get("route")),
        preview_url=resolve_preview_url(raw.get("preview_url"), dev_url),
        states=_states(path, raw, errors),
        elements=elements,
        components=tuple(dict.fromkeys(e.component for e in elements if e.component is not None)),
    )
    return screen, _transitions(path, identifier, raw, errors)


def _node_type(path: str, content: str) -> str | None:
    try:
        raw, _ = split_frontmatter(content)
    except FrontmatterError:
        raw = None
    declared = _clean(raw.get("type")) if raw else None
    if declared in {"ui_scenario", "ui_screen"}:
        return declared
    name = _stem(path).upper()
    if name.startswith("SCN-") or "/scenarios/" in path:
        return "ui_scenario"
    if name.startswith("SCR-") or "/screens/" in path:
        return "ui_screen"
    return None


def build_ui_spec(documents: Mapping[str, str | None], *, dev_url: str | None = None) -> UiSpec:
    """The UI spec of every node in ``documents`` (path → text; ``None`` = absent)."""
    errors: list[str] = []
    scenarios: list[UiScenario] = []
    screens: list[UiScreen] = []
    transitions: list[_Transition] = []
    for path in sorted(documents):
        content = documents[path]
        if content is None:
            errors.append(f"{path}: the document is absent at this revision")
            continue
        match _node_type(path, content):
            case "ui_scenario":
                scenario = parse_ui_scenario(path, content, errors=errors)
                if scenario is not None:
                    scenarios.append(scenario)
            case "ui_screen":
                parsed = parse_ui_screen(path, content, dev_url=dev_url, errors=errors)
                if parsed is not None:
                    screen, outgoing = parsed
                    screens.append(screen)
                    transitions.extend(outgoing)
            case _:
                errors.append(f"{path}: not a UI scenario or screen (type / SCN- / SCR-)")
    links: list[UiLink] = []
    counts: dict[str, int] = {}
    for transition in transitions:
        base = f"{transition.from_screen}->{transition.to_screen}"
        counts[base] = counts.get(base, 0) + 1
        link_id = base if counts[base] == 1 else f"{base}#{counts[base]}"
        links.append(
            UiLink(
                id=link_id,
                from_screen=transition.from_screen,
                to_screen=transition.to_screen,
                trigger=transition.trigger,
                condition=transition.condition,
            )
        )
    uses: dict[str, list[str]] = {}
    for screen in screens:
        for component in screen.components:
            used = uses.setdefault(component, [])
            if screen.id not in used:
                used.append(screen.id)
    return UiSpec(
        scenarios=tuple(scenarios),
        screens=tuple(screens),
        links=tuple(links),
        components=tuple(
            UiComponentUse(name=name, screens=tuple(screen_ids))
            for name, screen_ids in uses.items()
        ),
        errors=tuple(errors),
    )
