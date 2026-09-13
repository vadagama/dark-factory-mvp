"""Contract tests for TelemetryPort."""

from collections.abc import Callable

from dark_factory.ports import Span, TelemetryPort, Usage


def test_adapter_satisfies_protocol(telemetry_port: TelemetryPort) -> None:
    assert isinstance(telemetry_port, TelemetryPort)


def test_span_records_name_and_attributes(
    telemetry_port: TelemetryPort,
    recorded_spans: Callable[[], tuple[Span, ...]],
) -> None:
    with telemetry_port.span("stage.run", change_id="chg-001", stage="construction") as span:
        assert isinstance(span, Span)
        assert span.name == "stage.run"
        assert span.attributes == {"change_id": "chg-001", "stage": "construction"}
    assert recorded_spans() == (span,)


def test_record_usage_records_usage_and_attributes(
    telemetry_port: TelemetryPort,
    recorded_usage: Callable[[], tuple[tuple[Usage, dict[str, str]], ...]],
) -> None:
    usage = Usage(prompt_tokens=10, completion_tokens=20, total_tokens=30)
    telemetry_port.record_usage(usage, change_id="chg-001")
    assert recorded_usage() == ((usage, {"change_id": "chg-001"}),)
