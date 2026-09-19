"""ADR decision cards: sections, alternatives, impact, malformed documents (T093, ADR-039)."""

from pathlib import Path

from dark_factory.context.decisions import DecisionAlternative, parse_decision_card

PACK = Path(__file__).resolve().parents[1] / "packs" / "product-baseline" / "changeset" / "design"
ADR_PATH = ".factory/changes/2026/CHG-0001/design/decisions/ADR-001-example.md"


def test_pack_template_parses_into_a_full_card() -> None:
    card = parse_decision_card(
        ADR_PATH, (PACK / "decisions" / "ADR-001-example.md").read_text("utf-8"), revision="c0001"
    )
    assert card.id == "adr:example-product:0001"
    assert card.path == ADR_PATH and card.revision == "c0001"
    assert card.title.startswith("Таймаут")
    assert card.document_status == "proposed" and card.status == "proposed", "not derived here"
    assert card.impact == ("public_api", "ui")
    assert card.proposal is not None and card.proposal.startswith("Длительность таймаута")
    assert card.rationale is not None and "единственный источник" in card.rationale
    assert card.consequences is not None and card.consequences.startswith("- Сервис заказов")
    assert [a.title for a in card.alternatives] == [
        "Константа в коде",
        "Таймаут на стороне клиента",
    ]
    first = card.alternatives[0]
    assert first.summary == "Просто; Смена значения требует релиза"
    assert first.rejected_because == "Противоречит требованию AC-2"
    assert card.errors == () and card.pending_alternative is None
    assert card.affected_artifacts == ()


ENGLISH = """---
schema: dark-factory.dev/adr/v1
id: adr:calc:0002
type: adr
title: Cache the percent table
status: superseded
impact: public_api, data_schema
---

## Context

Some context.

## Decision

Cache it in memory.

## Rationale

Fast enough.

## Alternatives

### Recompute every time

Simple but slow on large inputs.
Rejected because: too slow for R2.

### Persist to disk

Survives restarts.

## Consequences

More memory.
"""


def test_english_headings_subsection_alternatives_and_string_impact() -> None:
    card = parse_decision_card("design/decisions/ADR-002-cache.md", ENGLISH)
    assert card.id == "adr:calc:0002" and card.document_status == "superseded"
    assert card.proposal == "Cache it in memory."
    assert card.rationale == "Fast enough."
    assert card.consequences == "More memory."
    assert card.impact == ("public_api", "data_schema")
    assert card.alternatives == (
        DecisionAlternative(
            title="Recompute every time",
            summary="Simple but slow on large inputs.",
            rejected_because="too slow for R2.",
        ),
        DecisionAlternative(title="Persist to disk", summary="Survives restarts."),
    )
    assert card.revision is None


def test_missing_sections_and_missing_frontmatter_fall_back_to_the_file_stem() -> None:
    card = parse_decision_card(
        "design/decisions/ADR-003-storage.md",
        "# Storage layout\n\n## Impact\n\n- data_schema\n- migrations\n",
    )
    assert card.id == "ADR-003-storage" and card.title == "Storage layout"
    assert card.proposal is None and card.rationale is None and card.consequences is None
    assert card.alternatives == () and card.document_status is None
    assert card.impact == ("data_schema", "migrations"), "bullets under ## Impact"
    assert card.errors == ()


def test_malformed_frontmatter_yields_a_card_with_an_error_not_an_exception() -> None:
    broken = "---\n- not\n- a mapping\n---\n\n## Решение\n\nStill readable.\n"
    card = parse_decision_card("design/decisions/ADR-004.md", broken)
    assert card.id == "ADR-004" and card.title == "ADR-004"
    assert card.proposal == "Still readable."
    assert len(card.errors) == 1 and "mapping" in card.errors[0]
    unterminated = parse_decision_card("design/decisions/ADR-005.md", "---\nid: x\n## Decision\n")
    assert unterminated.errors and "unterminated" in unterminated.errors[0]


def test_wrong_type_is_reported_and_two_column_tables_read_title_and_reason() -> None:
    text = (
        "---\nid: adr:x:1\ntype: requirement\ntitle: T\n---\n\n## Alternatives\n\n"
        "| Option | Why not |\n|---|---|\n| Do nothing | Violates REQ-001 |\n"
    )
    card = parse_decision_card("design/decisions/ADR-006.md", text)
    assert card.errors == ("type: expected 'adr', found 'requirement'",)
    assert card.alternatives == (
        DecisionAlternative(title="Do nothing", summary=None, rejected_because="Violates REQ-001"),
    )
