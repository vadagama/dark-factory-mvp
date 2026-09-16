"""Pack ``packs/ui``: the Small UIKit blueprint and its product copy (T042, T-071).

The pack ships the real UIKit (ADR-014): DTCG tokens, 12 components + 5
patterns, Storybook as the executable spec and UI gates. Two invariants are
asserted here: the pack metadata is self-consistent (manifest, CHANGELOG,
rules — the determinism contract referenced by ``playwright.config.ts``), and
the copy vendored into the web-app blueprint
(``frontend/packages/ui``) stays byte-identical to the pack blueprint, so a
drifted template fails factory CI instead of a product repository (ADR-015).
"""

import json
import re
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
PACK_ROOT = REPO_ROOT / "packs" / "ui"
KIT = PACK_ROOT / "blueprint" / "ui"
PRODUCT_COPY = REPO_ROOT / "packs" / "web-app" / "blueprint" / "frontend" / "packages" / "ui"

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")

# Working-tree artifacts of the kit (see KIT/.gitignore): never part of the
# template, excluded from the parity comparison. Visual baselines
# (tests/visual/uikit.spec.ts-snapshots) are regenerated in the pinned
# container per the packs/ui rules.md contract and are not copied into the
# blueprint either.
KIT_ARTIFACT_DIRS = frozenset(
    {"node_modules", "storybook-static", "test-results", "playwright-report"}
)
SNAPSHOTS_DIR = "tests/visual/uikit.spec.ts-snapshots"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _pack_manifest() -> dict[str, Any]:
    manifest = yaml.safe_load(_read(PACK_ROOT / "pack.yaml"))
    assert isinstance(manifest, dict)
    return manifest


def _kit_template_files(root: Path) -> dict[str, bytes]:
    """Template files of a kit root: real files minus working artifacts."""
    files: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if KIT_ARTIFACT_DIRS & set(relative.parts):
            continue
        if relative.suffix == ".tsbuildinfo":
            continue
        if relative.as_posix().startswith(SNAPSHOTS_DIR):
            continue
        files[relative.as_posix()] = path.read_bytes()
    return files


def _pattern_component_names(subdir: str) -> set[str]:
    directory = KIT / "src" / subdir
    return {
        path.name.removesuffix(".tsx")
        for path in directory.glob("*.tsx")
        if not path.name.endswith(".stories.tsx")
    }


def test_pack_manifest_declares_a_valid_pack() -> None:
    manifest = _pack_manifest()
    assert manifest["schema"] == "dark-factory.dev/pack/v1"
    assert manifest["id"] == "pack:ui"
    assert manifest["name"] == "ui"
    assert SEMVER.match(manifest["version"]), manifest["version"]
    assert manifest["description"].strip()
    kinds = {entry["path"]: entry["kind"] for entry in manifest["contents"]}
    assert kinds["blueprint"] == "template"
    for path in kinds:
        assert (PACK_ROOT / path).exists(), path


def test_changelog_declares_the_initial_version() -> None:
    changelog = _read(PACK_ROOT / "CHANGELOG.md")
    version = str(_pack_manifest()["version"])
    assert f"## [{version}]" in changelog
    assert version in changelog.split("## [", 1)[1]  # the initial entry is the first one


def test_rules_fix_the_determinism_and_ui_contract() -> None:
    rules = _read(PACK_ROOT / "rules.md")
    # Visual determinism contract (referenced by playwright.config.ts).
    assert "mcr.microsoft.com/playwright" in rules
    assert "maxDiffPixelRatio" in rules
    assert "packs/ui/rules.md" in _read(KIT / "playwright.config.ts")
    # Token discipline, public surface and the human-accepted part of WCAG.
    assert "var(--small-*)" in rules
    assert "policy/eslint-small-ui.mjs" in rules
    assert "WCAG 2.2 AA acceptance checklist" in rules
    assert "подтверждается человеком" in rules


def test_kit_package_json_declares_the_gates() -> None:
    pkg = json.loads(_read(KIT / "package.json"))
    assert pkg["name"] == "@small/ui"
    assert pkg["exports"] == {
        ".": "./src/index.ts",
        "./styles.css": "./src/styles.css",
        "./tokens.css": "./src/tokens.css",
    }
    for script in (
        "tokens:build",
        "lint",
        "typecheck",
        "test",
        "test:gates",
        "test:visual",
        "storybook:build",
    ):
        assert script in pkg["scripts"], script
    # The pinned browser version of the determinism contract (packs/ui/rules.md).
    assert pkg["devDependencies"]["@playwright/test"] == "1.63.0"


def test_kit_public_surface_exports_components_and_patterns() -> None:
    index = _read(KIT / "src" / "index.ts")
    components = _pattern_component_names("components")
    patterns = _pattern_component_names("patterns")
    assert len(components) == 12, components
    assert len(patterns) == 5, patterns
    for name in sorted(components | patterns):
        assert name in index, name
    assert "tokens" in index


def test_product_copy_is_byte_identical_to_the_blueprint() -> None:
    blueprint_files = _kit_template_files(KIT)
    copy_files = _kit_template_files(PRODUCT_COPY)
    assert set(copy_files) == set(blueprint_files)
    for name, payload in blueprint_files.items():
        assert copy_files[name] == payload, name


def test_product_copy_carries_no_build_artifacts() -> None:
    # node_modules is a legitimate working-tree artifact of `npm ci` in the
    # frontend workspace (nested vite versions) — it is restored from the
    # lockfile, never committed: the kit .gitignore that ignores it is part of
    # the parity comparison above. Build/report outputs are different: nothing
    # recreates them in the copy, so their presence would mean a leaked
    # machine-local state in the template.
    for artifact in ("storybook-static", "test-results", "playwright-report"):
        assert not (PRODUCT_COPY / artifact).exists(), artifact
    assert not list(PRODUCT_COPY.rglob("*.tsbuildinfo")), "tsbuildinfo artifacts"
    assert not (PRODUCT_COPY / SNAPSHOTS_DIR).exists(), SNAPSHOTS_DIR
