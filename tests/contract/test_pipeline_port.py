"""Contract tests for PipelinePort."""

import asyncio

from dark_factory.ports import PipelinePort, PipelineStatus, RepositoryRef

_ALLOWED = {"queued", "in_progress", "success", "failure", "canceled"}


def test_adapter_satisfies_protocol(pipeline_port: PipelinePort, repository: RepositoryRef) -> None:
    assert isinstance(pipeline_port, PipelinePort)


def test_status_reports_provider_neutral_state(
    pipeline_port: PipelinePort, repository: RepositoryRef
) -> None:
    status = asyncio.run(pipeline_port.status(repository, "feat/x"))
    assert isinstance(status, PipelineStatus)
    assert status.ref == "feat/x"
    assert status.status in _ALLOWED
