"""Artifact read model: kinds, anchors, properties, diff (T082/T083, ADR-035)."""

import pytest

from dark_factory.context.artifacts import (
    PROTECTED_PROPERTIES,
    ArtifactKind,
    ProtectedPropertyError,
    anchors_of,
    apply_properties,
    classify_path,
    document_properties,
    heading_slug,
    is_changeset_artifact,
    resolve_anchor,
    unified_diff,
)

CHG = ".factory/changes/2026/CHG-0001-percent"

DOC = """---
schema: dark-factory.dev/requirement/v1
id: req:calc:percent
type: requirement
title: Percent button
product: calc
status: proposed
change: chg:calc:2026:0001
owner: alice
---

# Percent button {#percent}

## Acceptance criteria

- AC-1: pressing % divides by 100.
- AC-2: rounding is half up.

```text
REQ-999 is only an example inside code
```

<a id="notes"></a>
See REQ-001 and ADR-003.
"""


@pytest.mark.parametrize(
    ("path", "kind"),
    [
        (f"{CHG}/spec/requirements/REQ-001.md", ArtifactKind.SPEC),
        (f"{CHG}/spec/delta.yaml", ArtifactKind.SPEC),
        (f"{CHG}/intent.md", ArtifactKind.SPEC),
        (f"{CHG}/change.yaml", ArtifactKind.SPEC),
        (f"{CHG}/design/overview.md", ArtifactKind.DESIGN),
        (f"{CHG}/design/decisions/ADR-001-storage.md", ArtifactKind.ADR),
        (f"{CHG}/decisions/ADR-002.md", ArtifactKind.ADR),
        (f"{CHG}/design/ADR-003-cache.md", ArtifactKind.ADR),
        (f"{CHG}/ui/screens/main.md", ArtifactKind.UI),
        (f"{CHG}/design/ui/scenarios/SCN-001-checkout.md", ArtifactKind.UI),
        (f"{CHG}/design/ui/screens/SCR-001-confirm.md", ArtifactKind.UI),
        (f"{CHG}/design/screens/SCR-002.md", ArtifactKind.UI),
        (f"{CHG}/screens/SCR-003.md", ArtifactKind.UI),
        ("design/ui/screens/SCR-001.md", ArtifactKind.UI),
        (f"{CHG}/tasks/graph.yaml", ArtifactKind.PLAN),
        (f"{CHG}/verification/plan.yaml", ArtifactKind.PLAN),
        (f"{CHG}/evidence/index.yaml", ArtifactKind.OTHER),
        ("README.md", ArtifactKind.OTHER),
    ],
)
def test_classify_path_by_the_changeset_layout(path: str, kind: ArtifactKind) -> None:
    assert classify_path(path) is kind


def test_only_text_files_under_the_changes_root_are_artifacts() -> None:
    assert is_changeset_artifact(f"{CHG}/spec/requirements/REQ-001.md")
    assert is_changeset_artifact(f"{CHG}/change.yaml")
    assert not is_changeset_artifact(f"{CHG}/ui/screen.png")
    assert not is_changeset_artifact(".factory/product/requirements/REQ-000.md")
    assert not is_changeset_artifact("src/app.py")


def test_anchors_include_frontmatter_id_headings_stable_ids_and_explicit_anchors() -> None:
    anchors = anchors_of(DOC)
    assert anchors[0] == "req:calc:percent"
    assert "percent" in anchors, "explicit {#id} on a heading"
    assert "percent-button" in anchors, "heading slug"
    assert "acceptance-criteria" in anchors
    assert {"AC-1", "AC-2", "REQ-001", "ADR-003", "notes"} <= set(anchors)
    assert "REQ-999" not in anchors, "ids inside fenced code are not anchors"
    assert len(anchors) == len(set(anchors)), "no duplicates"


def test_resolve_anchor_semantics() -> None:
    assert resolve_anchor(DOC, "AC-2")
    assert not resolve_anchor(DOC, "AC-3")
    assert resolve_anchor(DOC, None), "no anchor id = the whole document"
    assert not resolve_anchor(None, None), "an absent artifact resolves nothing"
    assert not resolve_anchor(None, "AC-1")


