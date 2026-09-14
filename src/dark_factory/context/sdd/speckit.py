"""Spec Kit bootstrap adapter: read-only import of legacy ``specs/<feature>/``
artifacts into the Native SDD Core (ADR-020 p.8, ADR-001).

Legacy files carry no frontmatter, so the import synthesizes OKF nodes from
them (intent, requirement, design) and parses the ``tasks.md`` checklist into a
TaskGraph. ``specs/`` is never written. ``create_change`` /
``read_requirements`` / ``apply_delta`` delegate to the native adapter over the
output ``.factory/`` root, so an imported bootstrap feature becomes a regular
ChangeSet.
"""

import re
from pathlib import Path

from dark_factory.changes.enums import RiskClass
from dark_factory.context.sdd.baseline import current_revision, read_factory_manifest
from dark_factory.context.sdd.errors import MissingArtifactError
from dark_factory.context.sdd.lifecycle import ChangeSetStatus
from dark_factory.context.sdd.models import (
    AddOperation,
    BaselineRef,
    ChangeManifest,
    Delta,
    Document,
    Frontmatter,
    TaskDef,
    TaskGraph,
    WorkflowRef,
)
from dark_factory.context.sdd.native import NativeChangeSetAdapter, next_change_id
from dark_factory.context.sdd.normalized import ChangeSet, RequirementsSnapshot
from dark_factory.context.sdd.strictness import ArtifactSlot, WorkflowProfile

_TASK_LINE: re.Pattern[str] = re.compile(r"^-\s*\[.\]\s*(?P<id>T\d+)\s+(?P<title>.+)$")
_HEADING: re.Pattern[str] = re.compile(r"^#\s+(?P<title>.+?)\s*$")


def parse_tasks_markdown(text: str) -> list[TaskDef]:
    """Extract ``- [ ] T<NNN> <title>`` checklist lines into TaskDefs."""
    tasks: list[TaskDef] = []
    for line in text.splitlines():
        match = _TASK_LINE.match(line.strip())
        if match is not None:
            tasks.append(TaskDef(id=match.group("id"), title=match.group("title").strip()))
    return tasks


def _first_heading(text: str, fallback: str) -> str:
    for line in text.splitlines():
        match = _HEADING.match(line.strip())
        if match is not None:
            return match.group("title")
    return fallback


class SpecKitAdapter:
    """Bootstrap ``SDDPort`` implementation over legacy Spec Kit artifacts."""

    def __init__(self, native: NativeChangeSetAdapter, specs_root: Path) -> None:
        self._native = native
        self._specs_root = specs_root

    async def create_change(self, change: ChangeSet, /) -> str:
        return await self._native.create_change(change)

    async def read_requirements(self, change_id: str, /) -> RequirementsSnapshot:
        return await self._native.read_requirements(change_id)

    async def apply_delta(self, change_id: str, /, *, expected_revision: str) -> str:
        return await self._native.apply_delta(change_id, expected_revision=expected_revision)

    def import_feature(self, feature: str) -> ChangeSet:
        """Import ``specs/<feature>/`` read-only into a draft ChangeSet."""
        feature_dir = self._specs_root / feature
        spec_path = feature_dir / "spec.md"
        if not spec_path.is_file():
            raise MissingArtifactError(
                f"legacy feature {feature!r} has no spec.md under {feature_dir}"
            )
        spec_text = spec_path.read_text(encoding="utf-8")
        title = _first_heading(spec_text, feature)
        factory_root = self._native.factory_root
        product = read_factory_manifest(factory_root).product
        change_id = next_change_id(factory_root, product)
        revision = current_revision(factory_root)
        target = f"req:{product}:{feature}:main"
        artifact = "requirements/SPEC-001.md"
        documents = [
            Document(
                path="intent.md",
                body=(
                    f"# {title}\n\n"
                    f"Bootstrap import of the Spec Kit feature `{feature}` "
                    "(ADR-001); the legacy spec became "
                    f"`spec/{artifact}` and is refined in this ChangeSet."
                ),
            ),
            Document(
                path=f"spec/{artifact}",
                frontmatter=Frontmatter(
                    schema_="dark-factory.dev/requirement/v1",
                    id=target,
                    type="requirement",
                    title=title,
                    product=product,
                    status="proposed",
                    change=change_id,
                ),
                body=spec_text,
            ),
        ]
        artifacts: dict[ArtifactSlot, str] = {
            ArtifactSlot.INTENT: "intent.md",
            ArtifactSlot.SPEC: "spec/delta.yaml",
        }
        plan_path = feature_dir / "plan.md"
        if plan_path.is_file():
            plan_text = plan_path.read_text(encoding="utf-8")
            documents.append(
                Document(
                    path="design/overview.md",
                    frontmatter=Frontmatter(
                        schema_="dark-factory.dev/design/v1",
                        id=f"design:{product}:{feature}",
                        type="design",
                        title=f"{title} design",
                        product=product,
                        status="proposed",
                        change=change_id,
                    ),
                    body=plan_text,
                )
            )
            artifacts[ArtifactSlot.DESIGN] = "design/overview.md"
        tasks_path = feature_dir / "tasks.md"
        tasks = (
            parse_tasks_markdown(tasks_path.read_text(encoding="utf-8"))
            if tasks_path.is_file()
            else []
        )
        if tasks:
            artifacts[ArtifactSlot.TASKS] = "tasks/graph.yaml"
        manifest = ChangeManifest(
            id=change_id,
            title=title,
            slug=feature,
            product=product,
            kind=WorkflowProfile.PRODUCT_FEATURE.value,
            risk_class=RiskClass.R1,
            status=ChangeSetStatus.DRAFT,
            baseline=BaselineRef(revision=revision),
            workflow=WorkflowRef(profile=WorkflowProfile.PRODUCT_FEATURE),
            artifacts=artifacts,
        )
        return ChangeSet(
            manifest=manifest,
            delta=Delta(
                baseline_revision=revision,
                operations=[AddOperation(target=target, artifact=artifact)],
            ),
            documents=documents,
            tasks=TaskGraph(tasks=tasks) if tasks else None,
        )
