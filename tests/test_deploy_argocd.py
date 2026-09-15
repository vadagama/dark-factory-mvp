"""Deploy tooling of T032: Argo CD install values, root Applications and the
gitops seed.

Two layers are validated without a cluster:

1. the local Applications chart (``deploy/argocd/chart``) rendered with
   ``helm template`` — the same render-level contract style as
   ``tests/test_chart_dark_factory.py`` (T031): namespaces, sources,
   destinations, sync policy, no secrets, no floating tags;
2. the Argo CD install values (``deploy/argocd/values-argocd.yaml``) and the
   gitops seed (``deploy/argocd/gitops-seed``) as data — the resource sums
   must fit the argocd namespace quota of T029 and the seed must follow the
   repository rules (immutable digests only, no secrets).
"""

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
ARGOCD_DIR = REPO_ROOT / "deploy" / "argocd"
APPS_CHART_DIR = ARGOCD_DIR / "chart"
ARGOCD_VALUES = ARGOCD_DIR / "values-argocd.yaml"
GITOPS_SEED_DIR = ARGOCD_DIR / "gitops-seed"

# The argocd namespace quota/limits of the bootstrap (T029, manifests/quotas.yaml).
ARGOCD_QUOTA = {
    "requests.cpu": "400m",
    "requests.memory": "800Mi",
    "limits.cpu": "750m",
    "limits.memory": "1500Mi",
}

# Pinned by install.sh; asserted here so the pin cannot drift silently.
EXPECTED_ARGOCD_CHART_VERSION = "10.9.1"

HELM_TIMEOUT_SECONDS = 60

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm CLI is not installed")


