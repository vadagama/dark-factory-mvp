"""``factory release verify``: smoke + release evidence of a deployed digest
(T034, US5, ADR-011 p.6).

The command is the CLI face of the release verification: it checks the digest
immutability (FR-011 — the expected digest of the promoted build equals the
digest observed on the deployment), the Argo Application sync/health (ADR-010)
and runs the smoke probes (FR-013) behind the ``quality.release`` seams, then
prints the decision — ``released`` only when every check passed. Any failure
carries the rollback signal for Operations (docs T-045): revert the GitOps
commit that pinned the digest and Argo CD auto-sync restores the previous one
(ADR-010, ADR-011 p.6).

Inputs (all validated before any probe runs — invalid configuration exits 2
without touching the deployment):

- expected digest: ``--expected-digest`` directly, or ``--digest-json`` with
  the path of the T033 ``image-digest.json`` artifact (schema v1; only the
  ``digest`` field is read, the rest is not interpreted);
- target: ``--application`` (``namespace/name`` of the Argo Application,
  recorded in the evidence);
- observed state: ``--observed-digest``, ``--argo-sync``, ``--argo-health``
  passed as values. The MVP has no live Argo/Kubernetes client (YAGNI): the
  caller — the CI job or the operator script — reads the Application status
  and passes it in; a live reader is a later task behind the same seams;
- smoke: ``--smoke-url`` (HTTP health probe, any 2xx passes) and optionally
  ``--smoke-digest-url`` with ``--smoke-digest-header`` (digest probe binding
  the running workload to the expected digest, FR-011/T033). Without smoke
  options the verification fails with ``smoke was not run`` — fail-closed.

With ``--evidence-dir`` (together with ``--change``), the command also
persists the run record with the ``release`` evidence section (ADR-015 p.4):
the digest, the raw Argo statuses, the smoke results, the decision and its
rollback signal. The change snapshot fixes what was verified; the manifest
resolves the exact commits from the environment (``DARK_FACTORY_COMMIT`` /
``DARK_FACTORY_PRODUCT_COMMIT`` / ``GITHUB_SHA`` / git HEAD, ADR-015 p.5).

Exit codes (contract cli.md): 0 when the decision is ``released``, 1 when it
is ``release_failed`` (CI must go red; the diagnostics name the failed check
and the rollback signal), 2 for invalid input/configuration. Probe URLs and
the passed state values are never echoed in diagnostics beyond what the
caller passed explicitly (ADR-009).
"""

import asyncio
import json
import os
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Final
from urllib.parse import urlparse

from dark_factory.changes.enums import ReleaseStatus
from dark_factory.changes.run_records import ReleaseEvidence, RunManifest
from dark_factory.cli._common import (
    os_error_reason,
    report_execution_error,
    report_invalid_input,
)
from dark_factory.cli.main import EXIT_ERROR, EXIT_OK, ReleaseVerifyArgs
from dark_factory.cli.run_records import (
    RunRecordError,
    build_release_run_record,
    collect_run_manifest,
    persist_run_record,
)
from dark_factory.cli.stage import (
    ChangeSnapshot,
    InvalidStageInput,
    generate_run_id,
    load_change_snapshot,
)
from dark_factory.quality.release import (
    HttpDigestProbe,
    HttpHealthProbe,
    ReleaseObservation,
    SmokeProbe,
    build_release_evidence,
    evaluate_release,
    pre_smoke_failure,
    run_smoke_probes,
)

_COMMAND: Final[str] = "release verify"
"""Subcommand name of the error reports (``factory release verify: ...``)."""

DIGEST_FIELD: str = "digest"
"""Field of the T033 ``image-digest.json`` artifact the expected digest is read from."""


class ReleaseVerifyError(ValueError):
    """Invalid command input or configuration; nothing was verified (exit 2)."""


@dataclass(frozen=True, slots=True)
class _ReleaseInputs:
    """Validated inputs of one verification, resolved before any probe runs."""

    expected_digest: str
    smoke_url: str | None
    smoke_digest_url: str | None
    smoke_digest_header: str | None
    evidence_dir: str | None
    change_snapshot: ChangeSnapshot | None
    manifest: RunManifest | None
    run_id: str


def render_text(evidence: ReleaseEvidence, evidence_path: str | None) -> str:
    """Human-readable decision report: status, target state, smoke, rollback, evidence."""
    lines = [f"factory release verify: {evidence.decision.value}"]
    if evidence.application is not None:
        lines.append(f"application: {evidence.application}")
    lines.append(
        f"argo: sync={evidence.argo_sync_status or 'unknown'}"
        f" health={evidence.argo_health_status or 'unknown'}"
    )
    lines.append(f"smoke: {_smoke_tally(evidence)}")
    if evidence.reason is not None:
        lines.append(f"reason: {evidence.reason}")
    if evidence.rollback_signal is not None:
        lines.append(f"rollback: {evidence.rollback_signal}")
    if evidence_path is not None:
        lines.append(f"evidence: {evidence_path}")
    return "\n".join(lines)


