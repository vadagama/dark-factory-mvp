"""Reusable-workflow wiring of the CI workflows (T033/T-044 incident).

A ``workflow_call`` callee that GitHub refuses to accept does not only break
itself: the *calling* run stops with ``startup_failure`` before a single job is
created, and there are no job logs to explain it. The repo-wide CI outage that
began with T-044 had exactly that shape — ``factory-image.yml`` declares
``permissions: packages: write``, while the job calling it in ``ci.yml`` granted
nothing, so GitHub rejected every ``ci.yml`` run at startup (pull requests and
pushes to ``main`` alike) for hours. ``console-image.yml`` had the same defect.

Two hypotheses were ruled out on the way (recorded here so they are not
re-litigated):

* an expression in ``on.workflow_call.inputs.<id>.default`` — the context
  availability table explicitly allows ``github``, ``inputs`` and ``vars``
  there, so ``default: ${{ github.sha }}`` is valid;
* ``inputs.rebuild-check`` in dot syntax — property names may contain ``-``.

These checks keep the class of defect out: the whole contract between a caller
and a locally called workflow is validated structurally, offline, without a
GitHub round trip. ``actionlint`` does not implement the permission rule, which
is why the defect reached ``main``.

The trigger key is a YAML 1.1 pitfall: a bare ``on`` is parsed as the boolean
``True``, so the trigger block is looked up under both keys.
"""

import pathlib
from dataclasses import dataclass
from typing import Any, Final

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"

WORKFLOWS: Final[dict[str, dict[str, Any]]] = {
    path.name: yaml.safe_load(path.read_text(encoding="utf-8"))
    for path in sorted(WORKFLOW_DIR.glob("*.yml"))
}

PERMISSION_RANK: Final[dict[str, int]] = {"none": 0, "read": 1, "write": 2}
"""Access levels of a `GITHUB_TOKEN` permission, from least to most capable."""

DEFAULT_PERMISSION_LEVEL: Final[str] = "read"
"""Level of an undeclared permission: the default token of a repository is read-only.

Assuming a grant that is not written down is exactly the mistake that produced
the T-044 incident, so an undeclared permission counts as `read` — enough for a
callee that only asks for `read`, never for one that asks for `write`.
"""


@dataclass(frozen=True)
class ReusableCall:
    """One job of one workflow calling a workflow of this repository."""

    caller_name: str
    caller: dict[str, Any]
    job_id: str
    job: dict[str, Any]
    callee_name: str
    callee: dict[str, Any]


def _on(workflow: dict[str, Any]) -> dict[str, Any]:
    """The trigger block of a workflow; a bare ``on`` is ``True`` under YAML 1.1."""
    # Looked up through an untyped view: the trigger key really is a boolean here.
    raw: dict[Any, Any] = workflow
    for key in ("on", True):
        value = raw.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _workflow_call(workflow: dict[str, Any]) -> dict[str, Any]:
    """The ``workflow_call`` contract of a reusable workflow (empty when it is not one)."""
    contract: dict[str, Any] = _on(workflow).get("workflow_call") or {}
    return contract


def _permissions(workflow: dict[str, Any]) -> dict[str, Any]:
    """Workflow-level ``permissions`` as a mapping (an empty mapping when unset)."""
    declared = workflow.get("permissions")
    return declared if isinstance(declared, dict) else {}


def _calls() -> list[ReusableCall]:
    """Every job calling a workflow of this repository, with its callee."""
    found: list[ReusableCall] = []
    for caller_name, caller in WORKFLOWS.items():
        for job_id, job in (caller.get("jobs") or {}).items():
            if not isinstance(job, dict):
                continue
            target = job.get("uses")
            if not isinstance(target, str) or not target.startswith("./"):
                continue
            callee_name = pathlib.Path(target).name
            callee = WORKFLOWS.get(callee_name)
            if callee is None:
                raise AssertionError(f"{caller_name}:{job_id} calls a missing workflow {target}")
            found.append(
                ReusableCall(
                    caller_name=caller_name,
                    caller=caller,
                    job_id=job_id,
                    job=job,
                    callee_name=callee_name,
                    callee=callee,
                )
            )
    return found


def permission_violations(call: ReusableCall) -> list[str]:
    """Permissions the callee requests that the calling job does not grant.

    Kept as a module-level function so the guard can be exercised against a
    known-bad workflow revision (see the T-044 incident note above).
    """
    requested = _permissions(call.callee)
    job_level = call.job.get("permissions")
    granted = job_level if isinstance(job_level, dict) else _permissions(call.caller)

    violations: list[str] = []
    for permission, level in sorted(requested.items()):
        needed = PERMISSION_RANK[str(level)]
        effective = PERMISSION_RANK[str(granted.get(permission, DEFAULT_PERMISSION_LEVEL))]
        if effective < needed:
            violations.append(
                f"{permission}: requested {level},"
                f" granted {granted.get(permission, DEFAULT_PERMISSION_LEVEL)}"
            )
    return violations


def test_the_repository_actually_has_called_workflows() -> None:
    """Guards the checks below: an empty workflow set would pass them vacuously."""
    assert WORKFLOWS
    assert _calls()


def test_calling_jobs_grant_what_the_callee_requests() -> None:
    """A callee requesting more than the caller grants is rejected at startup (T-044)."""
    for call in _calls():
        assert permission_violations(call) == [], (
            f"{call.caller_name}:{call.job_id} -> {call.callee_name}: {permission_violations(call)}"
        )


def test_calling_jobs_pass_only_declared_inputs() -> None:
    """A caller passing an input the callee does not declare is a startup_failure."""
    for call in _calls():
        declared = set((_workflow_call(call.callee).get("inputs") or {}).keys())
        passed = set((call.job.get("with") or {}).keys())

        assert passed <= declared, (
            f"{call.caller_name}:{call.job_id} passes undeclared inputs {sorted(passed - declared)}"
        )


def test_calling_jobs_never_mix_uses_with_runs_on_or_steps() -> None:
    """A job either calls a workflow or describes steps — never both."""
    for call in _calls():
        assert "runs-on" not in call.job, f"{call.caller_name}:{call.job_id} mixes uses + runs-on"
        assert "steps" not in call.job, f"{call.caller_name}:{call.job_id} mixes uses + steps"


def test_workflow_call_outputs_map_an_output_of_an_existing_job() -> None:
    """`outputs.<id>.value` must reference `jobs.<job>.outputs.<output>` that exists."""
    for name, workflow in WORKFLOWS.items():
        outputs = _workflow_call(workflow).get("outputs") or {}
        jobs = set((workflow.get("jobs") or {}).keys())

        for output_name, spec in outputs.items():
            value = str((spec or {}).get("value", ""))
            assert "jobs." in value, f"{name}:{output_name} does not map a job output"
            referenced = value.split("jobs.", 1)[1].split(".", 1)[0].strip()
            assert referenced in jobs, f"{name}:{output_name} references unknown job {referenced!r}"
