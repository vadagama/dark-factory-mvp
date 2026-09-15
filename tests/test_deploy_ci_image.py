"""Trusted OCI image build (T033, docs plan T-044).

Two layers are validated without building anything:

1. the image recipe (``deploy/ci/image/Dockerfile``) as text — the pinned
   digests, the reproducibility contract (SOURCE_DATE_EPOCH, mtime
   normalization, lockfile-only dependencies) and the runtime posture the
   chart (T031) and the agent job (T030) rely on (non-root 65532,
   read-only-rootfs-safe env, alembic + migrations inside the image);
2. the trusted build workflow (``.github/workflows/factory-image.yml``) and
   its call site in ``ci.yml`` — immutable sha-<sha> tags only, no ``latest``,
   ``packages: write`` as the single trusted-tier credential, scan/SBOM
   evidence steps, the optional cold rebuild check, and the gate ordering
   (the image is built only after every deterministic check is green).

The helper script (``deploy/ci/scripts/build-image.sh``) is checked with
``bash -n``/``shellcheck`` when available, following the T032 convention.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
IMAGE_DIR = REPO_ROOT / "deploy" / "ci" / "image"
DOCKERFILE = IMAGE_DIR / "Dockerfile"
DOCKERIGNORE = IMAGE_DIR / "Dockerfile.dockerignore"
BUILD_CONSTRAINTS = IMAGE_DIR / "build-constraints.txt"
IMAGE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "factory-image.yml"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
BUILD_SCRIPT = REPO_ROOT / "deploy" / "ci" / "scripts" / "build-image.sh"

# Digests pinned in the Dockerfile (base image + uv); the workflow pins its
# scanner images too. A pinned digest that silently disappears from the file
# is a reproducibility regression.
PINNED_DIGEST_RE = r"@sha256:[0-9a-f]{64}"

# The exact snapshot date the base image was built against (its own
# debian.sources comment); installing git from a fixed snapshot keeps apt
# packages from drifting the digest.
SNAPSHOT_DATE = "20260824T000000Z"


def _dockerfile_text() -> str:
    return DOCKERFILE.read_text(encoding="utf-8")


def _workflow() -> dict:
    return yaml.safe_load(IMAGE_WORKFLOW.read_text(encoding="utf-8"))


def _ci() -> dict:
    return yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))


def _dockerfile_run_instructions() -> list[str]:
    """RUN instructions with backslash continuations joined into one string.

    Docker strips comment and blank lines before joining continuations, so
    embedded comments do not split an instruction; per-physical-line checks
    would silently miss everything after the first continuation line.
    """
    lines = [
        stripped
        for raw in _dockerfile_text().splitlines()
        if (stripped := raw.strip()) and not stripped.startswith("#")
    ]
    instructions: list[str] = []
    parts: list[str] = []
    for line in lines:
        parts.append(line.removesuffix("\\"))
        if not line.endswith("\\"):
            instructions.append(" ".join(parts))
            parts = []
    return [instruction for instruction in instructions if instruction.startswith("RUN ")]


# --- Dockerfile: reproducibility contract ----------------------------------


def test_dockerfile_exists_with_minimal_context() -> None:
    assert DOCKERFILE.is_file()
    assert DOCKERIGNORE.is_file()
    # Drop comment lines before judging the pattern structure.
    entries = [
        line.strip()
        for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    # Deny-by-default context: the whitelist entries the build consumes.
    assert entries[0] == "**"
    for required in ("!alembic.ini", "!migrations/", "!src/", "!pyproject.toml", "!uv.lock"):
        assert required in entries, f"build context must expose {required}"


def test_dockerfile_pins_base_image_by_digest_in_both_stages() -> None:
    text = _dockerfile_text()
    from_lines = [line.strip() for line in text.splitlines() if line.strip().startswith("FROM ")]
    assert len(from_lines) == 2, "expected exactly builder + runtime stages"
    for line in from_lines:
        assert "python:3.12-slim-bookworm@" in line, line
        assert re.search(PINNED_DIGEST_RE, line), f"base image must be digest-pinned: {line}"
    # Same base digest in both stages: the venv is built and run on identical
    # userspace (the contract run-agent-job.sh and the chart rely on).
    digests = {line.split("@sha256:", 1)[1].split()[0] for line in from_lines}
    assert len(digests) == 1, f"builder and runtime must share one base digest, got {digests}"
    # No floating tags anywhere in the recipe.
    assert "latest" not in text.lower()


def test_dockerfile_source_date_epoch_contract() -> None:
    text = _dockerfile_text()
    assert "ARG SOURCE_DATE_EPOCH" in text
    # Every RUN that writes files ends with mtime normalization (BuildKit
    # does not normalize RUN-produced mtimes itself — verified empirically).
    # Instructions are joined across continuations: the uv sync and apt layers
    # are multi-line and the normalization sits on their last line.
    run_instructions = _dockerfile_run_instructions()
    assert run_instructions, "expected RUN steps in the Dockerfile"
    for line in run_instructions:
        if "touch" in line:
            assert "-d @$SOURCE_DATE_EPOCH" in line or '-d @"$SOURCE_DATE_EPOCH"' in line
        # uv sync steps must carry the normalization in the same layer.
        if "uv sync" in line:
            assert "touch" in line, f"uv sync layer must normalize mtimes: {line}"
        if "apt-get install" in line:
            assert "touch" in line, f"apt layer must normalize mtimes: {line}"


def test_dockerfile_strips_timestamps_from_file_contents() -> None:
    # mtime normalization cannot fix file CONTENTS: uv writes a wall-clock
    # timestamp into uv_cache.json inside freshly built dist-info directories,
    # and apt/dpkg embed Start-Date/End-Date inside /var/log (dpkg.log, apt
    # history/term.log). Both must be deleted in their layers.
    for line in _dockerfile_run_instructions():
        if "uv sync" in line:
            assert "-name uv_cache.json -delete" in line, (
                f"uv sync layer must delete uv_cache.json: {line}"
            )
    apt_layers = [line for line in _dockerfile_run_instructions() if "apt-get install" in line]
    assert len(apt_layers) == 1, "expected exactly one apt layer"
    assert "find /var/log -type f -delete" in apt_layers[0], "apt/dpkg logs embed timestamps"


def test_dockerfile_dependencies_come_from_the_lockfile() -> None:
    text = _dockerfile_text()
    assert "uv sync --frozen" in text, "dependencies must resolve from uv.lock only"
    assert "--no-dev" in text, "the image ships runtime dependencies only"
    assert "UV_COMPILE_BYTECODE=0" in text, ".pyc embeds source mtimes and breaks reproducibility"
    assert "UV_BUILD_CONSTRAINT" in text, "the wheel builder must be pinned"
    assert "PYTHONDONTWRITEBYTECODE=1" in text, "read-only rootfs: python must not write bytecode"
    constraints = BUILD_CONSTRAINTS.read_text(encoding="utf-8").strip()
    assert constraints.splitlines()[-1] == "hatchling==1.32.0", constraints


def test_dockerfile_installs_git_from_fixed_snapshot() -> None:
    text = _dockerfile_text()
    assert "snapshot.debian.org" in text
    assert SNAPSHOT_DATE in text
    assert "apt-get install" in text and "git" in text
    # The sed rewrite covers both archive roots (main and security); the
    # moving mirror survives only inside the sed pattern itself, never as a
    # live source line.
    assert text.count(f"https://snapshot.debian.org/archive/debian/{SNAPSHOT_DATE}") == 1
    assert text.count(f"https://snapshot.debian.org/archive/debian-security/{SNAPSHOT_DATE}") == 1


def test_dockerfile_runtime_posture() -> None:
    text = _dockerfile_text()
    assert "USER 65532:65532" in text, "uid/gid of the agent job (T030) and chart API pod (T031)"
    assert 'CMD ["factory", "--help"]' in text
    assert "COPY alembic.ini" in text and "COPY migrations/" in text, "migrations job contract"
    uv_pinned = re.search(r"ghcr\.io/astral-sh/uv:0\.11\.19@sha256:[0-9a-f]{64}", text)
    assert uv_pinned, "uv must be pinned (version + digest)"
    assert "GIT_SHA" in text and "org.opencontainers.image.revision" in text
    # The revision label is the only build-dependent metadata; no wall-clock
    # labels are allowed.
    assert "build_date" not in text.lower() and "created_at" not in text.lower()


# --- Workflow: immutable publication and evidence ---------------------------


def test_image_workflow_is_valid_yaml_with_trusted_permissions() -> None:
    wf = _workflow()
    assert wf["name"] == "Factory image"
    permissions = wf["permissions"]
    assert permissions["contents"] == "read"
    assert permissions["packages"] == "write"
    assert set(permissions) == {"contents", "packages"}, "no extra trusted-tier credentials"


def test_image_workflow_publishes_only_immutable_sha_tag() -> None:
    wf = _workflow()
    # TAG is the single publication tag: sha-<sha>, never latest/floating.
    job_env = next(iter(wf["jobs"].values()))["env"]
    assert job_env["TAG"].startswith("sha-"), job_env["TAG"]
    # Walk every build step and collect the tags each one publishes.
    tags: list[str] = []
    for job in wf["jobs"].values():
        for step in job.get("steps", []):
            with_block = step.get("with") or {}
            if "tags" in with_block:
                tags.append(str(with_block["tags"]))
    assert tags, "the workflow must publish at least one image tag"
    for tag in tags:
        assert "${{ env.TAG }}" in tag, f"publication must use the immutable TAG, got: {tag}"
        assert "latest" not in tag.lower(), "latest is forbidden for promotion"
    # The consumed image name matches the chart (T031) and agent job (T030).
    assert wf["env"]["FACTORY_IMAGE"] == "ghcr.io/vadagama/dark-factory"


def test_image_workflow_disables_attestations() -> None:
    jobs = _workflow()["jobs"]
    steps_yaml = yaml.safe_dump(jobs)
    assert "provenance: false" in steps_yaml
    assert "sbom: false" in steps_yaml


def test_image_workflow_scanner_images_are_digest_pinned() -> None:
    steps_yaml = yaml.safe_dump(_workflow()["jobs"])
    for image in (
        "zricethezav/gitleaks:v",
        "ghcr.io/aquasecurity/trivy:",
        "ghcr.io/anchore/syft:v",
    ):
        assert image in steps_yaml, image
    for digest_ref in re.findall(r"[^\s]+@sha256:[0-9a-f]{64}", steps_yaml):
        assert "00000" not in digest_ref, f"placeholder digest left in workflow: {digest_ref}"


def test_image_workflow_evidence_steps() -> None:
    steps_yaml = yaml.safe_dump(_workflow()["jobs"])
    for marker in ("gitleaks", "bandit", "pip-audit", "trivy", "syft", "image-digest.json"):
        assert marker in steps_yaml, f"missing evidence step: {marker}"
    assert "SOURCE_DATE_EPOCH" in steps_yaml


def test_image_workflow_rebuild_check() -> None:
    steps_yaml = yaml.safe_dump(_workflow()["jobs"])
    assert "no-cache: true" in steps_yaml
    assert "Compare digests" in steps_yaml
    assert "reproducibility deviation" in steps_yaml


def test_image_workflow_fork_prs_never_push() -> None:
    # Raw text (not yaml.safe_dump): the expression is one long line that
    # safe_dump would wrap.
    raw = IMAGE_WORKFLOW.read_text(encoding="utf-8")
    assert "head.repo.full_name == github.repository" in raw


def test_ci_builds_image_only_after_all_gates() -> None:
    ci = _ci()
    factory_image = ci["jobs"]["factory-image"]
    assert factory_image["uses"].endswith(".github/workflows/factory-image.yml")
    needs = factory_image["needs"]
    for gate in (
        "lint",
        "typecheck",
        "test",
        "build",
        "security",
        "factory-us1-parity",
        "factory-stage-smoke",
    ):
        assert gate in needs, f"trusted build must wait for {gate}"
    with_block = factory_image["with"]
    assert "sha" in with_block, "the trusted build runs on the final SHA (FR-009)"


# --- Helper script -----------------------------------------------------------


def _run_bash_n() -> subprocess.CompletedProcess[str] | None:
    bash = shutil.which("bash")
    if bash is None:
        return None
    return subprocess.run(
        [bash, "-n", str(BUILD_SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def test_build_script_is_valid_shell() -> None:
    assert BUILD_SCRIPT.is_file()
    import os

    assert os.access(BUILD_SCRIPT, os.X_OK), "build-image.sh must be executable"
    result = _run_bash_n()
    if result is None:
        pytest.skip("bash is not installed")
    assert result.returncode == 0, result.stderr


def test_build_script_shellcheck() -> None:
    shellcheck = shutil.which("shellcheck")
    if shellcheck is None:
        pytest.skip("shellcheck is not installed")
    result = subprocess.run(
        [shellcheck, str(BUILD_SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout
