"""Parameterizable CI stages (T058, ADR-026): one ``CI_SKIP_*`` toggle per ci.yml job.

The contract these tests pin without running CI:

* every job of ``ci.yml`` is a stage with its own repository-variable toggle
  ``CI_SKIP_<JOB>`` (``-`` → ``_``, upper case) evaluated in a job-level ``if``;
* a stage runs unless the variable is exactly ``true`` — the opt-out/fail-safe
  rule, so a typo can never drop a gate from the pipeline;
* the trusted image jobs keep their ``needs`` chain and the final SHA, tolerate
  a gate skipped on purpose, and still refuse to build after a failed or
  cancelled gate;
* the instruction (``docs/instructions/manage-ci-stages.md``) and the ADR
  (``docs/adr/ADR-026-parameterizable-ci-stages.md``) stay in sync with the
  workflow — documentation drift fails the suite instead of going unnoticed.
"""

import re
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
INSTRUCTION = REPO_ROOT / "docs" / "instructions" / "manage-ci-stages.md"
ADR = REPO_ROOT / "docs" / "adr" / "ADR-026-parameterizable-ci-stages.md"
ADR_INDEX = REPO_ROOT / "docs" / "adr" / "README.md"

IMAGE_JOBS = ("factory-image", "console-image")
"""Trusted-tier jobs: the only ci.yml jobs with a ``needs`` chain to guard."""

FACTORY_GATES = (
    "lint",
    "typecheck",
    "test",
    "build",
    "security",
    "factory-us1-parity",
    "factory-stage-smoke",
)
"""Deterministic gates every trusted image build must wait for (T033/T036)."""


def _ci() -> dict[str, Any]:
    values: dict[str, Any] = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    return values


def _skip_variable(job: str) -> str:
    """Repository variable that switches one stage off (ADR-026 naming rule)."""
    return f"CI_SKIP_{job.upper().replace('-', '_')}"


def test_every_ci_job_carries_its_skip_toggle() -> None:
    """A new ci.yml job without its toggle is a drift — the stage could not be disabled."""
    jobs = _ci()["jobs"]
    missing = {
        job: job_config.get("if")
        for job, job_config in jobs.items()
        if f"vars.{_skip_variable(job)}" not in str(job_config.get("if"))
    }
    assert not missing, f"ci.yml jobs without their CI_SKIP_* toggle: {missing}"


def test_skip_toggles_are_fail_safe() -> None:
    """Only the literal ``true`` skips a stage; anything else (a typo too) runs it."""
    for job, job_config in _ci()["jobs"].items():
        condition = str(job_config.get("if"))
        variable = _skip_variable(job)
        assert f"vars.{variable} != 'true'" in condition, (job, condition)
        assert f"vars.{variable} == 'true'" not in condition, (job, condition)


def test_image_jobs_keep_the_gate_chain_and_the_final_sha() -> None:
    """Toggling must not weaken the trusted-build contract (T033, ADR-021 p.7)."""
    jobs = _ci()["jobs"]
    for job in IMAGE_JOBS:
        for gate in FACTORY_GATES:
            assert gate in jobs[job]["needs"], f"{job} must still wait for {gate}"
        assert "sha" in jobs[job]["with"], f"{job} must run on the final SHA (FR-009)"
    assert "console-lint" in jobs["console-image"]["needs"], "console gates still gate its image"


def test_image_jobs_tolerate_skipped_gates_but_not_failures() -> None:
    """A gate off on purpose must not skip the image build; a red gate still must."""
    jobs = _ci()["jobs"]
    for job in IMAGE_JOBS:
        condition = str(jobs[job]["if"])
        assert "!cancelled()" in condition, (job, condition)
        assert "!contains(needs.*.result, 'failure')" in condition, (job, condition)
        assert "!contains(needs.*.result, 'cancelled')" in condition, (job, condition)


def test_instruction_documents_every_stage_and_toggle() -> None:
    """`manage-ci-stages.md` describes every stage and names its switch."""
    jobs = _ci()["jobs"]
    text = INSTRUCTION.read_text(encoding="utf-8")
    undocumented = [job for job in jobs if f"`{_skip_variable(job)}`" not in text]
    assert not undocumented, f"instruction does not name the toggle: {undocumented}"
    undescribed = [job for job in jobs if f"`{job}`" not in text]
    assert not undescribed, f"instruction does not describe the stage: {undescribed}"


def test_instruction_references_only_declared_toggles() -> None:
    """The instruction may not promise a variable the workflow does not read."""
    declared = {_skip_variable(job) for job in _ci()["jobs"]}
    referenced = set(re.findall(r"CI_SKIP_[A-Z0-9_]+", INSTRUCTION.read_text(encoding="utf-8")))
    assert referenced <= declared, (
        f"documented but not read by ci.yml: {sorted(referenced - declared)}"
    )


def test_instruction_explains_how_to_toggle() -> None:
    """The instruction is actionable: read, disable, restore, and the web UI path."""
    text = INSTRUCTION.read_text(encoding="utf-8")
    for marker in ("gh variable list", "gh variable set", "gh variable delete", "Settings"):
        assert marker in text, f"instruction must explain {marker!r}"


def test_adr_records_the_decision_and_is_registered() -> None:
    text = ADR.read_text(encoding="utf-8")
    assert text.startswith("# ADR-026:"), "the decision lives in its own ADR (ADR-000 format)"
    assert "CI_SKIP_" in text, "the ADR documents the toggle contract"
    assert "ADR-026" in ADR_INDEX.read_text(encoding="utf-8"), "ADR registry lists the decision"


def test_workflow_header_points_to_the_guide() -> None:
    """The toggle contract is discoverable from the workflow itself."""
    raw = CI_WORKFLOW.read_text(encoding="utf-8")
    assert "docs/instructions/manage-ci-stages.md" in raw
    assert "ADR-026" in raw
