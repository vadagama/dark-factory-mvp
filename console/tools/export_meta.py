"""Generate the console meta snapshot from the Python sources of truth.

T036 / ADR-021 p.5: the budgets/limits and settings/profiles screens need the
factory's configured limits (``dark_factory.orchestration.rules.limits`` over the
``BudgetSnapshot`` defaults), the agent role profiles
(``dark_factory.agents.profiles``) and the human-participation facts
(``dark_factory.orchestration.routes.HUMAN_GATES``, ``orchestration.policy.merge``).
None of these are exposed by the API in the MVP, so the console ships a
static snapshot generated mechanically from these sources:

    uv run python console/tools/export_meta.py

The output is deterministic (fixed key sorting, sorted lists) and committed to
``console/src/generated/meta.json``. ``tests/test_console_meta_snapshot.py``
regenerates the snapshot in memory and fails when the committed file drifts —
run the command above after changing any source of truth. Importing the
snapshot in JS is documented as known tech debt in ``console/README.md``;
a read-only ``/api/v1/meta/*`` will replace it when the data becomes dynamic
(T-062).

The ``mode`` field is a display-only projection (ADR-018): ``with_approvals``
when the flow has human gates (the MVP: specification + review, ADR-011 p.2),
``autonomous_until_mr`` when no gate waits for a human but the merge policy
lists auto-mergeable risk classes, ``manual`` otherwise. Interactive
switching is out of scope — there is no API for it.
"""

import json
from pathlib import Path
from typing import Any

from dark_factory.agents.profiles.manifest import AgentProfile
from dark_factory.agents.profiles.registry import CORE_ROLES, get_profile
from dark_factory.changes.enums import Gate
from dark_factory.changes.usage import BudgetSnapshot
from dark_factory.orchestration.policy.merge import DEFAULT_MERGE_POLICY
from dark_factory.orchestration.routes import HUMAN_GATES, STAGE_SEQUENCE

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_PATH = REPO_ROOT / "console" / "src" / "generated" / "meta.json"

SNAPSHOT_SCHEMA_VERSION = 1


def _limits(budget: BudgetSnapshot) -> dict[str, Any]:
    """Configured run limits (FR-008/FR-016/FR-018); ``None`` means "not set"."""
    return {
        "max_rework_rounds": budget.max_rework_rounds,
        "token_budget": budget.token_budget,
        # Decimal budgets serialize as strings to keep the exact value.
        "cost_budget": None if budget.cost_budget is None else str(budget.cost_budget),
        "deadline": None if budget.deadline is None else budget.deadline.isoformat(),
    }


def _profile(profile: AgentProfile) -> dict[str, Any]:
    """Serialize one role profile; ``model_dump(mode="json")`` maps enums/tuples to wire strings."""
    return profile.model_dump(mode="json")


def _profiles() -> list[dict[str, Any]]:
    profiles = [_profile(get_profile(role)) for role in CORE_ROLES]
    return sorted(profiles, key=lambda item: str(item["role"]))


def _factory_mode() -> dict[str, Any]:
    """Human-participation facts plus the display-only ``mode`` projection."""
    human_gates = [gate.value for gate in Gate if gate in HUMAN_GATES]
    auto_merge = sorted(
        policy_class.value for policy_class in DEFAULT_MERGE_POLICY.auto_merge_risk_classes
    )
    if human_gates:
        mode = "with_approvals"
    elif auto_merge:
        mode = "autonomous_until_mr"
    else:
        mode = "manual"
    return {
        "mode": mode,
        "human_gates": human_gates,
        "auto_merge_risk_classes": auto_merge,
        "merge_authorization_gate": DEFAULT_MERGE_POLICY.merge_authorization_gate.value,
        "merge_methods": sorted(DEFAULT_MERGE_POLICY.merge_methods),
        "stage_sequence": [stage.value for stage in STAGE_SEQUENCE],
    }


def build_snapshot() -> dict[str, Any]:
    """Build the full snapshot dict; pure, no I/O (the drift test reuses it)."""
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "limits": _limits(BudgetSnapshot()),
        "profiles": _profiles(),
        "factory_mode": _factory_mode(),
    }


def render_snapshot() -> str:
    """Render the snapshot with a fixed layout: sorted keys, 2-space indent, trailing newline."""
    return json.dumps(build_snapshot(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(render_snapshot(), encoding="utf-8")
    print(f"meta snapshot written: {OUTPUT_PATH.relative_to(REPO_ROOT)}")


if __name__ == "__main__":  # pragma: no cover - thin I/O wrapper
    main()