def _smoke_tally(evidence: ReleaseEvidence) -> str:
    """One-line smoke tally: aggregate status plus per-probe outcomes."""
    if not evidence.smoke:
        return "not_run"
    per_probe = ", ".join(
        f"{probe.name}={'passed' if probe.passed else 'failed'}" for probe in evidence.smoke
    )
    aggregate = "failed" if any(not probe.passed for probe in evidence.smoke) else "passed"
    return f"{aggregate} ({per_probe})"


def render_json(evidence: ReleaseEvidence) -> str:
    """Stable JSON of the release evidence (``--json``)."""
    return json.dumps(evidence.model_dump(mode="json"))


def run_release_verify_command(args: ReleaseVerifyArgs) -> int:
    """Handle ``factory release verify``; return the process exit code (T034).

    All input validation happens before any probe runs (fail fast, exit 2).
    The digest (FR-011) and Argo (ADR-010) checks are evaluated first: when
    one fails, the release stops before any probe fires — no smoke request
    is sent to a deployment that is not the promoted one. Otherwise the
    probes run behind the ``SmokeProbe`` seam, the deterministic core
    decides on the complete observation, and the evidence is persisted (when
    configured) and printed.
    """
    try:
        inputs = _resolve_inputs(args)
    except (ReleaseVerifyError, InvalidStageInput, RunRecordError) as exc:
        return report_invalid_input(_COMMAND, str(exc), json_output=args.json_output)

    observation = ReleaseObservation(
        expected_digest=inputs.expected_digest,
        observed_digest=args.observed_digest,
        argo_sync_raw=args.argo_sync,
        argo_health_raw=args.argo_health,
    )
    decision = pre_smoke_failure(observation)
    if decision is None:
        probes = _build_probes(inputs)
        if probes:
            smoke = asyncio.run(run_smoke_probes(probes)).probes
            observation = replace(observation, smoke=smoke)
        decision = evaluate_release(observation)
    evidence = build_release_evidence(
        observation,
        decision,
        verified_at=datetime.now(UTC),
        application=args.application,
    )

    evidence_path: str | None = None
    if inputs.evidence_dir is not None:
        # The pairing is validated in _resolve_inputs; the explicit guard keeps
        # the invariant enforced for real (an assert vanishes under python -O).
        if inputs.change_snapshot is None or inputs.manifest is None:
            raise RuntimeError(
                "release verification is missing its evidence inputs:"
                " --evidence-dir and --change are paired in _resolve_inputs"
            )
        record = build_release_run_record(
            change=inputs.change_snapshot.change,
            run_id=inputs.run_id,
            evidence=evidence,
            manifest=inputs.manifest,
        )
        try:
            evidence_path = str(persist_run_record(Path(inputs.evidence_dir), record))
        except OSError as exc:
            # The evidence must not be lost silently: report and go red (exit 1).
            return report_execution_error(
                _COMMAND,
                f"cannot persist the run record: {os_error_reason(exc)}",
                json_output=args.json_output,
            )
    return _emit(evidence, evidence_path, json_output=args.json_output)


def _emit(evidence: ReleaseEvidence, evidence_path: str | None, *, json_output: bool) -> int:
    """Print the evidence in the requested format; ``released`` exits 0."""
    if json_output:
        print(render_json(evidence))
    else:
        print(render_text(evidence, evidence_path))
    return EXIT_OK if evidence.decision is ReleaseStatus.RELEASED else EXIT_ERROR


def _resolve_inputs(args: ReleaseVerifyArgs) -> _ReleaseInputs:
    """Validate every option and resolve the expected digest (no probes yet).

    Raises :class:`ReleaseVerifyError` (or ``InvalidStageInput`` /
    ``RunRecordError`` for the evidence inputs) without echoing any URL or
    state value (ADR-009).
    """
    expected_digest = _resolve_expected_digest(args)
    _validate_smoke_options(args)
    if (args.evidence_dir is None) != (args.change is None):
        raise ReleaseVerifyError(
            "--evidence-dir and --change must be provided together:"
            " the run record indexes the change it verifies"
        )

    change_snapshot: ChangeSnapshot | None = None
    manifest: RunManifest | None = None
    if args.evidence_dir is not None and args.change is not None:
        change_snapshot = load_change_snapshot(args.change)
        manifest = collect_run_manifest(os.environ)

    return _ReleaseInputs(
        expected_digest=expected_digest,
        smoke_url=args.smoke_url,
        smoke_digest_url=args.smoke_digest_url,
        smoke_digest_header=args.smoke_digest_header,
        evidence_dir=args.evidence_dir,
        change_snapshot=change_snapshot,
        manifest=manifest,
        run_id=_resolve_run_id(args.run_id),
    )


