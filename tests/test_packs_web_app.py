"""Pack ``packs/web-app``: blueprint templates are valid and self-consistent (T041, T-070).

The pack is data, not code (ADR-015): every template file is parsed with the
strict parser of its format (tomllib/json/yaml), the Helm chart is linted and
rendered with ``helm`` when the CLI is available, and the release rules are
asserted statically — immutable digest placeholders, no floating tags, no
hardcoded secrets, quota-safe resources for the pilot namespace ``apps-dev``.
"""

import json
import re
import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
PACK_ROOT = REPO_ROOT / "packs" / "web-app"
BLUEPRINT = PACK_ROOT / "blueprint"
BACKEND = BLUEPRINT / "backend"
FRONTEND = BLUEPRINT / "frontend"
DEPLOY = BLUEPRINT / "deploy"
CHART = DEPLOY / "chart"
WORKFLOW = BLUEPRINT / ".github" / "workflows" / "ci.yml"
HELM_TIMEOUT_SECONDS = 60

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")

# apps-dev envelope (deploy/bootstrap/manifests/quotas.yaml, T029), canonical units.
QUOTA_REQUESTS_CPU_M = 500
QUOTA_REQUESTS_MEM_MIB = 1024
QUOTA_LIMITS_CPU_M = 1000
QUOTA_LIMITS_MEM_MIB = 2048
LIMITRANGE_MIN_CPU_M = 25
LIMITRANGE_MIN_MEM_MIB = 32
LIMITRANGE_MAX_CPU_M = 2000
LIMITRANGE_MAX_MEM_MIB = 2048

BLUEPRINT_FILES = (
    "README.md",
    ".gitignore",
    ".github/workflows/ci.yml",
    "backend/pyproject.toml",
    "backend/uv.lock",
    "backend/.python-version",
    "backend/.env.example",
    "backend/alembic.ini",
    "backend/migrations/env.py",
    "backend/migrations/script.py.mako",
    "backend/migrations/versions/0001_initial.py",
    "backend/src/app/__init__.py",
    "backend/src/app/config.py",
    "backend/src/app/db.py",
    "backend/src/app/models.py",
    "backend/src/app/health.py",
    "backend/src/app/main.py",
    "backend/tests/__init__.py",
    "backend/tests/unit/__init__.py",
    "backend/tests/unit/test_config.py",
    "backend/tests/unit/test_health.py",
    "backend/tests/integration/__init__.py",
    "backend/tests/integration/test_health_database.py",
    "frontend/package.json",
    "frontend/package-lock.json",
    "frontend/.nvmrc",
    "frontend/.prettierrc.json",
    "frontend/eslint.config.js",
    "frontend/tsconfig.json",
    "frontend/vite.config.ts",
    "frontend/index.html",
    "frontend/nginx.conf.template",
    "frontend/src/main.tsx",
    "frontend/src/App.tsx",
    "frontend/src/index.css",
    "frontend/src/api/client.ts",
    "frontend/src/api/client.test.ts",
    "frontend/src/pages/HealthPage.tsx",
    "frontend/src/pages/HealthPage.test.tsx",
    "frontend/src/test/setup.ts",
    # frontend/packages/ui is the vendored Small UIKit (packs/ui): its file set
    # and byte parity with the pack blueprint are asserted in
    # tests/test_packs_ui.py, so they are not enumerated here.
    "deploy/Dockerfile.backend",
    "deploy/Dockerfile.frontend",
    "deploy/chart/Chart.yaml",
    "deploy/chart/values.yaml",
    "deploy/chart/values-apps-dev.yaml",
    "deploy/chart/templates/_helpers.tpl",
    "deploy/chart/templates/backend-deployment.yaml",
    "deploy/chart/templates/backend-service.yaml",
    "deploy/chart/templates/backend-migrations-job.yaml",
    "deploy/chart/templates/frontend-deployment.yaml",
    "deploy/chart/templates/frontend-service.yaml",
    "deploy/chart/templates/postgres-statefulset.yaml",
)

