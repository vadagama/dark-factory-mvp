"""Console meta snapshot drift test (T036, ADR-021 p.5).

The committed ``console/src/generated/meta.json`` must always equal the
output of ``console/tools/export_meta.py``: the budgets/limits and
settings/profiles screens render this snapshot, so an unnoticed drift would
show operators stale limits or profiles. The test regenerates the snapshot
in memory (no node, no subprocess) and compares it with the committed file —
pure pytest, so it runs in the standard CI pytest job.
"""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[1]
GENERATOR_PATH = REPO_ROOT / "console" / "tools" / "export_meta.py"
SNAPSHOT_PATH = REPO_ROOT / "console" / "src" / "generated" / "meta.json"


def _load_generator() -> ModuleType:
    """Import the generator by path; ``console/`` is not a Python package."""
    spec = importlib.util.spec_from_file_location("console_export_meta", GENERATOR_PATH)
    assert spec is not None and spec.loader is not None, f"cannot load {GENERATOR_PATH}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_committed_meta_snapshot_matches_generator() -> None:
    generator = _load_generator()
    committed = SNAPSHOT_PATH.read_text(encoding="utf-8")
    assert committed == generator.render_snapshot(), (
        "console/src/generated/meta.json is stale: regenerate and commit it with"
        " `uv run python console/tools/export_meta.py`"
    )


def test_snapshot_is_deterministic() -> None:
    """Two consecutive renders are byte-identical (fixed key sorting)."""
    generator = _load_generator()
    assert generator.render_snapshot() == generator.render_snapshot()


def test_snapshot_parses_and_has_expected_sections() -> None:
    generator = _load_generator()
    snapshot = json.loads(generator.render_snapshot())
    assert snapshot["schema_version"] == 1
    # Limits (FR-008/FR-016/FR-018), role profiles (ADR-007) and the
    # human-participation facts (ADR-018) back console screens 4 and 5.
    assert set(snapshot["limits"]) == {
        "max_rework_rounds",
        "token_budget",
        "cost_budget",
        "deadline",
    }
    roles = [profile["role"] for profile in snapshot["profiles"]]
    assert roles == sorted(roles), "profiles are sorted by role for a stable diff"
    mode = snapshot["factory_mode"]
    assert mode["mode"] in {"with_approvals", "autonomous_until_mr", "manual"}
    # MVP default (ADR-011 p.2): human gates exist, auto-merge is empty.
    assert mode["human_gates"], "the MVP flow must keep its human gates in the snapshot"
