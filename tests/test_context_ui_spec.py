"""UI spec parsing: scenarios, screens, states, elements, links, components (T094, ADR-039)."""

from pathlib import Path

from dark_factory.context.ui_spec import (
    STATE_KINDS,
    UiLink,
    UiState,
    build_ui_spec,
    parse_ui_screen,
    resolve_preview_url,
)

PACK = Path(__file__).resolve().parents[1] / "packs" / "product-baseline" / "changeset" / "design"
CHG = ".factory/changes/2026/CHG-0001"
SCN = f"{CHG}/design/ui/scenarios/SCN-001-example.md"
SCR = f"{CHG}/design/ui/screens/SCR-001-example.md"


def _pack_documents() -> dict[str, str | None]:
    return {
        SCN: (PACK / "ui" / "scenarios" / "SCN-001-example.md").read_text("utf-8"),
        SCR: (PACK / "ui" / "screens" / "SCR-001-example.md").read_text("utf-8"),
    }


def test_pack_templates_build_the_full_spec() -> None:
    spec = build_ui_spec(_pack_documents(), dev_url="https://dev.example.test/")
    assert spec.errors == ()
    (scenario,) = spec.scenarios
    assert scenario.id == "SCN-001" and scenario.path == SCN
    assert [s.id for s in scenario.steps] == ["S1", "S2", "S3"]
    assert scenario.steps[0].screen == "SCR-001" and scenario.screens == ("SCR-001",)
    assert scenario.summary is not None and scenario.summary.startswith("Пример")
    (screen,) = spec.screens
    assert screen.id == "SCR-001" and screen.route == "/checkout/confirm"
    assert screen.preview_url == "https://dev.example.test/checkout/confirm"
    assert tuple(state.kind for state in screen.states) == STATE_KINDS, "all five, in order"
    assert screen.states[2].description is not None and "таймауту" in screen.states[2].description
    assert [e.id for e in screen.elements] == [
        "EL-summary",
        "EL-confirm",
        "EL-timeout-alert",
        "EL-retry",
    ]
    assert screen.elements[1].kind == "button" and screen.elements[1].component == "Button"
    assert screen.components == ("Card", "Button", "Alert")
    assert screen.purpose is not None and "экран подтверждения" in screen.purpose
    assert spec.links == (
        UiLink(
            id="SCR-001->SCR-001",
            from_screen="SCR-001",
            to_screen="SCR-001",
            trigger="Нажатие «Повторить»",
            condition="Состояние error",
        ),
    )
    assert [(c.name, c.screens) for c in spec.components] == [
        ("Card", ("SCR-001",)),
        ("Button", ("SCR-001",)),
        ("Alert", ("SCR-001",)),
    ]


def test_preview_url_rules_never_invent_a_host() -> None:
    assert resolve_preview_url("https://x.test/a", "https://dev.test") == "https://x.test/a"
    assert resolve_preview_url("/calc", "https://dev.test/") == "https://dev.test/calc"
    assert resolve_preview_url("calc", "https://dev.test") == "https://dev.test/calc"
    assert resolve_preview_url("/calc", None) == "/calc"
    assert resolve_preview_url("  ", None) is None and resolve_preview_url(None, "x") is None


SCREEN_GAPS = """---
id: SCR-002
type: ui_screen
title: Result
status: proposed
states:
  loading: spinner
  success: number shown
  bogus: not a kind
elements:
  - id: EL-out
    component: Text
  - kind: button
transitions:
  - to: SCR-001
    trigger: back
  - to: SCR-001
    trigger: back again
  - trigger: nowhere
---
"""


def test_missing_states_are_absent_unknown_nodes_are_errors_and_links_get_suffixes() -> None:
    spec = build_ui_spec(
        {
            f"{CHG}/design/ui/screens/SCR-002-result.md": SCREEN_GAPS,
            f"{CHG}/design/ui/screens/SCR-003-broken.md": "---\n- list\n---\n",
            f"{CHG}/design/ui/notes.md": "no frontmatter\n",
            f"{CHG}/design/ui/screens/SCR-004-gone.md": None,
        }
    )
    (screen,) = spec.screens
    assert screen.states == (
        UiState(kind="loading", description="spinner"),
        UiState(kind="success", description="number shown"),
    ), "a missing kind is simply absent; status is an allowed extra key"
    assert screen.preview_url is None and screen.purpose is None
    assert [e.id for e in screen.elements] == ["EL-out"]
    assert [link.id for link in spec.links] == ["SCR-002->SCR-001", "SCR-002->SCR-001#2"]
    joined = "\n".join(spec.errors)
    assert "unknown state kind 'bogus'" in joined
    assert "element 2 has no id" in joined
    assert "transition 3 has no target screen" in joined
    assert "SCR-003-broken.md: frontmatter" in joined
    assert "notes.md: not a UI scenario or screen" in joined
    assert "SCR-004-gone.md: the document is absent" in joined


def test_scenario_steps_tolerate_strings_and_missing_ids() -> None:
    text = (
        "---\nid: SCN-002\ntype: ui_scenario\ntitle: Quick\nsteps:\n"
        "  - text: first\n  - just a string\n  - screen: SCR-001\n---\n"
    )
    spec = build_ui_spec({"design/ui/scenarios/SCN-002.md": text})
    (scenario,) = spec.scenarios
    assert [(s.id, s.text, s.screen) for s in scenario.steps] == [
        ("S1", "first", None),
        ("S2", "just a string", None),
    ]
    assert spec.errors == ("design/ui/scenarios/SCN-002.md: step 3 has no text",)
    assert scenario.summary is None


def test_screen_type_is_inferred_from_the_file_name_when_the_type_key_is_missing() -> None:
    errors: list[str] = []
    parsed = parse_ui_screen(
        "design/ui/screens/SCR-009.md", "---\nid: SCR-009\n---\n\nPurpose line.\n", errors=errors
    )
    assert parsed is not None
    screen, transitions = parsed
    assert screen.title == "SCR-009" and screen.purpose == "Purpose line." and transitions == []
    assert errors == []
    spec = build_ui_spec({"design/ui/screens/SCR-009.md": "---\nid: SCR-009\n---\n"})
    assert [s.id for s in spec.screens] == ["SCR-009"]
