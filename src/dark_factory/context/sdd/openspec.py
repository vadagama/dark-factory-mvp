"""OpenSpec compatibility adapter (ADR-020 p.8): OpenSpec change directories ↔
native delta operations.

OpenSpec represents a change as ``openspec/changes/<name>/specs/<capability>/
spec.md`` with ``## ADDED/MODIFIED/REMOVED Requirements`` sections, each holding
``### Requirement: <name>`` blocks. The converter maps ADDED/MODIFIED/REMOVED to
add/modify/retire operations with stable requirement ids
``req:<product>:<capability>:<kebab>``. On import the adapter writes our
``change.yaml`` into the OpenSpec change directory (a change known to the
native core); ``create``/``read_requirements``/``apply_delta`` delegate to the
native adapter over the output ``.factory/`` root — there is no OpenSpec
baseline (ADR-020 p.3).
"""

import re
from pathlib import Path

from dark_factory.changes.enums import RiskClass
from dark_factory.changes.run_records import to_yaml
from dark_factory.context.sdd.baseline import current_revision, read_factory_manifest
from dark_factory.context.sdd.errors import MissingArtifactError
from dark_factory.context.sdd.lifecycle import ChangeSetStatus
from dark_factory.context.sdd.models import (
    AddOperation,
    BaselineRef,
    ChangeManifest,
    Delta,
    DeltaOperationKind,
    Document,
    Frontmatter,
    ModifyOperation,
    RetireOperation,
    WorkflowRef,
)
from dark_factory.context.sdd.native import NativeChangeSetAdapter, next_change_id
from dark_factory.context.sdd.normalized import ChangeSet, RequirementsSnapshot
from dark_factory.context.sdd.strictness import ArtifactSlot, WorkflowProfile

_SECTION: re.Pattern[str] = re.compile(r"^## (ADDED|MODIFIED|REMOVED) Requirements\s*$")
_REQUIREMENT: re.Pattern[str] = re.compile(r"^### Requirement: (.+?)\s*$")
_NON_ALNUM: re.Pattern[str] = re.compile(r"[^a-z0-9]+")


def kebab(name: str) -> str:
    """Canonical kebab-case fragment of a requirement name."""
    slug = _NON_ALNUM.sub("-", name.lower()).strip("-")
    return slug or "requirement"


def requirement_id(product: str, capability: str, name: str) -> str:
    """OpenSpec requirement id: ``req:<product>:<capability>:<kebab>``."""
    return f"req:{product}:{capability}:{kebab(name)}"


def parse_spec_sections(text: str) -> list[tuple[str, str, str]]:
    """Parse a capability ``spec.md`` into (KIND, requirement name, body) triples.

    Blocks run from their ``### Requirement:`` heading to the next heading of
    either kind; content outside such blocks is ignored.
    """
    entries: list[tuple[str, str, list[str]]] = []
    kind: str | None = None
    name: str | None = None
    buffer: list[str] = []
    for line in text.splitlines():
        section = _SECTION.match(line.strip())
        if section is not None:
            if kind is not None and name is not None:
                entries.append((kind, name, buffer))
            kind, name, buffer = section.group(1), None, []
            continue
        heading = _REQUIREMENT.match(line.strip())
        if heading is not None:
            if kind is not None and name is not None:
                entries.append((kind, name, buffer))
            kind, name, buffer = kind, heading.group(1), []
            continue
        if kind is not None and name is not None:
            buffer.append(line)
    if kind is not None and name is not None:
        entries.append((kind, name, buffer))
    return [
        (entry_kind, entry_name, "\n".join(entry_body).strip())
        for entry_kind, entry_name, entry_body in entries
    ]


