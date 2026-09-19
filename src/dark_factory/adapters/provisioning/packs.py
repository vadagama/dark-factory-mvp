"""Resolve baseline packs into the files a bootstrap writes (T069, ADR-031 p.1/p.6).

A pack is data, not code (``packs/<name>``): ``pack.yaml`` names it and its version,
and the payload of a bootstrap is its ``baseline/`` subtree. Applying a pack to a
product repository means laying that subtree down as the repository's ``.factory/``
skeleton (ADR-020) — a file ``baseline/<rel>`` becomes ``.factory/<rel>`` at the
repository root, the rule ``packs/product-baseline/README.md`` documents. The other
declared contents of a pack are templates, not bootstrap payload: a ChangeSet is
materialised per change, never at bootstrap.

The loader is strict and fails closed: an unknown pack, a manifest without a name
or a SemVer ``version``, or a missing payload is an actionable error that names the
pack and carries no value that could be a secret (ADR-009). It imports nothing from
the core (``tests/test_import_boundaries.py`` rule B), so every provisioning adapter
that applies packs can share it.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import yaml

BASELINE_DIR: Final[str] = "baseline"
"""Subtree of a pack that becomes the product repository's ``.factory/`` skeleton."""

FACTORY_DIR: Final[str] = ".factory"
"""Product-baseline root inside a product repository (ADR-020)."""

MANIFEST_NAME: Final[str] = "pack.yaml"
"""Manifest file of a pack, the entry point of ``packs/<name>``."""

PACK_SCHEMA: Final[str] = "dark-factory.dev/pack/v1"
"""Schema a loadable manifest must declare — the pack format this loader understands."""

_VERSION_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
"""SemVer 2.0.0 shape, as the pack manifests of this repository are versioned."""


class PackLoadError(ValueError):
    """A pack cannot be resolved into a bootstrap payload (fail closed, ADR-009).

    A ``ValueError`` because the cause is a misconfiguration of the factory
    contour (a missing pack, a broken manifest, a payload-less pack), not an
    absent product repository — the port's ``KeyError`` convention. Named after
    the pack, never after a value: the message is safe to report (ADR-009).
    """


@dataclass(frozen=True, slots=True)
class ResolvedPack:
    """One pack resolved into its bootstrap payload (ADR-031 p.6).

    ``files`` maps repository-relative paths (``.factory/<rel>``) to the bytes
    written there, so an adapter applies a pack without knowing its layout.
    """

    name: str
    version: str
    files: Mapping[str, bytes]


def load_pack(packs_root: Path, name: str) -> ResolvedPack:
    """Resolve ``<packs_root>/<name>`` into the payload a bootstrap writes.

    Reads the manifest strictly — a mapping of the ``dark-factory.dev/pack/v1``
    schema, with a non-blank ``name``, a SemVer ``version`` and, when present, an
    ``id`` that names the pack directory — and materialises the ``baseline/`` subtree
    as the repository's ``.factory/`` skeleton. A missing pack, a foreign or broken
    manifest, a missing payload: ``PackLoadError`` naming the pack. The manifest
    decides the recorded name and version, so a drifted manifest is observed, not
    assumed.
    """
    directory = packs_root / name
    manifest_path = directory / MANIFEST_NAME
    if not manifest_path.is_file():
        raise PackLoadError(
            f"the pack {name!r} could not be loaded: {MANIFEST_NAME} is missing"
            " under the configured packs root"
        )
    manifest = _read_manifest(manifest_path, name)
    schema = str(manifest.get("schema") or "").strip()
    if schema != PACK_SCHEMA:
        raise PackLoadError(
            f"the pack {name!r} is invalid: the manifest schema is not {PACK_SCHEMA!r}"
        )
    identifier = str(manifest.get("id") or "").strip()
    if identifier and identifier.removeprefix("pack:") != name:
        raise PackLoadError(f"the pack {name!r} is invalid: the manifest id does not name the pack")
    pack_name = str(manifest.get("name") or "").strip()
    version = str(manifest.get("version") or "").strip()
    if not pack_name:
        raise PackLoadError(f"the pack {name!r} is invalid: the manifest has no name")
    if not _VERSION_PATTERN.match(version):
        raise PackLoadError(
            f"the pack {name!r} is invalid: the manifest version is not SemVer-shaped"
        )
    payload_root = directory / BASELINE_DIR
    if not payload_root.is_dir():
        raise PackLoadError(
            f"the pack {name!r} has no payload: the {BASELINE_DIR}/ subtree is missing"
        )
    files = {
        f"{FACTORY_DIR}/{path.relative_to(payload_root).as_posix()}": path.read_bytes()
        for path in sorted(payload_root.rglob("*"))
        if path.is_file()
    }
    if not files:
        raise PackLoadError(
            f"the pack {name!r} has no payload: the {BASELINE_DIR}/ subtree is empty"
        )
    return ResolvedPack(name=pack_name, version=version, files=files)


def _read_manifest(path: Path, name: str) -> Mapping[str, Any]:
    """Parse one manifest into a mapping; a broken or non-mapping one fails closed."""
    try:
        manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise PackLoadError(f"the pack {name!r} is invalid: its manifest is unreadable") from error
    if not isinstance(manifest, dict):
        raise PackLoadError(f"the pack {name!r} is invalid: its manifest is not a mapping")
    return manifest