# Static floating-tag scan: template sources humans edit (generated lockfiles
# are pinned by construction and checked separately).
_TEMPLATE_SUFFIXES = frozenset({".yaml", ".yml", ".tpl"})

SECRET_PATTERNS = (
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"ghp_[A-Za-z0-9]{36}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{22,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)password\s*[:=]\s*['\"][^'\"<\s]{3,}"),
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _pack_manifest() -> dict[str, Any]:
    manifest = yaml.safe_load(_read(PACK_ROOT / "pack.yaml"))
    assert isinstance(manifest, dict)
    return manifest


def _cpu_millicores(value: str) -> int:
    """Kubernetes CPU quantity to millicores (``m`` suffix or whole cores)."""
    if value.endswith("m"):
        return int(value[:-1])
    return int(float(value) * 1000)


def _memory_mib(value: str) -> int:
    """Kubernetes memory quantity to MiB (``Gi``/``Mi``; plain means bytes)."""
    if value.endswith("Gi"):
        return int(value[:-2]) * 1024
    if value.endswith("Mi"):
        return int(value[:-2])
    return int(value) // (1024 * 1024)


def _container_resources(container: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    resources = container.get("resources", {})
    requests = resources.get("requests", {})
    limits = resources.get("limits", {})
    assert isinstance(requests, dict) and isinstance(limits, dict)
    return requests, limits


def _assert_quota(
    containers: list[dict[str, Any]],
    *,
    requests_cpu: int,
    requests_mem: int,
    limits_cpu: int,
    limits_mem: int,
) -> None:
    total_requests_cpu = total_requests_mem = 0
    total_limits_cpu = total_limits_mem = 0
    for container in containers:
        requests, limits = _container_resources(container)
        total_requests_cpu += _cpu_millicores(requests["cpu"])
        total_requests_mem += _memory_mib(requests["memory"])
        total_limits_cpu += _cpu_millicores(limits["cpu"])
        total_limits_mem += _memory_mib(limits["memory"])
        # Every single container also respects the apps-dev LimitRange.
        assert _cpu_millicores(requests["cpu"]) >= LIMITRANGE_MIN_CPU_M
        assert _memory_mib(requests["memory"]) >= LIMITRANGE_MIN_MEM_MIB
        assert _cpu_millicores(limits["cpu"]) <= LIMITRANGE_MAX_CPU_M
        assert _memory_mib(limits["memory"]) <= LIMITRANGE_MAX_MEM_MIB
    assert total_requests_cpu <= requests_cpu
    assert total_requests_mem <= requests_mem
    assert total_limits_cpu <= limits_cpu
    assert total_limits_mem <= limits_mem


def _assert_run_contains(job: dict[str, Any], fragment: str) -> None:
    runs = [str(step.get("run", "")) for step in job.get("steps", [])]
    assert any(fragment in run for run in runs), fragment


def test_pack_manifest_declares_a_valid_pack() -> None:
    manifest = _pack_manifest()
    assert manifest["schema"] == "dark-factory.dev/pack/v1"
    assert manifest["id"] == "pack:web-app"
    assert manifest["name"] == "web-app"
    assert SEMVER.match(manifest["version"]), manifest["version"]
    assert manifest["description"].strip()
    for entry in manifest["contents"]:
        assert (PACK_ROOT / entry["path"]).exists(), entry["path"]


def test_changelog_declares_the_initial_version() -> None:
    changelog = _read(PACK_ROOT / "CHANGELOG.md")
    version = str(_pack_manifest()["version"])
    assert f"## [{version}]" in changelog
    assert version in changelog.split("## [", 1)[1]  # the initial entry is the first one


def test_blueprint_structure_is_complete() -> None:
    for relative in BLUEPRINT_FILES:
        assert (BLUEPRINT / relative).is_file(), relative


def test_backend_pyproject_is_strict_and_complete() -> None:
    data = tomllib.loads(_read(BACKEND / "pyproject.toml"))
    project = data["project"]
    assert project["name"] == "example-product-backend"
    assert SEMVER.match(project["version"])
    assert project["requires-python"] == ">=3.12"
    runtime = {re.split(r"[<>=~]", dep)[0].strip() for dep in project["dependencies"]}
    assert {
        "fastapi",
        "uvicorn",
        "sqlalchemy",
        "alembic",
        "psycopg",
        "pydantic-settings",
    } <= runtime
    dev = {re.split(r"[<>=~]", dep)[0].strip() for dep in data["dependency-groups"]["dev"]}
    assert {"pytest", "ruff", "mypy", "httpx"} <= dev
    # src-layout package mapping, mirroring the factory layout.
    assert data["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == ["src/app"]
    assert data["tool"]["mypy"]["strict"] is True
    assert data["tool"]["ruff"]["line-length"] == 100


def test_backend_lockfile_pins_the_dependency_set() -> None:
    assert (BACKEND / "uv.lock").stat().st_size > 1000, "uv.lock must be committed"
    workflow = _read(WORKFLOW)
    dockerfile = _read(DEPLOY / "Dockerfile.backend")
    assert workflow.count("uv sync --frozen") >= 3, "every backend job must be frozen"
    assert "uv sync --frozen" in dockerfile
    assert "uv.lock" in dockerfile


def test_frontend_package_json_is_valid() -> None:
    pkg = json.loads(_read(FRONTEND / "package.json"))
    assert pkg["name"] == "example-product-frontend"
    assert SEMVER.match(pkg["version"])
    assert pkg["type"] == "module"
    assert pkg["workspaces"] == ["packages/ui"]
    assert pkg["dependencies"]["@small/ui"] == "*"
    assert {"react", "react-dom"} <= set(pkg["dependencies"])
    for script in ("dev", "build", "test", "lint", "typecheck"):
        assert script in pkg["scripts"]
    # Small UIKit gates run through the workspace scripts (packs/ui, ADR-014).
    for script in ("ui:lint", "ui:typecheck", "ui:test", "ui:gates", "ui:storybook:build"):
        assert script in pkg["scripts"]
    assert (FRONTEND / "package-lock.json").stat().st_size > 1000, "lockfile must be committed"
    assert "npm ci" in _read(WORKFLOW)


def test_small_ui_workspace_package_is_the_real_kit() -> None:
    ui_pkg = json.loads(_read(FRONTEND / "packages" / "ui" / "package.json"))
    assert ui_pkg["name"] == "@small/ui"
    assert ui_pkg["exports"] == {
        ".": "./src/index.ts",
        "./styles.css": "./src/styles.css",
        "./tokens.css": "./src/tokens.css",
    }
    index = _read(FRONTEND / "packages" / "ui" / "src" / "index.ts")
    assert "Button" in index
    assert "tokens" in index


def test_exactly_one_starting_migration() -> None:
    versions = sorted((BACKEND / "migrations" / "versions").glob("*.py"))
    assert len(versions) == 1
    migration = _read(versions[0])
    assert re.search(r'revision: str = "0001"', migration)
    assert re.search(r"down_revision: str \| None = None", migration)
    # The starter migration mirrors the model: the notes table.
    assert 'op.create_table(\n        "notes"' in migration


def test_chart_metadata_is_semver() -> None:
    chart = yaml.safe_load(_read(CHART / "Chart.yaml"))
    assert chart["apiVersion"] == "v2"
    assert chart["name"] == "web-app"
    assert chart["type"] == "application"
    assert SEMVER.match(chart["version"])
    assert SEMVER.match(chart["appVersion"])


def test_chart_values_reference_immutable_digest_placeholders() -> None:
    values = yaml.safe_load(_read(CHART / "values.yaml"))
    for section in ("backend", "frontend", "postgres"):
        digest = values[section]["image"]["digest"]
        assert digest.startswith("sha256:__") and digest.endswith("__"), digest
    # No floating tag in any hand-edited template source.
    for path in sorted(BLUEPRINT.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix in _TEMPLATE_SUFFIXES or path.name.startswith("Dockerfile."):
            assert ":latest" not in _read(path), path


def test_apps_dev_overlay_stays_within_the_quota() -> None:
    overlay = yaml.safe_load(_read(CHART / "values-apps-dev.yaml"))
    containers = [
        {"resources": overlay[section]["resources"]}
        for section in ("backend", "frontend", "migrations", "postgres")
    ]
    # The sum includes the transient migrations Job: the upgrade peak must fit.
    _assert_quota(
        containers,
        requests_cpu=QUOTA_REQUESTS_CPU_M,
        requests_mem=QUOTA_REQUESTS_MEM_MIB,
        limits_cpu=QUOTA_LIMITS_CPU_M,
        limits_mem=QUOTA_LIMITS_MEM_MIB,
    )


def test_dockerfiles_pin_digests_and_run_non_root() -> None:
    backend = _read(DEPLOY / "Dockerfile.backend")
    frontend = _read(DEPLOY / "Dockerfile.frontend")
    for name, text in (("backend", backend), ("frontend", frontend)):
        from_lines = [line for line in text.splitlines() if line.startswith("FROM ")]
        assert from_lines, name
        for line in from_lines:
            assert re.search(r"@sha256:[0-9a-f]{64}", line), line
        assert "latest" not in text.lower()
    assert re.search(r"COPY --from=ghcr\.io/astral-sh/uv:\S+@sha256:[0-9a-f]{64}", backend)
    assert "USER 65532" in backend
    assert "libpq5" in backend
    assert "USER 101" in frontend


def test_product_workflow_runs_the_release_rules() -> None:
    text = _read(WORKFLOW)
    workflow = yaml.safe_load(text)
    jobs = workflow["jobs"]
    # Backend gates (the pack conventions: unit+integration pytest, ruff, mypy).
    backend_test = jobs["backend-test"]
    assert "postgres" in backend_test["services"]
    assert "APP_TEST_DATABASE_URL" in backend_test.get("env", {})
    _assert_run_contains(jobs["backend-lint"], "ruff check .")
    _assert_run_contains(jobs["backend-lint"], "ruff format --check .")
    _assert_run_contains(jobs["backend-typecheck"], "mypy")
    _assert_run_contains(backend_test, "pytest")
    # Frontend gates (eslint / tsc / vitest) and the dist build for the image.
    _assert_run_contains(jobs["frontend-lint"], "npm run lint")
    _assert_run_contains(jobs["frontend-typecheck"], "npm run typecheck")
    _assert_run_contains(jobs["frontend-test"], "npm run test")
    _assert_run_contains(jobs["image-frontend"], "npm run build")
    # Small UIKit gates run through the workspace scripts (packs/ui, ADR-014);
    # the visual gate is NOT in product CI — it is owned by the factory CI in
    # the pinned Playwright container (packs/ui rules.md determinism contract).
    ui_gates = jobs["frontend-ui-gates"]
    for script in ("ui:lint", "ui:typecheck", "ui:test", "ui:gates", "ui:storybook:build"):
        _assert_run_contains(ui_gates, f"npm run {script}")
    assert "frontend-ui-gates" in jobs["image-frontend"]["needs"]
    for name in ("image-backend", "image-frontend"):
        job = jobs[name]
        assert job["permissions"]["packages"] == "write", name
        assert job["permissions"]["contents"] == "read"
        assert "refs/heads/main" in job["env"]["CAN_PUSH"], name
        assert job["env"]["TAG"].startswith("sha-${{"), name
        build = next(step for step in job["steps"] if "tags" in step.get("with", {}))
        assert build["with"]["push"] == "${{ env.CAN_PUSH }}"
        assert "${{ env." in build["with"]["tags"] and "IMAGE" in build["with"]["tags"]
        trivy = next(step for step in job["steps"] if "trivy" in str(step.get("run", "")))
        assert trivy.get("if") == "env.CAN_PUSH == 'true'"
        gitleaks = next(step for step in job["steps"] if "gitleaks" in str(step.get("run", "")))
        assert "gitleaks" in str(gitleaks["run"])
    # Every job checks out the final SHA of the change (FR-009 pattern).
    for name, job in jobs.items():
        checkout = job["steps"][0]
        assert "checkout" in str(checkout.get("uses", "")), name
        assert (
            checkout["with"]["ref"] == "${{ github.event.pull_request.head.sha || github.sha }}"
        ), name
    assert ":latest" not in text


def test_blueprint_contains_no_hardcoded_secrets() -> None:
    # Tool caches (.ruff_cache, __pycache__, node_modules, .venv) are working-
    # tree artifacts, never blueprint content: they are gitignored and their
    # binary payloads cannot be scanned meaningfully.
    skipped_dirs = {".ruff_cache", "__pycache__", "node_modules", ".venv", "dist", "coverage"}
    for path in sorted(BLUEPRINT.rglob("*")):
        if not path.is_file() or skipped_dirs & set(path.parts):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in SECRET_PATTERNS:
            assert pattern.search(text) is None, f"{path}: {pattern.pattern}"


@pytest.fixture(scope="module")
def default_docs() -> list[dict[str, Any]]:
    return _render_chart()


@pytest.fixture(scope="module")
def apps_dev_docs() -> list[dict[str, Any]]:
    return _render_chart(
        "example-product", "-n", "apps-dev", "-f", str(CHART / "values-apps-dev.yaml")
    )


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm CLI is not installed")
class TestChartRenders:
    """Render-level contract of the chart, mirroring tests/test_chart_dark_factory.py."""

    def test_helm_lint_passes(self) -> None:
        completed = subprocess.run(
            ["helm", "lint", str(CHART)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=HELM_TIMEOUT_SECONDS,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert "0 chart(s) failed" in completed.stdout

    def test_workload_composition(self, default_docs: list[dict[str, Any]]) -> None:
        kinds = [doc["kind"] for doc in default_docs]
        assert kinds.count("Deployment") == 2  # backend + frontend
        assert kinds.count("Service") == 3  # backend + frontend + postgres
        assert kinds.count("StatefulSet") == 1
        assert kinds.count("Job") == 1  # migrations hook
        # The chart creates no credentials and no service accounts: secrets
        # arrive through existingSecret, the pods use the namespace default.
        assert "Secret" not in kinds
        assert "ServiceAccount" not in kinds

    def test_backend_deployment_contract(self, default_docs: list[dict[str, Any]]) -> None:
        deployment = _workload(default_docs, "Deployment", "backend")
        spec = deployment["spec"]
        assert spec["strategy"]["type"] == "Recreate"  # quota envelope
        pod = spec["template"]["spec"]
        assert pod["automountServiceAccountToken"] is False
        assert pod["securityContext"]["runAsNonRoot"] is True
        assert pod["securityContext"]["runAsUser"] == 65532
        container = pod["containers"][0]
        security = container["securityContext"]
        assert security["readOnlyRootFilesystem"] is True
        assert security["allowPrivilegeEscalation"] is False
        assert security["capabilities"]["drop"] == ["ALL"]
        assert container["image"].endswith("@sha256:__BACKEND_IMAGE_DIGEST__")
        # Liveness without the database, readiness with it (factory pattern).
        assert container["livenessProbe"]["httpGet"]["path"] == "/"
        assert container["readinessProbe"]["httpGet"]["path"] == "/api/healthz"
        env = {entry["name"]: entry for entry in container["env"]}
        database = env["DATABASE_URL"]["valueFrom"]["secretKeyRef"]
        assert database["name"] == "example-product-db"
        assert database["key"] == "DATABASE_URL"

    def test_frontend_deployment_contract(self, default_docs: list[dict[str, Any]]) -> None:
        deployment = _workload(default_docs, "Deployment", "frontend")
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        assert container["securityContext"]["readOnlyRootFilesystem"] is True
        assert deployment["spec"]["template"]["spec"]["securityContext"]["runAsUser"] == 101
        assert container["image"].endswith("@sha256:__FRONTEND_IMAGE_DIGEST__")
        env = {entry["name"]: entry for entry in container["env"]}
        # The nginx /api/ proxy points at the in-cluster backend Service.
        assert env["API_UPSTREAM"]["value"] == "web-app-backend:8000"

    def test_migrations_job_is_a_hook(self, default_docs: list[dict[str, Any]]) -> None:
        job = _workload(default_docs, "Job", "migrations")
        annotations = job["metadata"]["annotations"]
        assert annotations["helm.sh/hook"] == "pre-install,pre-upgrade"
        assert job["spec"]["backoffLimit"] == 0
        assert job["spec"]["activeDeadlineSeconds"] == 300
        container = job["spec"]["template"]["spec"]["containers"][0]
        assert container["command"] == ["alembic", "upgrade", "head"]
        assert container["image"].endswith("@sha256:__BACKEND_IMAGE_DIGEST__")

    def test_postgres_statefulset_contract(self, default_docs: list[dict[str, Any]]) -> None:
        statefulset = _workload(default_docs, "StatefulSet", "database")
        pod = statefulset["spec"]["template"]["spec"]
        assert pod["securityContext"]["fsGroup"] == 999
        container = pod["containers"][0]
        assert container["securityContext"]["runAsUser"] == 999
        # Deliberately not hardened to a read-only rootfs (bootstrap T029 parity).
        assert container["securityContext"].get("readOnlyRootFilesystem") is not True
        env = {entry["name"]: entry for entry in container["env"]}
        assert env["PGDATA"]["value"].endswith("/pgdata")
        assert env["POSTGRES_PASSWORD"]["valueFrom"]["secretKeyRef"]["name"] == "example-product-db"
        assert "pg_isready" in str(container["readinessProbe"]["exec"]["command"])
        claims = statefulset["spec"]["volumeClaimTemplates"]
        assert claims[0]["spec"]["accessModes"] == ["ReadWriteOnce"]
        assert container["image"].endswith("@sha256:__POSTGRES_IMAGE_DIGEST__")

    def test_rendered_images_are_digest_pinned(self, apps_dev_docs: list[dict[str, Any]]) -> None:
        for doc in apps_dev_docs:
            if doc["kind"] not in {"Deployment", "StatefulSet", "Job"}:
                continue
            for container in doc["spec"]["template"]["spec"]["containers"]:
                assert re.search(r"@sha256:\S+$", container["image"]), container["image"]
                assert "latest" not in container["image"]

    def test_apps_dev_render_stays_within_the_quota(
        self, apps_dev_docs: list[dict[str, Any]]
    ) -> None:
        containers = [
            container
            for doc in apps_dev_docs
            if doc["kind"] in {"Deployment", "StatefulSet", "Job"}
            for container in doc["spec"]["template"]["spec"]["containers"]
        ]
        assert len(containers) == 4  # backend + frontend + postgres + migrations
        _assert_quota(
            containers,
            requests_cpu=QUOTA_REQUESTS_CPU_M,
            requests_mem=QUOTA_REQUESTS_MEM_MIB,
            limits_cpu=QUOTA_LIMITS_CPU_M,
            limits_mem=QUOTA_LIMITS_MEM_MIB,
        )

    def test_apps_dev_render_uses_the_pinned_release_name(
        self, apps_dev_docs: list[dict[str, Any]]
    ) -> None:
        names = [doc["metadata"]["name"] for doc in apps_dev_docs]
        assert "example-product-backend" in names
        assert "example-product-frontend" in names
        assert "example-product-postgres" in names
        assert "example-product-migrations" in names


def _render_chart(release: str = "web-app", *extra_args: str) -> list[dict[str, Any]]:
    command = ["helm", "template", release, str(CHART), *extra_args]
    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=HELM_TIMEOUT_SECONDS,
        check=False,
    )
    assert completed.returncode == 0, f"helm template failed:\n{completed.stderr}"
    documents = [doc for doc in yaml.safe_load_all(completed.stdout) if doc is not None]
    assert documents
    return documents


def _workload(docs: list[dict[str, Any]], kind: str, component: str) -> dict[str, Any]:
    found = [
        doc
        for doc in docs
        if doc["kind"] == kind
        and doc.get("metadata", {}).get("labels", {}).get("app.kubernetes.io/component")
        == component
    ]
    assert len(found) == 1, f"expected exactly one {kind} with component={component}"
    return found[0]