class OpenSpecAdapter:
    """Compatibility ``SDDPort`` implementation backed by the native adapter."""

    def __init__(self, native: NativeChangeSetAdapter, openspec_root: Path) -> None:
        self._native = native
        self._openspec_root = openspec_root

    async def create_change(self, change: ChangeSet, /) -> str:
        return await self._native.create_change(change)

    async def read_requirements(self, change_id: str, /) -> RequirementsSnapshot:
        return await self._native.read_requirements(change_id)

    async def apply_delta(self, change_id: str, /, *, expected_revision: str) -> str:
        return await self._native.apply_delta(change_id, expected_revision=expected_revision)

    def import_change(self, change_name: str) -> ChangeSet:
        """Convert an OpenSpec change directory into a ChangeSet.

        Writes our ``change.yaml`` into the OpenSpec change directory so the
        change is registered in native terms while its OpenSpec files remain
        the working surface.
        """
        change_dir = self._openspec_root / "changes" / change_name
        specs_root = change_dir / "specs"
        if not specs_root.is_dir():
            raise MissingArtifactError(
                f"OpenSpec change {change_name!r} has no specs/ under {change_dir}"
            )
        factory_root = self._native.factory_root
        product = read_factory_manifest(factory_root).product
        change_id = next_change_id(factory_root, product)
        revision = current_revision(factory_root)
        operations: list[AddOperation | ModifyOperation | RetireOperation] = []
        documents: list[Document] = []
        for spec_file in sorted(specs_root.glob("*/spec.md")):
            capability = spec_file.parent.name
            for kind, name, body in parse_spec_sections(spec_file.read_text(encoding="utf-8")):
                target = requirement_id(product, capability, name)
                if kind in ("ADDED", "MODIFIED"):
                    artifact = f"requirements/{capability}/{kebab(name)}.md"
                    documents.append(
                        Document(
                            path=f"spec/{artifact}",
                            frontmatter=Frontmatter(
                                schema_="dark-factory.dev/requirement/v1",
                                id=target,
                                type="requirement",
                                title=name,
                                product=product,
                                status="proposed",
                                change=change_id,
                            ),
                            body=body,
                        )
                    )
                    if kind == "ADDED":
                        operations.append(AddOperation(target=target, artifact=artifact))
                    else:
                        operations.append(ModifyOperation(target=target, artifact=artifact))
                else:
                    operations.append(RetireOperation(target=target))
        manifest = ChangeManifest(
            id=change_id,
            title=change_name,
            slug=kebab(change_name),
            product=product,
            kind=WorkflowProfile.PRODUCT_FEATURE.value,
            risk_class=RiskClass.R1,
            status=ChangeSetStatus.PROPOSED,
            baseline=BaselineRef(revision=revision),
            workflow=WorkflowRef(profile=WorkflowProfile.PRODUCT_FEATURE),
            artifacts={ArtifactSlot.SPEC: "spec/delta.yaml"},
        )
        (change_dir / "change.yaml").write_text(to_yaml(manifest), encoding="utf-8")
        return ChangeSet(
            manifest=manifest,
            delta=Delta(baseline_revision=revision, operations=operations),
            documents=documents,
        )

    def export_change(self, change: ChangeSet) -> Path:
        """Render a ChangeSet as an OpenSpec change directory; returns its path.

        ``add``/``modify`` operations become ADDED/MODIFIED blocks grouped by
        capability (content from the matching change documents), ``retire``
        becomes a REMOVED block.
        """
        if change.delta is None:
            raise MissingArtifactError(f"ChangeSet {change.manifest.id!r} has no delta to export")
        change_dir = self._openspec_root / "changes" / change.manifest.slug
        documents = {doc.path: doc for doc in change.documents}
        sections: dict[str, dict[str, list[str]]] = {}
        for op in change.delta.operations:
            if op.operation is DeltaOperationKind.RETIRE:
                capability = op.target.split(":")[2]
                name = op.target.split(":")[3]
                sections.setdefault(capability, {}).setdefault("REMOVED", []).append(
                    f"### Requirement: {name.replace('-', ' ').title()}"
                )
                continue
            if op.operation is DeltaOperationKind.SUPERSEDE:
                # OpenSpec sections cover add/modify/retire only (ADR-020 p.8).
                continue
            capability = op.target.split(":")[2]
            document = documents.get(f"spec/{op.artifact}")
            frontmatter = document.frontmatter if document is not None else None
            title = frontmatter.title if frontmatter is not None else op.target
            body = document.body if document is not None else ""
            sections.setdefault(capability, {}).setdefault(
                "ADDED" if op.operation is DeltaOperationKind.ADD else "MODIFIED", []
            ).append(f"### Requirement: {title}\n\n{body}".strip())
        for capability, kinds in sections.items():
            lines = [f"# {capability} specification", ""]
            for kind in ("ADDED", "MODIFIED", "REMOVED"):
                blocks = kinds.get(kind)
                if blocks:
                    lines.append(f"## {kind} Requirements")
                    lines.append("")
                    lines.extend(blocks)
                    lines.append("")
            spec_dir = change_dir / "specs" / capability
            spec_dir.mkdir(parents=True, exist_ok=True)
            (spec_dir / "spec.md").write_text("\n".join(lines), encoding="utf-8")
        (change_dir / "change.yaml").write_text(to_yaml(change.manifest), encoding="utf-8")
        return change_dir
