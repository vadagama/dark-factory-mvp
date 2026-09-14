"""Product Baseline under ``.factory/``: factory manifest, content-hash revisions,
reconciliation of accepted deltas (ADR-020, sdd-native-core.md §3-4, §14).

Layout (one product — one repository)::

    .factory/
    ├── factory.yaml     # product identity
    ├── product/         # accepted state only: active / superseded / retired
    └── changes/         # ChangeSet directories

``baseline_revision`` is a deterministic sha256 over the sorted
``(path, content_hash)`` pairs of every file under ``product/`` — no
timestamps, recomputed on demand. ``factory.yaml`` is excluded from the hash on
purpose: it identifies the product and would otherwise make the revision
self-referential.
"""

import json
from collections.abc import Iterable, Sequence
from hashlib import sha256
from pathlib import Path
from typing import Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from dark_factory.context.sdd.errors import BaselineMismatchError, MissingArtifactError
from dark_factory.context.sdd.frontmatter import (
    parse_frontmatter,
    render_document,
    split_frontmatter,
    with_status,
)
from dark_factory.context.sdd.models import (
    DeltaOperation,
    DeltaOperationKind,
    Frontmatter,
    ReconciliationResult,
)

FACTORY_SCHEMA: Final = "dark-factory.dev/factory/v1"

# Literal contract type of FACTORY_SCHEMA; keep the two in sync.
type FactorySchema = Literal["dark-factory.dev/factory/v1"]
PRODUCT_DIR: Final = "product"

_BASELINE_SCHEMA_CONFIG: Final = ConfigDict(populate_by_name=True, serialize_by_alias=True)


class FactoryManifest(BaseModel):
    """Identity of the product baseline (``factory.yaml``)."""

    model_config = _BASELINE_SCHEMA_CONFIG

    schema_: FactorySchema = Field(FACTORY_SCHEMA, alias="schema")
    product: str = Field(min_length=1)
    title: str | None = None


class BaselineFile(BaseModel):
    """One baseline file: path relative to the factory root + sha256 of content."""

    model_config = ConfigDict(frozen=True)

    path: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)


def hash_bytes(content: bytes) -> str:
    """sha256 hex digest of file content."""
    return sha256(content).hexdigest()


def compute_revision(files: Iterable[BaselineFile]) -> str:
    """Deterministic sha256 over the sorted (path, content_hash) pairs."""
    pairs = sorted((item.path, item.content_hash) for item in files)
    payload = json.dumps(pairs, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


def scan_baseline(factory_root: Path) -> list[BaselineFile]:
    """Every file under ``product/`` with its content hash, sorted by path."""
    product_root = factory_root / PRODUCT_DIR
    files: list[BaselineFile] = []
    if product_root.is_dir():
        for path in sorted(product_root.rglob("*")):
            if path.is_file():
                rel = path.relative_to(factory_root).as_posix()
                files.append(BaselineFile(path=rel, content_hash=hash_bytes(path.read_bytes())))
    return files


def current_revision(factory_root: Path) -> str:
    """Revision of the baseline as it is on disk right now."""
    return compute_revision(scan_baseline(factory_root))


def read_factory_manifest(factory_root: Path) -> FactoryManifest:
    """Parse ``factory.yaml`` of the baseline."""
    data = yaml.safe_load((factory_root / "factory.yaml").read_text(encoding="utf-8"))
    return FactoryManifest.model_validate(data)


def write_factory_manifest(factory_root: Path, manifest: FactoryManifest) -> None:
    """Serialize ``factory.yaml`` (wire key ``schema``)."""
    factory_root.mkdir(parents=True, exist_ok=True)
    payload = manifest.model_dump(mode="json", exclude_none=True)
    (factory_root / "factory.yaml").write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )


def init_baseline(factory_root: Path, *, product: str, title: str, change: str) -> None:
    """Create a minimal valid baseline: ``factory.yaml`` + ``product/product.md``.

    The product node is a self-addressable OKF node, so it carries the minimal
    frontmatter (sdd-native-core.md §7) with ``active`` status — the only state
    a fresh baseline may contain (§4).
    """
    write_factory_manifest(factory_root, FactoryManifest(product=product, title=title))
    product_node = Frontmatter(
        schema_="dark-factory.dev/product/v1",
        id=f"product:{product}",
        type="product",
        title=title,
        product=product,
        status="active",
        change=change,
    )
    product_root = factory_root / PRODUCT_DIR
    product_root.mkdir(parents=True, exist_ok=True)
    (product_root / "product.md").write_text(
        render_document(product_node, f"# {title}\n"), encoding="utf-8"
    )


def _find_baseline_doc(factory_root: Path, target_id: str) -> Path:
    """Locate the baseline markdown file whose frontmatter ``id`` equals ``target_id``."""
    product_root = factory_root / PRODUCT_DIR
    for path in sorted(product_root.rglob("*.md")):
        document = parse_frontmatter(path.read_text(encoding="utf-8"))
        if document is not None and document.id == target_id:
            return path
    raise MissingArtifactError(f"baseline document {target_id!r} not found under product/")


def _set_baseline_doc_status(path: Path, status: str) -> None:
    text = path.read_text(encoding="utf-8")
    raw, body = split_frontmatter(text)
    if raw is None:
        raise MissingArtifactError(f"baseline document {path} has no frontmatter")
    document = parse_frontmatter(text)
    if document is None:  # pragma: no cover - guarded by raw check above
        raise MissingArtifactError(f"baseline document {path} has no frontmatter")
    path.write_text(render_document(with_status(document, status), body), encoding="utf-8")


def apply_delta_operations(
    factory_root: Path,
    change_dir: Path,
    operations: Sequence[DeltaOperation],
    *,
    change_id: str,
    expected_revision: str,
) -> ReconciliationResult:
    """Reconcile accepted delta operations into the baseline (sdd-native-core.md §14).

    Guards the expected revision (parallel-change detection), then applies
    ``add``/``modify`` (copy the spec artifact into ``product/`` with
    ``active`` status), ``supersede`` (target becomes ``superseded``) and
    ``retire`` (target becomes ``retired``). Returns the result record; the
    caller persists it where it belongs (``reconciliation/result.yaml``).
    """
    original_revision = current_revision(factory_root)
    if original_revision != expected_revision:
        raise BaselineMismatchError(expected_revision, original_revision)

    for op in operations:
        if op.operation in (DeltaOperationKind.ADD, DeltaOperationKind.MODIFY):
            source = change_dir / "spec" / op.artifact
            if not source.is_file():
                raise MissingArtifactError(
                    f"change artifact {op.artifact!r} not found in {change_dir}"
                )
            source_text = source.read_text(encoding="utf-8")
            document = parse_frontmatter(source_text)
            if document is None:
                raise MissingArtifactError(f"change artifact {op.artifact!r} has no frontmatter")
            target_path = factory_root / PRODUCT_DIR / op.artifact
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(
                render_document(with_status(document, "active"), source_text),
                encoding="utf-8",
            )
        elif op.operation is DeltaOperationKind.SUPERSEDE:
            _set_baseline_doc_status(_find_baseline_doc(factory_root, op.target), "superseded")
        elif op.operation is DeltaOperationKind.RETIRE:
            _set_baseline_doc_status(_find_baseline_doc(factory_root, op.target), "retired")

    return ReconciliationResult(
        change_id=change_id,
        original_revision=original_revision,
        new_revision=current_revision(factory_root),
        applied_operations=len(operations),
        conflicts=[],
    )


def write_reconciliation_result(
    factory_root: Path, change_dir: Path, result: ReconciliationResult
) -> None:
    """Persist ``reconciliation/result.yaml`` of a ChangeSet (§14 step 8)."""
    target = change_dir / "reconciliation"
    target.mkdir(parents=True, exist_ok=True)
    payload = result.model_dump(mode="json", exclude_none=True)
    (target / "result.yaml").write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
