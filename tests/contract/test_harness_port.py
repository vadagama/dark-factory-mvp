"""Contract tests for HarnessPort."""

import asyncio

from dark_factory.ports import (
    AgentResult,
    HarnessPort,
    HealthStatus,
    Role,
    Stage,
    TaskEnvelope,
)


def _envelope(instruction: str = "implement the feature") -> TaskEnvelope:
    return TaskEnvelope(
        change_id="chg-001",
        run_id="run-001",
        stage=Stage.CONSTRUCTION,
        role=Role.DEVELOP,
        instruction=instruction,
    )


def test_adapter_satisfies_protocol(harness_port: HarnessPort) -> None:
    assert isinstance(harness_port, HarnessPort)


def test_run_stage_returns_versioned_result(harness_port: HarnessPort) -> None:
    result = asyncio.run(harness_port.run_stage(_envelope()))
    assert isinstance(result, AgentResult)
    assert result.schema_version == 1
    assert result.ok is True
    assert result.output != ""


def test_run_stage_is_deterministic(harness_port: HarnessPort) -> None:
    first = asyncio.run(harness_port.run_stage(_envelope()))
    second = asyncio.run(harness_port.run_stage(_envelope()))
    assert second == first


def test_health_reports_healthy(harness_port: HarnessPort) -> None:
    health = asyncio.run(harness_port.health())
    assert isinstance(health, HealthStatus)
    assert health.healthy is True
