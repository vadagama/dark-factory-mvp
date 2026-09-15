"""The ``dark-factory-runs`` seed: schemas stay in sync with the versioned contracts (T-061).

``deploy/runs/`` prepares the runs repository the way ``deploy/argocd/gitops-seed``
prepares the GitOps one: the repository itself is created by a human (the
bootstrap commands are documented, not executed), while the versioned contracts
it publishes — the JSON schemas of ``RunManifest`` and ``StageResult``
(ADR-015 p.3/p.4) — are generated here and drift-checked.
"""

import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from dark_factory.changes.run import StageResult
from dark_factory.changes.run_records import RunManifest

REPO_ROOT = Path(__file__).resolve().parents[1]
SEED_DIR = REPO_ROOT / "deploy" / "runs"
SCHEMA_DIR = SEED_DIR / "schema"

SCHEMAS: dict[str, type[BaseModel]] = {
    "run-manifest.schema.json": RunManifest,
    "stage-result.schema.json": StageResult,
}


def render_schema(model: type[BaseModel]) -> str:
    """Canonical rendering of a model schema: sorted keys, stable indentation."""
    return json.dumps(model.model_json_schema(), indent=2, sort_keys=True) + "\n"


def test_seed_contains_exactly_the_versioned_contract_schemas() -> None:
    assert sorted(path.name for path in SCHEMA_DIR.glob("*.json")) == sorted(SCHEMAS)


@pytest.mark.parametrize(("name", "model"), sorted(SCHEMAS.items()))
def test_committed_schema_matches_the_model(name: str, model: type[BaseModel]) -> None:
    committed = (SCHEMA_DIR / name).read_text(encoding="utf-8")

    assert committed == render_schema(model)
    assert json.loads(committed)["title"] == model.__name__


def test_seed_readme_documents_the_record_protocol_and_the_publish_command() -> None:
    readme = (SEED_DIR / "README.md").read_text(encoding="utf-8")

    assert "ADR-015" in readme
    assert "factory run publish" in readme
