"""Structural checks for the CI runner verification tooling (T030, TD-001).

``deploy/ci/verify.sh`` runs against a LIVE cluster, so most of its failure
modes are only observable there. This module pins the parts that can be
validated statically:

1. the script passes ``bash -n`` (and ``shellcheck`` when available), and it
   carries the NetworkPolicy-enforcement canary (ported from the bootstrap
   ``negative-egress-test.sh`` per TD-001's remediation plan);
2. the probe manifest (``manifests/agent-job-probe.yaml``) is valid YAML with
   the placeholders verify.sh renders, and its ``NET_NEGATIVE_MODE`` switch
   marks the network negative checks as ``SKIPPED`` on clusters without
   policy enforcement;
3. the securityContext blocks of the probe stay in sync with
   ``manifests/agent-job-template.yaml`` — the sync note in both files says
   the blocks are kept equal manually, so a drift must fail a test, not a
   live run.
"""

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CI_DIR = REPO_ROOT / "deploy" / "ci"
VERIFY_SCRIPT = CI_DIR / "verify.sh"
PROBE_MANIFEST = CI_DIR / "manifests" / "agent-job-probe.yaml"
JOB_TEMPLATE = CI_DIR / "manifests" / "agent-job-template.yaml"

# The network negative checks that degrade to SKIPPED when the cluster does
# not enforce NetworkPolicies (TD-001). Positive and credential checks must
# stay hard in both modes.
NET_NEGATIVE_CHECKS = ("api_blocked", "api_ip_blocked", "http80_blocked")

CANARY_IMAGE_DIGEST = (
    "busybox@sha256:9db7b59979c38555a39def84a31fb98b5296952f9e3afd4f6f11f05b07adfab0"
)


def _verify_text() -> str:
    return VERIFY_SCRIPT.read_text(encoding="utf-8")


def _probe() -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load(PROBE_MANIFEST.read_text(encoding="utf-8"))
    return loaded


def _job_template() -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load(JOB_TEMPLATE.read_text(encoding="utf-8"))
    return loaded


def test_verify_script_passes_bash_syntax_check() -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is not installed")
    result = subprocess.run(
        [bash, "-n", str(VERIFY_SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_verify_script_shellcheck() -> None:
    shellcheck = shutil.which("shellcheck")
    if shellcheck is None:
        pytest.skip("shellcheck is not installed")
    result = subprocess.run(
        [shellcheck, str(VERIFY_SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout


def test_verify_script_carries_the_enforcement_canary() -> None:
    text = _verify_text()
    # The canary pair mechanics ported from negative-egress-test.sh.
    assert "detect_policy_enforcement" in text
    assert "factory-policy-canary" in text
    assert "egress-canary" in text
    # The canary must reuse the same digest-pinned image as the probe so the
    # canary adds no new supply-chain surface.
    assert CANARY_IMAGE_DIGEST in text
    # Both modes must be renderable: assert on an enforcing cluster, skip
    # otherwise (unknown enforcement degrades to skip, as in the bootstrap
    # negative-egress-test.sh).
    assert 'net_mode="skip"' in text
    assert 'if [[ "$POLICY_ENFORCEMENT" == "on" ]]; then net_mode="assert"; fi' in text
    assert "s/__NET_NEGATIVE_MODE__/$net_mode/" in text


def test_probe_manifest_is_valid_yaml_with_render_placeholders() -> None:
    probe = _probe()
    assert probe["apiVersion"] == "batch/v1"
    assert probe["kind"] == "Job"
    spec = probe["spec"]
    assert spec["ttlSecondsAfterFinished"] == "__TTL_SECONDS__"
    assert spec["activeDeadlineSeconds"] == "__DEADLINE_SECONDS__"
    assert probe["metadata"]["name"] == "__JOB_NAME__"
    container = spec["template"]["spec"]["containers"][0]
    env = {entry["name"]: entry["value"] for entry in container["env"]}
    assert env["NET_NEGATIVE_MODE"] == "__NET_NEGATIVE_MODE__"
    args = container["args"][0]
    assert "__KUBERNETES_CLUSTER_IP__" in args
    # The in-pod script must print exactly one PROBE_RESULT line per check.
    for check in (
        "uid_65532",
        "rootfs_readonly",
        "tmp_writable",
        "no_sa_token",
        "env_clean",
        "dns",
        "external_https",
        *NET_NEGATIVE_CHECKS,
    ):
        assert args.count(f"res {check} ") >= 1, f"missing check {check}"


def test_probe_network_negative_checks_degrade_to_skipped() -> None:
    args = _probe()["spec"]["template"]["spec"]["containers"][0]["args"][0]
    for check in NET_NEGATIVE_CHECKS:
        # The skip branch prints SKIPPED and does not touch `fail`.
        assert f"res {check} SKIPPED" in args, f"{check} has no SKIPPED branch"
        # The assert branch keeps the hard failure.
        assert f"res {check} FAIL; fail=1" in args, f"{check} lost its assert branch"
    # Positive and credential checks must NOT learn to skip: their only
    # outcomes are PASS or FAIL with fail=1.
    for check in ("no_sa_token", "env_clean", "dns", "external_https"):
        assert f"res {check} SKIPPED" not in args


def test_probe_securitycontext_matches_agent_job_template() -> None:
    """The sync note in both manifests keeps the two blocks equal by hand.

    A drift between the probe (what verify.sh tests) and the template (what
    agent jobs actually run) would make the verification green while the real
    posture differs — so the equality is pinned here.
    """
    probe_pod = _probe()["spec"]["template"]["spec"]
    template_pod = _job_template()["spec"]["template"]["spec"]
    assert probe_pod["securityContext"] == template_pod["securityContext"]
    assert (
        probe_pod["automountServiceAccountToken"] == (template_pod["automountServiceAccountToken"])
    )

    def container_ctx(pod: dict[str, Any]) -> dict[str, Any]:
        ctx: dict[str, Any] = pod["containers"][0]["securityContext"]
        return ctx

    assert container_ctx(probe_pod) == container_ctx(template_pod)
