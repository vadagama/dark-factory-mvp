"""Native SDD adapter: ChangeSets on disk under ``.factory/`` (ADR-020 p.8).

Implements the port structurally — no import of ``dark_factory.ports`` (a
runtime-checkable protocol validates instances without it, and the port layer
imports this package, so a back-import would be a cycle). File layout follows
``specs/001-dark-factory-mvp/contracts/changeset.md``:
``.factory/changes/<year>/<CHG-NNNN-slug>/`` with ``change.yaml`` as the entry
point; empty artifact files are not created.
"""

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import yaml
from pydantic import BaseModel

from dark_factory.changes.run_records import from_yaml, to_yaml
from dark_factory.context.sdd.baseline import apply_delta_operations, write_reconciliation_result
from dark_factory.context.sdd.errors import ChangeNotFoundError, MissingArtifactError, SDDError
from dark_factory.context.sdd.frontmatter import (
    parse_frontmatter,
    render_document,
    split_frontmatter,
)
from dark_factory.context.sdd.models import (
    ChangeManifest,
    Delta,
    Document,
    EvidenceIndex,
    ReconciliationPlan,
    TaskGraph,
    VerificationPlan,
)
from dark_factory.context.sdd.normalized import (
    ChangeSet,
    RequirementEntry,
    RequirementsSnapshot,
)

_CHANGE_DIR_PATTERN: Final = re.compile(r"^CHG-(?P<num>\d{4})-")


def change_dir_name(manifest: ChangeManifest) -> str:
    """Stable ChangeSet directory name: ``<CHG-NNNN>-<slug>`` (changeset.md)."""
    number = manifest.id.rsplit(":", 1)[-1]
    return f"CHG-{number}-{manifest.slug}"


def next_change_id(factory_root: Path, product: str) -> str:
    """Next free ChangeSet id for ``product`` in the current year.

    Numbers are derived from existing ``CHG-NNNN-*`` directories; the first
    change of a year gets ``0001``.
    """
    year = datetime.now(UTC).year
    year_root = factory_root / "changes" / str(year)
    highest = 0
    if year_root.is_dir():
        for entry in year_root.iterdir():
            match = _CHANGE_DIR_PATTERN.match(entry.name)
            if match is not None:
                highest = max(highest, int(match.group("num")))
    return f"chg:{product}:{year}:{highest + 1:04d}"


