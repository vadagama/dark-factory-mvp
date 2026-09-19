"""The UI specification of a change over git (M3, T094, ADR-039).

``GET /changes/{id}/ui`` and ``factory change ui``: the ``ui`` nodes of the
ChangeSet tree (``design/ui/scenarios/SCN-*.md``, ``design/ui/screens/SCR-*.md``)
parsed by ``context.ui_spec`` into scenarios, screens, links and components,
plus the product's ``dev_url`` from ``.factory/product/factory.yaml`` on the
change branch — the base of a relative ``preview_url``. Without a bound
repository the view is empty and says why.
"""

from typing import Final

import yaml
from pydantic import ConfigDict

from dark_factory.changes.run import Change
from dark_factory.context.artifacts import ArtifactKind
from dark_factory.context.ui_spec import UiSpec, build_ui_spec
from dark_factory.orchestration.artifacts import ArtifactService
from dark_factory.orchestration.decisions import REPOSITORY_NOT_BOUND

__all__ = ["PRODUCT_FACTORY_PATH", "UiSpecView", "build_ui_spec_view", "dev_url_of"]

PRODUCT_FACTORY_PATH: Final[str] = ".factory/product/factory.yaml"
"""The product baseline manifest; its optional ``dev_url`` is the dev environment base."""


class UiSpecView(UiSpec):
    """``GET /changes/{id}/ui`` (contract m3 §2): the spec plus its change, revision and dev URL."""

    model_config = ConfigDict(frozen=True)

    change_id: str
    revision: str | None = None
    dev_url: str | None = None


def dev_url_of(manifest: str | None) -> str | None:
    """``dev_url`` of the product manifest text, or ``None`` when absent or unreadable."""
    if manifest is None:
        return None
    try:
        data = yaml.safe_load(manifest)
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    value = data.get("dev_url")
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def build_ui_spec_view(change: Change, *, artifacts: ArtifactService | None) -> UiSpecView:
    """Scenarios, screens, links and components of ``change`` at the current head."""
    if artifacts is None:
        return UiSpecView(change_id=change.id, errors=(REPOSITORY_NOT_BOUND,))
    tree = artifacts.tree(change)
    if tree.revision is None:
        return UiSpecView(change_id=change.id)
    paths = [node.path for node in tree.nodes if node.kind is ArtifactKind.UI]
    texts = artifacts.read_many(change, [*paths, PRODUCT_FACTORY_PATH])
    dev_url = dev_url_of(texts.pop(PRODUCT_FACTORY_PATH, None))
    spec = build_ui_spec(texts, dev_url=dev_url)
    revision = artifacts.latest_revision(change, paths) or tree.revision
    return UiSpecView(**spec.model_dump(), change_id=change.id, revision=revision, dev_url=dev_url)
