"""Contract tests for CIPort (T-024, ADR-019 p.3)."""

import asyncio
from collections.abc import Callable

import pytest

from dark_factory.ports import (
    ArtifactRef,
    CIPort,
    GateResult,
    GateStatus,
    RepositoryRef,
    StageJobRequest,
)

REF = "abc1234"


def _job_request(repository: RepositoryRef) -> StageJobRequest:
    return StageJobRequest(repository=repository, stage="construction", ref=REF)


def test_adapter_satisfies_protocol(ci_port: CIPort) -> None:
    assert isinstance(ci_port, CIPort)
    assert not isinstance(object(), CIPort)


def test_run_stage_job_returns_job_ref(ci_port: CIPort, repository: RepositoryRef) -> None:
    job_ref = asyncio.run(ci_port.run_stage_job(_job_request(repository), idempotency_key="k1"))
    assert job_ref


def test_run_stage_job_replays_same_key_to_same_ref(
    ci_port: CIPort, stage_job_journal: Callable[[], int], repository: RepositoryRef
) -> None:
    first = asyncio.run(ci_port.run_stage_job(_job_request(repository), idempotency_key="k1"))
    second = asyncio.run(ci_port.run_stage_job(_job_request(repository), idempotency_key="k1"))
    assert second == first
    assert stage_job_journal() == 1


def test_run_stage_job_new_key_dispatches_new_job(
    ci_port: CIPort, stage_job_journal: Callable[[], int], repository: RepositoryRef
) -> None:
    first = asyncio.run(ci_port.run_stage_job(_job_request(repository), idempotency_key="k1"))
    second = asyncio.run(ci_port.run_stage_job(_job_request(repository), idempotency_key="k2"))
    assert second != first
    assert stage_job_journal() == 2


def test_gate_status_is_pending_before_checks_report(
    ci_port: CIPort, repository: RepositoryRef
) -> None:
    job_ref = asyncio.run(ci_port.run_stage_job(_job_request(repository), idempotency_key="k1"))
    result = asyncio.run(ci_port.gate_status(job_ref))
    assert isinstance(result, GateResult)
    assert result.status is GateStatus.PENDING
    assert result.sha == REF


def test_gate_status_reports_seeded_outcome(
    ci_port: CIPort, seed_gate: Callable[..., None], repository: RepositoryRef
) -> None:
    job_ref = asyncio.run(ci_port.run_stage_job(_job_request(repository), idempotency_key="k1"))
    seed_gate(job_ref, status=GateStatus.PASSED, summary="2 checks passed")
    result = asyncio.run(ci_port.gate_status(job_ref))
    assert result.status is GateStatus.PASSED
    assert result.summary == "2 checks passed"


def test_gate_status_of_unknown_job_ref_fails(ci_port: CIPort) -> None:
    with pytest.raises(KeyError):
        asyncio.run(ci_port.gate_status("github:small/pilot:construction:999"))


def test_artifacts_are_empty_until_seeded(ci_port: CIPort, repository: RepositoryRef) -> None:
    job_ref = asyncio.run(ci_port.run_stage_job(_job_request(repository), idempotency_key="k1"))
    assert asyncio.run(ci_port.artifacts(job_ref)) == []


def test_artifacts_return_seeded_refs(
    ci_port: CIPort, seed_artifacts: Callable[..., None], repository: RepositoryRef
) -> None:
    job_ref = asyncio.run(ci_port.run_stage_job(_job_request(repository), idempotency_key="k1"))
    seeded = ArtifactRef(
        artifact_type="pytest-report", uri="https://ci.test/artifacts/pytest-report"
    )
    seed_artifacts(job_ref, (seeded,))
    produced = asyncio.run(ci_port.artifacts(job_ref))
    assert [(artifact.artifact_type, artifact.uri) for artifact in produced] == [
        ("pytest-report", "https://ci.test/artifacts/pytest-report")
    ]