class NativeChangeSetAdapter:
    """Primary ``SDDPort`` implementation over a ``.factory/`` baseline."""

    def __init__(self, factory_root: Path) -> None:
        self._root = factory_root

    @property
    def factory_root(self) -> Path:
        """The ``.factory/`` root this adapter operates on."""
        return self._root

    async def create_change(self, change: ChangeSet, /) -> str:
        """Materialize the ChangeSet directory; returns its id.

        The directory must not exist yet: a ChangeSet is created once and then
        evolves through status transitions and reconciliation, never by
        silent overwrite.
        """
        change_dir = self._change_dir(change.manifest)
        if change_dir.exists():
            raise SDDError(f"ChangeSet directory already exists: {change_dir}")
        change_dir.mkdir(parents=True)
        self._write(change_dir, "change.yaml", change.manifest)
        for document in change.documents:
            self._write_text(change_dir, document.path, _render_document(document))
        if change.delta is not None:
            self._write(change_dir, "spec/delta.yaml", change.delta)
        if change.tasks is not None:
            self._write(change_dir, "tasks/graph.yaml", change.tasks)
        if change.verification is not None:
            self._write(change_dir, "verification/plan.yaml", change.verification)
        if change.evidence is not None:
            self._write(change_dir, "evidence/index.yaml", change.evidence)
        if change.reconciliation is not None:
            self._write(change_dir, "reconciliation/plan.yaml", change.reconciliation)
        return change.manifest.id

    async def read_requirements(self, change_id: str, /) -> RequirementsSnapshot:
        """Requirement view of the spec delta of a persisted ChangeSet."""
        change = self.read_change(change_id)
        entries = []
        if change.delta is not None:
            for op in change.delta.operations:
                entries.append(
                    RequirementEntry(
                        id=op.target,
                        operation=op.operation,
                        artifact=getattr(op, "artifact", None),
                        superseded_by=getattr(op, "superseded_by", None),
                    )
                )
        revision = (
            change.delta.baseline_revision
            if change.delta is not None
            else change.manifest.baseline.revision
        )
        return RequirementsSnapshot(
            change_id=change_id,
            baseline_revision=revision,
            requirements=tuple(entries),
        )

    async def apply_delta(self, change_id: str, /, *, expected_revision: str) -> str:
        """Reconcile the accepted delta into the baseline; returns the new revision."""
        change_dir = self._find_change_dir(change_id)
        delta_path = change_dir / "spec" / "delta.yaml"
        if not delta_path.is_file():
            raise MissingArtifactError(f"ChangeSet {change_id!r} has no spec/delta.yaml")
        delta = from_yaml(Delta, delta_path.read_text(encoding="utf-8"))
        result = apply_delta_operations(
            self._root,
            change_dir,
            delta.operations,
            change_id=change_id,
            expected_revision=expected_revision,
        )
        write_reconciliation_result(self._root, change_dir, result)
        return result.new_revision

    def read_change(self, change_id: str) -> ChangeSet:
        """Load a persisted ChangeSet back into the aggregate (sync helper)."""
        change_dir = self._find_change_dir(change_id)
        manifest = from_yaml(
            ChangeManifest, (change_dir / "change.yaml").read_text(encoding="utf-8")
        )
        documents = [
            _load_document(change_dir, path)
            for path in sorted(change_dir.rglob("*.md"))
            if path.is_file()
        ]
        return ChangeSet(
            manifest=manifest,
            delta=self._read_optional(Delta, change_dir / "spec" / "delta.yaml"),
            documents=documents,
            tasks=self._read_optional(TaskGraph, change_dir / "tasks" / "graph.yaml"),
            verification=self._read_optional(
                VerificationPlan, change_dir / "verification" / "plan.yaml"
            ),
            evidence=self._read_optional(EvidenceIndex, change_dir / "evidence" / "index.yaml"),
            reconciliation=self._read_optional(
                ReconciliationPlan, change_dir / "reconciliation" / "plan.yaml"
            ),
        )

    def _read_optional[T: BaseModel](self, cls: type[T], path: Path) -> T | None:
        if not path.is_file():
            return None
        return from_yaml(cls, path.read_text(encoding="utf-8"))

    def _change_dir(self, manifest: ChangeManifest) -> Path:
        year = manifest.id.split(":")[2]
        return self._root / "changes" / year / change_dir_name(manifest)

    def _find_change_dir(self, change_id: str) -> Path:
        changes_root = self._root / "changes"
        if changes_root.is_dir():
            for path in sorted(changes_root.glob("*/*/change.yaml")):
                raw = yaml.safe_load(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict) and raw.get("id") == change_id:
                    return path.parent
        raise ChangeNotFoundError(f"no ChangeSet {change_id!r} under {self._root}")

    def _write(self, change_dir: Path, rel_path: str, model: BaseModel) -> None:
        target = change_dir / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(to_yaml(model), encoding="utf-8")

    def _write_text(self, change_dir: Path, rel_path: str, content: str) -> None:
        target = change_dir / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _render_document(document: Document) -> str:
    if document.frontmatter is None:
        return document.body
    return render_document(document.frontmatter, document.body)


def _load_document(change_dir: Path, path: Path) -> Document:
    text = path.read_text(encoding="utf-8")
    raw, body = split_frontmatter(text)
    return Document(
        path=path.relative_to(change_dir).as_posix(),
        frontmatter=parse_frontmatter(text) if raw is not None else None,
        body=body.strip(),
    )