def resolve_expected_digest(*, expected_digest: str | None, digest_json: str | None) -> str:
    """Expected digest from the flag or the T033 artifact; exactly one source.

    Shared by ``factory release verify`` (T034) and the release options of
    ``factory run advance`` (T-092 S4). Raises :class:`ReleaseVerifyError`
    naming the violated rule, never echoing a value (ADR-009).
    """
    if expected_digest is not None and digest_json is not None:
        raise ReleaseVerifyError("--expected-digest and --digest-json are mutually exclusive")
    if expected_digest is not None:
        value = expected_digest.strip()
        if not value:
            raise ReleaseVerifyError("--expected-digest must be a non-empty string")
        return value
    if digest_json is not None:
        return _expected_digest_from_json(digest_json)
    raise ReleaseVerifyError(
        "the expected digest is required: pass --expected-digest or --digest-json"
    )


def _resolve_expected_digest(args: ReleaseVerifyArgs) -> str:
    """Expected digest of ``release verify``: the shared rule over its flags."""
    return resolve_expected_digest(
        expected_digest=args.expected_digest, digest_json=args.digest_json
    )


def _expected_digest_from_json(path: str) -> str:
    """Read the expected digest from the T033 ``image-digest.json`` artifact.

    Only the ``digest`` field is interpreted: the artifact may gain fields
    without breaking this reader (schema-version policing is the CI
    producer's duty).
    """
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise ReleaseVerifyError(
            f"cannot read the image-digest artifact: {os_error_reason(exc)}"
        ) from exc
    except json.JSONDecodeError:
        raise ReleaseVerifyError("the image-digest artifact is not valid JSON") from None
    digest = payload.get(DIGEST_FIELD) if isinstance(payload, dict) else None
    if not isinstance(digest, str) or not digest.strip():
        raise ReleaseVerifyError(
            "the image-digest artifact must be a JSON object with a non-empty 'digest' field"
        )
    return digest.strip()


def validate_smoke_options(
    *, smoke_url: str | None, smoke_digest_url: str | None, smoke_digest_header: str | None
) -> None:
    """Consistency of the smoke flags; the health probe is the base of the set.

    Shared by ``factory release verify`` (T034) and the release options of
    ``factory run advance`` (T-092 S4). Raises :class:`ReleaseVerifyError`
    without echoing any URL (ADR-009).
    """
    if smoke_url is None:
        if smoke_digest_url is not None:
            raise ReleaseVerifyError(
                "--smoke-digest-url requires --smoke-url:"
                " the health probe is the base of the smoke set"
            )
        if smoke_digest_header is not None:
            raise ReleaseVerifyError("--smoke-digest-header requires --smoke-digest-url")
        return
    _validated_smoke_url(smoke_url)
    if smoke_digest_url is not None:
        _validated_smoke_url(smoke_digest_url)


def _validate_smoke_options(args: ReleaseVerifyArgs) -> None:
    """Consistency of the ``release verify`` smoke flags: the shared rule."""
    validate_smoke_options(
        smoke_url=args.smoke_url,
        smoke_digest_url=args.smoke_digest_url,
        smoke_digest_header=args.smoke_digest_header,
    )


def _validated_smoke_url(value: str) -> str:
    """Smoke URL restricted to http(s); the value itself is never echoed (ADR-009)."""
    if urlparse(value).scheme.lower() not in ("http", "https"):
        raise ReleaseVerifyError("smoke probe URLs must be http(s) URLs")
    return value


def _build_probes(inputs: _ReleaseInputs) -> list[SmokeProbe]:
    """MVP probe set from the validated options; empty means no smoke at all."""
    probes: list[SmokeProbe] = []
    if inputs.smoke_url is not None:
        probes.append(HttpHealthProbe(inputs.smoke_url))
        if inputs.smoke_digest_url is not None:
            probes.append(
                HttpDigestProbe(
                    inputs.smoke_digest_url,
                    inputs.expected_digest,
                    header=inputs.smoke_digest_header,
                )
            )
    return probes


def _resolve_run_id(value: str | None) -> str:
    """Explicit ``--run-id`` or a generated ``run_<uuid4 hex>``."""
    if value is None:
        return generate_run_id()
    stripped = value.strip()
    if not stripped:
        raise ReleaseVerifyError("--run-id must be a non-empty string")
    return stripped
