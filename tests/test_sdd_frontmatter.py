"""YAML frontmatter of OKF nodes: minimal field validation (ADR-020 §7-8)."""

import pytest

from dark_factory.context.sdd.errors import FrontmatterError
from dark_factory.context.sdd.frontmatter import (
    parse_frontmatter,
    render_document,
    split_frontmatter,
    validate_frontmatter,
    with_status,
)
from tests.sdd_factories import make_frontmatter

DOCUMENT = """\
---
schema: dark-factory.dev/requirement/v1
id: req:pilot:reservation:timeout
type: requirement
title: Reservation timeout
product: pilot
status: proposed
change: chg:pilot:2026:0002
---

The reservation must expire after 30 minutes.
"""


def test_split_returns_mapping_and_body() -> None:
    raw, body = split_frontmatter(DOCUMENT)
    assert raw is not None
    assert raw["id"] == "req:pilot:reservation:timeout"
    assert body.lstrip().startswith("The reservation")


def test_document_without_frontmatter_has_none() -> None:
    raw, _body = split_frontmatter("# Just a heading\n")
    assert raw is None
    assert parse_frontmatter("# Just a heading\n") is None


def test_unterminated_frontmatter_is_an_error() -> None:
    with pytest.raises(FrontmatterError, match="unterminated"):
        split_frontmatter("---\nid: x\n")


def test_missing_minimal_fields_are_reported() -> None:
    with pytest.raises(FrontmatterError, match="title, product"):
        validate_frontmatter(
            {
                "schema": "dark-factory.dev/requirement/v1",
                "id": "req:x",
                "type": "requirement",
                "status": "proposed",
                "change": "chg:x:2026:0001",
            }
        )


def test_parse_returns_typed_frontmatter() -> None:
    frontmatter = parse_frontmatter(DOCUMENT)
    assert frontmatter is not None
    assert frontmatter.schema_ == "dark-factory.dev/requirement/v1"
    assert frontmatter.type == "requirement"
    assert frontmatter.product == "pilot"


def test_render_and_parse_round_trip() -> None:
    frontmatter = make_frontmatter()
    text = render_document(frontmatter, "Body text.")
    assert text.startswith("---\n")
    assert parse_frontmatter(text) == frontmatter
    _raw, body = split_frontmatter(text)
    assert body.strip() == "Body text."


def test_with_status_keeps_extras() -> None:
    frontmatter = make_frontmatter(realizes=["req:pilot:x"])
    updated = with_status(frontmatter, "active")
    assert updated.status == "active"
    assert updated.realizes == ["req:pilot:x"]  # type: ignore[attr-defined]
    assert frontmatter.status == "proposed"