def test_heading_slug_keeps_unicode_and_drops_punctuation() -> None:
    assert heading_slug("Критерии приёмки: v2!") == "критерии-приёмки-v2"
    assert heading_slug("Percent button {#percent}") == "percent-button"


def test_document_properties_split_frontmatter_and_keep_the_body() -> None:
    properties, body, error = document_properties(DOC)
    assert error is None
    assert properties is not None
    assert properties.values["owner"] == "alice"
    assert properties.protected == PROTECTED_PROPERTIES
    assert set(properties.editable) == {"title", "status", "owner"}
    assert body.lstrip().startswith("# Percent button")


def test_document_without_frontmatter_and_a_broken_block_are_distinguishable() -> None:
    assert document_properties("# Just text\n") == (None, "# Just text\n", None)
    properties, body, error = document_properties("---\n- not\n- a mapping\n---\nbody\n")
    assert properties is None
    assert body.startswith("---"), "nothing is dropped from a broken document"
    assert error is not None and "mapping" in error


def test_apply_properties_edits_editable_keys_and_protects_system_ids() -> None:
    updated = apply_properties(DOC, {"status": "approved", "owner": None, "risk": "R1"})
    properties, body, _ = document_properties(updated)
    assert properties is not None
    assert properties.values["status"] == "approved"
    assert "owner" not in properties.values
    assert properties.values["risk"] == "R1"
    assert list(properties.values)[:3] == ["schema", "id", "type"], "key order is kept"
    assert body.strip() == document_properties(DOC)[1].strip(), "the body is untouched"
    assert updated.endswith("See REQ-001 and ADR-003.\n"), "the trailing newline survives"
    with pytest.raises(ProtectedPropertyError):
        apply_properties(DOC, {"id": "req:calc:other"})
    # Re-submitting the same protected value is not a change.
    assert apply_properties(DOC, {"id": "req:calc:percent"}) == apply_properties(DOC, {})


def test_apply_properties_creates_frontmatter_when_missing() -> None:
    text = apply_properties("# Body only\n", {"title": "T"})
    assert text.startswith("---\ntitle: T\n---\n")
    assert text.endswith("# Body only\n")


def test_unified_diff_counts_added_and_removed_lines() -> None:
    diff = unified_diff(
        "a\nb\nc\n", "a\nB\nc\nd\n", path="spec/x.md", from_revision="r1", to_revision="r2"
    )
    assert diff.added == 2 and diff.removed == 1
    assert "--- spec/x.md@r1" in diff.unified and "+++ spec/x.md@r2" in diff.unified
    assert unified_diff("same", "same", path="p", from_revision="a", to_revision="b").is_empty


SCREEN = """---
schema: dark-factory.dev/ui-screen/v1
id: SCR-001
type: ui_screen
title: Confirm
elements:
  - id: EL-display
    kind: text
  - id: EL-btn-percent
    kind: button
transitions:
  - to: SCR-002
    trigger: press
---

## States

Step S1 shows the display; S12 retries. The S3 bucket is not a step but matches as a word.
See SCN-001 and `EL-retry`.
"""


def test_ui_ids_declared_in_frontmatter_and_body_are_anchors() -> None:
    anchors = anchors_of(SCREEN)
    assert anchors[0] == "SCR-001", "the frontmatter id comes first"
    assert {"EL-display", "EL-btn-percent", "SCR-002"} <= set(anchors), "frontmatter ids"
    assert {"S1", "S12", "SCN-001", "EL-retry", "states"} <= set(anchors), "body ids"
    assert "S3" in anchors, "S<n> matches as a whole word"
    assert len(anchors) == len(set(anchors))
    assert resolve_anchor(SCREEN, "EL-btn-percent")
    assert not resolve_anchor(SCREEN, "EL-missing")


def test_step_and_element_ids_match_only_as_whole_words() -> None:
    anchors = anchors_of("HTTPS1 and S1x and xS2 are not steps; S4 is. EL-a_b is EL-a only.\n")
    assert "S4" in anchors
    assert not {"S1", "S2", "HTTPS1"} & set(anchors)
    assert "EL-a" in anchors and "EL-a_b" not in anchors