def _helm(*args: str, expect_success: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["helm", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=HELM_TIMEOUT_SECONDS,
        check=False,
    )
    if expect_success:
        assert completed.returncode == 0, f"helm {' '.join(args)} failed:\n{completed.stderr}"
    return completed


def _render_apps_chart(*extra_args: str) -> tuple[list[dict[str, Any]], str]:
    completed = _helm("template", "dark-factory-argocd-apps", str(APPS_CHART_DIR), *extra_args)
    docs = [doc for doc in yaml.safe_load_all(completed.stdout) if doc is not None]
    return docs, completed.stdout


def _yaml_files(root: Path) -> list[Path]:
    # helm chart templates (pilot/templates/*.yaml) are not plain YAML data —
    # they are validated by rendering (see the pilot chart tests below).
    return [
        path
        for path in sorted(root.rglob("*.yaml")) + sorted(root.rglob("*.yml"))
        if "templates" not in path.parts
    ]


def _load_docs(path: Path) -> list[dict[str, Any]]:
    docs = [doc for doc in yaml.safe_load_all(path.read_text()) if doc is not None]
    assert docs, f"no YAML documents found in {path}"
    return docs


def _cpu_cores(value: str) -> float:
    """Parse a Kubernetes CPU quantity into cores."""
    return float(value[:-1]) / 1000.0 if value.endswith("m") else float(value)


def _memory_mib(value: str) -> int:
    """Parse a Kubernetes memory quantity into MiB."""
    if value.endswith("Gi"):
        return int(float(value[:-2]) * 1024)
    if value.endswith("Mi"):
        return int(value[:-2])
    raise AssertionError(f"unsupported memory quantity: {value}")


# ---------------------------------------------------------------------------
# Root Applications chart (deploy/argocd/chart)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def apps_docs() -> list[dict[str, Any]]:
    docs, _ = _render_apps_chart()
    return docs


def test_renders_exactly_two_root_applications(apps_docs: list[dict[str, Any]]) -> None:
    applications = [doc for doc in apps_docs if doc["kind"] == "Application"]
    assert len(applications) == 2
    assert {app["metadata"]["name"] for app in applications} == {"factory", "apps-dev"}
    for app in applications:
        assert app["apiVersion"] == "argoproj.io/v1alpha1"
        assert app["metadata"]["namespace"] == "argocd"
        labels = app["metadata"]["labels"]
        assert labels["app.kubernetes.io/part-of"] == "dark-factory"
        assert labels["app.kubernetes.io/managed-by"] == "dark-factory"
        assert app["spec"]["project"] == "default"
        destination = app["spec"]["destination"]
        assert destination["server"] == "https://kubernetes.default.svc"
        assert destination["namespace"] in {"factory", "argocd"}


def test_factory_application_points_to_this_repo_platform_chart(
    apps_docs: list[dict[str, Any]],
) -> None:
    app = next(doc for doc in apps_docs if doc["metadata"]["name"] == "factory")
    source = app["spec"]["source"]
    assert source["repoURL"] == "https://github.com/vadagama/dark-factory-mvp.git"
    assert source["path"] == "charts/dark-factory"
    assert source["targetRevision"] == "main"
    assert source["helm"]["valueFiles"] == ["values-local.yaml"]
    assert app["spec"]["destination"]["namespace"] == "factory"


def test_apps_dev_application_is_gitops_app_of_apps(apps_docs: list[dict[str, Any]]) -> None:
    app = next(doc for doc in apps_docs if doc["metadata"]["name"] == "apps-dev")
    source = app["spec"]["source"]
    assert source["repoURL"] == "https://github.com/vadagama/dark-factory-gitops.git"
    assert source["path"] == "envs/dev"
    assert source["targetRevision"] == "main"
    # The app-of-apps creates child Application CRs next to itself.
    assert app["spec"]["destination"]["namespace"] == "argocd"


def test_both_root_applications_sync_automatically_without_prune_selfheal(
    apps_docs: list[dict[str, Any]],
) -> None:
    for app in (doc for doc in apps_docs if doc["kind"] == "Application"):
        automated = app["spec"]["syncPolicy"]["automated"]
        assert automated["prune"] is False
        assert automated["selfHeal"] is False


def test_applications_chart_renders_stable_and_has_no_secrets_or_floating_tags() -> None:
    # Idempotency: two consecutive renders must be byte-identical.
    first_docs, first_stdout = _render_apps_chart()
    _, second_stdout = _render_apps_chart()
    assert first_stdout == second_stdout, "helm template output must be stable"
    kinds = {doc["kind"] for doc in first_docs}
    assert "Secret" not in kinds
    assert "ServiceAccount" not in kinds
    assert ":latest" not in first_stdout


# ---------------------------------------------------------------------------
# Argo CD install values (deploy/argocd/values-argocd.yaml)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def argocd_values() -> dict[str, Any]:
    values: dict[str, Any] = yaml.safe_load(ARGOCD_VALUES.read_text())
    return values


def test_argocd_values_fit_argocd_quota(argocd_values: dict[str, Any]) -> None:
    # Sum the container resources of every component deployed by the chart
    # (controller, repoServer, server, redis, applicationSet and the transient
    # redisSecretInit hook job); dex/notifications/commitServer are disabled.
    component_keys = [
        "controller",
        "repoServer",
        "server",
        "redis",
        "applicationSet",
        "redisSecretInit",
    ]
    totals = {"requests.cpu": 0.0, "requests.memory": 0, "limits.cpu": 0.0, "limits.memory": 0}
    for key in component_keys:
        resources = argocd_values[key]["resources"]
        for kind in ("requests", "limits"):
            totals[f"{kind}.cpu"] += _cpu_cores(resources[kind]["cpu"])
            totals[f"{kind}.memory"] += _memory_mib(resources[kind]["memory"])
    quota_cpu = _cpu_cores(ARGOCD_QUOTA["requests.cpu"])
    quota_memory = _memory_mib(ARGOCD_QUOTA["requests.memory"])
    limits_cpu = _cpu_cores(ARGOCD_QUOTA["limits.cpu"])
    limits_memory = _memory_mib(ARGOCD_QUOTA["limits.memory"])
    assert totals["requests.cpu"] <= quota_cpu
    assert totals["requests.memory"] <= quota_memory
    assert totals["limits.cpu"] <= limits_cpu
    assert totals["limits.memory"] <= limits_memory


def test_argocd_values_disable_optional_components(argocd_values: dict[str, Any]) -> None:
    assert argocd_values["dex"]["enabled"] is False
    assert argocd_values["notifications"]["enabled"] is False
    assert argocd_values["commitServer"]["enabled"] is False


def test_argocd_values_disable_redis_automount_and_chart_networkpolicies(
    argocd_values: dict[str, Any],
) -> None:
    # NetworkPolicy ownership stays with the bootstrap (T029).
    assert argocd_values["global"]["networkPolicy"]["create"] is False
    assert argocd_values["redis"]["automountServiceAccountToken"] is False
    assert argocd_values["redis"]["serviceAccount"]["automountServiceAccountToken"] is False
    # The components that talk to the Kubernetes API keep their tokens.
    for key in ("controller", "repoServer", "server", "applicationSet"):
        assert argocd_values[key]["automountServiceAccountToken"] is True


def test_argocd_values_render_with_the_pinned_upstream_chart() -> None:
    # Full render of the upstream chart with our values — validates the value
    # keys against the pinned chart version. Offline machines (no chart cache)
    # skip; a reachable registry makes render failures hard assertions.
    repo_add = subprocess.run(
        ["helm", "repo", "add", "argo", "https://argoproj.github.io/argo-helm", "--force-update"],
        capture_output=True,
        text=True,
        timeout=HELM_TIMEOUT_SECONDS,
        check=False,
    )
    if repo_add.returncode != 0:
        pytest.skip(f"argo helm repository is unreachable: {repo_add.stderr.strip()}")
    # Pull into a temp dir: the probe must not drop the chart tgz into the repo.
    with tempfile.TemporaryDirectory() as tmp_dir:
        pull = subprocess.run(
            [
                "helm",
                "pull",
                "argo/argo-cd",
                "--version",
                EXPECTED_ARGOCD_CHART_VERSION,
                "--destination",
                tmp_dir,
            ],
            capture_output=True,
            text=True,
            timeout=HELM_TIMEOUT_SECONDS,
            check=False,
        )
    if pull.returncode != 0:
        pytest.skip(
            f"argo-cd chart {EXPECTED_ARGOCD_CHART_VERSION} unavailable: {pull.stderr.strip()}"
        )
    # helm template defaults to kube 1.20 in helm v4, below the chart's
    # kubeVersion floor (>=1.25): render against a realistic local cluster.
    completed = _helm(
        "template",
        "argocd",
        "argo/argo-cd",
        "--version",
        EXPECTED_ARGOCD_CHART_VERSION,
        "--namespace",
        "argocd",
        "--kube-version",
        "1.30.0",
        "-f",
        str(ARGOCD_VALUES),
    )
    # dex/notifications disabled values still leak TLS-volume and ConfigMap
    # mentions into the render, so assert on workload names instead of raw text.
    workloads = {
        doc["metadata"]["name"]
        for doc in yaml.safe_load_all(completed.stdout)
        if doc and doc.get("kind") in ("Deployment", "StatefulSet")
    }
    disabled = ("argocd-dex-server", "argocd-notifications-controller", "argocd-commit-server")
    for workload in disabled:
        assert workload not in workloads
    assert "argocd-application-controller" in workloads


def test_install_sh_pins_the_expected_chart_version() -> None:
    script = (ARGOCD_DIR / "install.sh").read_text()
    assert f'ARGOCD_CHART_VERSION="{EXPECTED_ARGOCD_CHART_VERSION}"' in script


# ---------------------------------------------------------------------------
# GitOps seed (deploy/argocd/gitops-seed)
# ---------------------------------------------------------------------------


def test_seed_yaml_files_contain_no_secrets() -> None:
    for path in _yaml_files(GITOPS_SEED_DIR):
        for doc in _load_docs(path):
            assert doc.get("kind") != "Secret"
            for key in ("password", "secret", "token", "privateKey"):
                assert key not in doc.get("metadata", {}).get("annotations", {})
                assert key not in doc.get("stringData", {})


def test_seed_child_application_follows_the_repository_rules() -> None:
    docs = _load_docs(GITOPS_SEED_DIR / "envs" / "dev" / "apps.yaml")
    apps = [doc for doc in docs if doc["kind"] == "Application"]
    assert apps, "envs/dev/apps.yaml must declare at least one child Application"
    pilot = next(doc for doc in apps if doc["metadata"]["name"] == "pilot-dev")
    assert pilot["metadata"]["namespace"] == "argocd"
    assert pilot["metadata"]["labels"]["app.kubernetes.io/managed-by"] == "dark-factory-gitops"
    source = pilot["spec"]["source"]
    assert source["repoURL"] == "https://github.com/vadagama/dark-factory-gitops.git"
    assert source["path"] == "envs/dev/pilot"
    assert source["targetRevision"] == "main"
    assert pilot["spec"]["destination"]["namespace"] == "apps-dev"
    automated = pilot["spec"]["syncPolicy"]["automated"]
    assert automated["prune"] is False
    assert automated["selfHeal"] is False


def test_seed_pilot_chart_pins_only_a_digest() -> None:
    values: dict[str, Any] = yaml.safe_load(
        (GITOPS_SEED_DIR / "envs" / "dev" / "pilot" / "values.yaml").read_text()
    )
    digest = values["image"]["digest"]
    assert digest.startswith("sha256:"), "the seed must reference an immutable digest"
    assert "latest" not in digest
    # No tag field at all: the digest is the only release variable.
    assert "tag" not in values["image"]


def test_seed_pilot_chart_renders_a_digest_pinned_deployment() -> None:
    pilot_chart = GITOPS_SEED_DIR / "envs" / "dev" / "pilot"
    completed = _helm("template", "pilot-dev", str(pilot_chart))
    docs = [doc for doc in yaml.safe_load_all(completed.stdout) if doc is not None]
    deployments = [doc for doc in docs if doc["kind"] == "Deployment"]
    assert len(deployments) == 1
    deployment = deployments[0]
    assert "namespace" not in deployment["metadata"]  # set by Argo CD (apps-dev)
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert container["image"] == "busybox@sha256:__PILOT_IMAGE_DIGEST__"
    pod_spec = deployment["spec"]["template"]["spec"]
    assert pod_spec["automountServiceAccountToken"] is False
    assert pod_spec["securityContext"]["runAsNonRoot"] is True
    assert ":latest" not in completed.stdout
    assert not [doc for doc in docs if doc["kind"] == "Secret"]


def test_seed_pilot_chart_requires_a_digest() -> None:
    # Removing the digest must fail the render (fail-closed against a
    # tag-less/floating state sneaking back in).
    pilot_chart = GITOPS_SEED_DIR / "envs" / "dev" / "pilot"
    completed = _helm(
        "template",
        "pilot-dev",
        str(pilot_chart),
        "--set",
        "image.digest=",
        expect_success=False,
    )
    assert completed.returncode != 0


def test_seed_rules_document_immutable_digests_and_mr_only_flow() -> None:
    readme = (GITOPS_SEED_DIR / "README.md").read_text()
    assert "sha256" in readme
    assert "latest" in readme
    assert "gh repo create" in readme
    assert "секрет" in readme.lower()


def test_argocd_scripts_pass_bash_syntax_check() -> None:
    bash = shutil.which("bash")
    assert bash is not None
    for script in sorted(ARGOCD_DIR.glob("*.sh")) + sorted((ARGOCD_DIR / "scripts").glob("*.sh")):
        completed = subprocess.run(
            [bash, "-n", str(script)],
            capture_output=True,
            text=True,
            timeout=HELM_TIMEOUT_SECONDS,
            check=False,
        )
        assert completed.returncode == 0, f"bash -n {script.name} failed:\n{completed.stderr}"
