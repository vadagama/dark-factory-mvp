"""Helm chart ``charts/dark-factory``: render-level contract tests (T031 / T-042).

The chart is data, not code: these tests render it with ``helm template``
(default values and the local profile) and assert the structural guarantees
the API deployment relies on — the bootstrap security pattern of T028, the
quota envelope of T029 and ADR-004/009/010. They skip when the ``helm`` CLI
is unavailable, so machines without the tool keep the rest of the suite green.
"""

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CHART_DIR = REPO_ROOT / "charts" / "dark-factory"
LOCAL_VALUES = CHART_DIR / "values-local.yaml"
HELM_TIMEOUT_SECONDS = 60

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm CLI is not installed")


def _render(*extra_args: str) -> list[dict[str, Any]]:
    command = ["helm", "template", "dark-factory", str(CHART_DIR), *extra_args]
    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=HELM_TIMEOUT_SECONDS,
        check=False,
    )
    assert completed.returncode == 0, f"helm template failed:\n{completed.stderr}"
    return [doc for doc in yaml.safe_load_all(completed.stdout) if doc is not None]


def _by_kind(docs: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [doc for doc in docs if doc.get("kind") == kind]


def _first(docs: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    found = _by_kind(docs, kind)
    assert found, f"expected at least one {kind} in the rendered chart"
    return found[0]


def _api_container(deployment: dict[str, Any]) -> dict[str, Any]:
    containers = deployment["spec"]["template"]["spec"]["containers"]
    assert len(containers) == 1, "the API pod must have exactly one container"
    container: dict[str, Any] = containers[0]
    return container


def _workload_with_component(
    docs: list[dict[str, Any]], kind: str, component: str
) -> dict[str, Any]:
    """First workload of `kind` whose metadata component label matches."""
    found = [
        doc
        for doc in _by_kind(docs, kind)
        if doc.get("metadata", {}).get("labels", {}).get("app.kubernetes.io/component") == component
    ]
    assert found, f"expected at least one {kind} with component={component} in the rendered chart"
    return found[0]


def _container_env(container: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["name"]: entry for entry in container["env"]}


@pytest.fixture(scope="module")
def default_docs() -> list[dict[str, Any]]:
    return _render()


@pytest.fixture(scope="module")
def local_docs() -> list[dict[str, Any]]:
    return _render("-f", str(LOCAL_VALUES))


def test_renders_deployment_and_service(default_docs: list[dict[str, Any]]) -> None:
    deployment = _first(default_docs, "Deployment")
    assert deployment["spec"]["replicas"] == 1
    assert deployment["spec"]["strategy"]["type"] == "Recreate"
    labels = deployment["metadata"]["labels"]
    assert labels["app.kubernetes.io/part-of"] == "dark-factory"
    assert labels["app.kubernetes.io/managed-by"] == "dark-factory"
    assert labels["app.kubernetes.io/component"] == "api"
    service = _first(default_docs, "Service")
    assert service["spec"]["type"] == "ClusterIP"
    assert service["spec"]["ports"][0]["targetPort"] == "http"


def test_no_ingress_by_default(default_docs: list[dict[str, Any]]) -> None:
    assert not _by_kind(default_docs, "Ingress")


def test_chart_never_creates_secrets_or_service_accounts(
    default_docs: list[dict[str, Any]], local_docs: list[dict[str, Any]]
) -> None:
    # Secrets are created outside git (pattern of deploy/events, T028) and the
    # default SA comes from the bootstrap (T029): the chart must only refer.
    for docs in (default_docs, local_docs):
        assert not _by_kind(docs, "Secret")
    assert not _by_kind(default_docs, "ServiceAccount")


def test_probes_split_liveness_readiness(default_docs: list[dict[str, Any]]) -> None:
    container = _api_container(_first(default_docs, "Deployment"))
    liveness = container["livenessProbe"]["httpGet"]
    readiness = container["readinessProbe"]["httpGet"]
    # Liveness = process alive without the database; readiness = state store
    # answers (GET /api/v1/runs returns 500 on a database failure).
    assert liveness["path"] == "/openapi.json"
    assert readiness["path"] == "/api/v1/runs"
    assert liveness["port"] == readiness["port"] == "http"


def test_security_context_matches_bootstrap_pattern(
    default_docs: list[dict[str, Any]],
) -> None:
    for workload in (_first(default_docs, "Deployment"), _first(default_docs, "Job")):
        pod_spec = workload["spec"]["template"]["spec"]
        assert pod_spec["automountServiceAccountToken"] is False
        pod_security = pod_spec["securityContext"]
        assert pod_security["runAsNonRoot"] is True
        assert pod_security["runAsUser"] == 65532
        assert pod_security["seccompProfile"]["type"] == "RuntimeDefault"
        container = pod_spec["containers"][0]
        container_security = container["securityContext"]
        assert container_security["allowPrivilegeEscalation"] is False
        assert container_security["readOnlyRootFilesystem"] is True
        assert container_security["capabilities"]["drop"] == ["ALL"]
        mounts = [mount["mountPath"] for mount in container["volumeMounts"]]
        assert "/tmp" in mounts, "read-only root filesystem requires a writable /tmp"


def test_resources_and_image_tag(default_docs: list[dict[str, Any]]) -> None:
    container = _api_container(_first(default_docs, "Deployment"))
    resources = container["resources"]
    assert resources["requests"] == {"cpu": "100m", "memory": "128Mi"}
    assert resources["limits"] == {"cpu": "500m", "memory": "512Mi"}
    image = container["image"]
    assert image.startswith("ghcr.io/vadagama/dark-factory:")
    tag = image.rsplit(":", 1)[-1]
    assert tag and tag != "latest", "the image tag must be pinned, not latest"


def test_bind_host_is_zero_zero_zero_zero(default_docs: list[dict[str, Any]]) -> None:
    # The CLI defaults to 127.0.0.1, which is unreachable from the probes and
    # the Service inside a container — the chart must override the host.
    container = _api_container(_first(default_docs, "Deployment"))
    assert container["command"] == ["factory", "api", "serve"]
    assert container["args"] == ["--host", "0.0.0.0", "--port", "8000"]


def test_database_url_comes_from_secret_ref(default_docs: list[dict[str, Any]]) -> None:
    deployment_env = _container_env(_api_container(_first(default_docs, "Deployment")))
    reference = deployment_env["DATABASE_URL"]["valueFrom"]["secretKeyRef"]
    assert reference == {"name": "factory-api-database", "key": "DATABASE_URL"}
    # No auth secret by default: the token env must be absent entirely.
    assert "DARK_FACTORY_API_TOKENS" not in deployment_env


def test_local_values_enable_ingress(local_docs: list[dict[str, Any]]) -> None:
    ingress = _first(local_docs, "Ingress")
    assert ingress["spec"]["ingressClassName"] == "nginx"
    assert ingress["spec"]["rules"][0]["host"] == "factory.localhost"
    backend = ingress["spec"]["rules"][0]["http"]["paths"][0]["backend"]["service"]
    assert backend["port"]["number"] == 8000


def test_local_values_keep_compact_resources(local_docs: list[dict[str, Any]]) -> None:
    container = _api_container(_first(local_docs, "Deployment"))
    assert container["resources"]["requests"] == {"cpu": "100m", "memory": "128Mi"}
    assert container["resources"]["limits"] == {"cpu": "500m", "memory": "512Mi"}


def test_migrations_job_is_a_hook(default_docs: list[dict[str, Any]]) -> None:
    job = _first(default_docs, "Job")
    annotations = job["metadata"]["annotations"]
    assert annotations["helm.sh/hook"] == "pre-install,pre-upgrade"
    assert annotations["helm.sh/hook-weight"] == "0"
    assert annotations["helm.sh/hook-delete-policy"] == "before-hook-creation,hook-succeeded"
    assert job["spec"]["activeDeadlineSeconds"] == 300
    assert job["spec"]["backoffLimit"] == 0
    container = job["spec"]["template"]["spec"]["containers"][0]
    assert container["command"] == ["alembic", "upgrade", "head"]
    job_env = _container_env(container)
    reference = job_env["DATABASE_URL"]["valueFrom"]["secretKeyRef"]
    assert reference == {"name": "factory-api-database", "key": "DATABASE_URL"}
    pod_spec = job["spec"]["template"]["spec"]
    assert pod_spec["serviceAccountName"] == "factory-api"


# -------------------------------------------------------------------------
# Console (T036, ADR-021 p.7): nginx serving the SPA + /api proxy.
# -------------------------------------------------------------------------


def test_renders_console_deployment_and_service(default_docs: list[dict[str, Any]]) -> None:
    deployment = _workload_with_component(default_docs, "Deployment", "console")
    assert deployment["spec"]["replicas"] == 1
    assert deployment["spec"]["strategy"]["type"] == "Recreate"
    labels = deployment["metadata"]["labels"]
    assert labels["app.kubernetes.io/part-of"] == "dark-factory"
    assert labels["app.kubernetes.io/managed-by"] == "dark-factory"
    assert labels["app.kubernetes.io/component"] == "console"
    service = _workload_with_component(default_docs, "Service", "console")
    assert service["spec"]["type"] == "ClusterIP"
    assert service["spec"]["ports"][0]["port"] == 80
    assert service["spec"]["ports"][0]["targetPort"] == "http"


def test_console_security_context(default_docs: list[dict[str, Any]]) -> None:
    pod_spec = _workload_with_component(default_docs, "Deployment", "console")["spec"]["template"][
        "spec"
    ]
    assert pod_spec["automountServiceAccountToken"] is False
    pod_security = pod_spec["securityContext"]
    assert pod_security["runAsNonRoot"] is True
    # nginx-unprivileged runs as UID/GID 101 — not the 65532 of the API image.
    assert pod_security["runAsUser"] == 101
    assert pod_security["runAsGroup"] == 101
    assert pod_security["seccompProfile"]["type"] == "RuntimeDefault"
    container = pod_spec["containers"][0]
    container_security = container["securityContext"]
    assert container_security["allowPrivilegeEscalation"] is False
    assert container_security["readOnlyRootFilesystem"] is True
    assert container_security["capabilities"]["drop"] == ["ALL"]
    mounts = {mount["mountPath"] for mount in container["volumeMounts"]}
    assert {"/tmp", "/var/cache/nginx"} <= mounts, (
        "read-only root filesystem requires writable nginx temp paths"
    )


def _console_container(docs: list[dict[str, Any]]) -> dict[str, Any]:
    pod_spec = _workload_with_component(docs, "Deployment", "console")["spec"]["template"]["spec"]
    container: dict[str, Any] = pod_spec["containers"][0]
    return container


def test_console_resources_and_image_tag(default_docs: list[dict[str, Any]]) -> None:
    container = _console_container(default_docs)
    resources = container["resources"]
    assert resources["requests"] == {"cpu": "25m", "memory": "32Mi"}
    assert resources["limits"] == {"cpu": "100m", "memory": "64Mi"}
    image = container["image"]
    assert image.startswith("ghcr.io/vadagama/dark-factory-console:")
    tag = image.rsplit(":", 1)[-1]
    # The bootstrap placeholder is allowed (T031 pattern); floating tags are not.
    assert tag and tag != "latest", "the image tag must be pinned, not latest"


def test_console_proxies_to_the_api_service(default_docs: list[dict[str, Any]]) -> None:
    container = _console_container(default_docs)
    env = _container_env(container)
    upstream = env["API_UPSTREAM"]["value"]
    assert upstream == "dark-factory.default.svc.cluster.local:8000"
    # The console holds no credentials: no secret refs at all in its env.
    assert all("valueFrom" not in entry for entry in container["env"])


def test_console_probes_are_process_only(default_docs: list[dict[str, Any]]) -> None:
    # The console serves static files; a probe must not depend on the API.
    container = _console_container(default_docs)
    assert container["livenessProbe"]["httpGet"]["path"] == "/"
    assert container["readinessProbe"]["httpGet"]["path"] == "/"
    assert container["livenessProbe"]["httpGet"]["port"] == "http"


def test_console_container_port(default_docs: list[dict[str, Any]]) -> None:
    container = _console_container(default_docs)
    assert container["ports"] == [{"name": "http", "containerPort": 8080, "protocol": "TCP"}]
