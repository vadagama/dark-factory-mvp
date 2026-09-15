"""Unit tests of the OTLP telemetry adapter (T-060, ADR-009).

The contract suite binds the adapter to ``TelemetryPort``; these tests pin what
is specific to OpenTelemetry behind that port: the trace one deployment can be
reconstructed from (the T-060 DoD), usage/cost correlation, exception capture,
the export policy and its fail-closed configuration, the JSON-lines artifact,
and the guarantee that a broken sink degrades telemetry instead of the run.
"""

import json
from collections.abc import Sequence
from contextlib import ExitStack
from decimal import Decimal
from pathlib import Path

import pytest
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from dark_factory.adapters.telemetry import (
    DEFAULT_SERVICE_NAME,
    EXPORTER_ENV_VAR,
    FILE_ENV_VAR,
    SERVICE_NAME_ENV_VAR,
    USAGE_ATTRIBUTE_PREFIX,
    USAGE_SPAN_NAME,
    JsonLinesSpanExporter,
    OtlpTelemetryAdapter,
    SafeSpanExporter,
    TelemetryConfig,
    TelemetryExporter,
)
from dark_factory.ports import Span, TelemetryPort, Usage

SERVICE_NAME = "dark-factory-pilot"

# The correlation chain the trace must restore (T-060 DoD).
CHAIN = ("change", "run", "stage", "agent", "tool", "ci_job", "deployment")

SECRET = "hunter2-do-not-export"


def _adapter(
    *, exporter: SpanExporter | None = None, service_name: str = SERVICE_NAME
) -> tuple[OtlpTelemetryAdapter, InMemorySpanExporter]:
    """An adapter over a fresh in-memory exporter, which doubles as its journal."""
    memory = InMemorySpanExporter()
    config = TelemetryConfig(service_name=service_name)
    return OtlpTelemetryAdapter(config, exporter=exporter or memory), memory


def _by_name(spans: Sequence[ReadableSpan], name: str) -> ReadableSpan:
    return next(span for span in spans if span.name == name)


def _attributes(span: ReadableSpan) -> dict[str, object]:
    """Attributes of a span as a plain mapping; OTel types them as optional."""
    return dict(span.attributes or {})


def _restore_chain(spans: Sequence[ReadableSpan]) -> list[str]:
    """Names of the innermost span and its ancestors, root first.

    Starts from the only span that no other span names as parent and walks up
    ``parent.span_id`` links, which is exactly what a trace reader must do to
    rebuild the run from the exported spans.
    """
    by_id = {span.context.span_id: span for span in spans}
    parents = {span.parent.span_id for span in spans if span.parent is not None}
    leaves = [span for span in spans if span.context.span_id not in parents]
    assert len(leaves) == 1
    names: list[str] = []
    current: ReadableSpan | None = leaves[0]
    while current is not None:
        names.append(current.name)
        parent = current.parent
        current = by_id.get(parent.span_id) if parent is not None else None
    return list(reversed(names))


def test_adapter_satisfies_port_and_yields_port_spans() -> None:
    adapter, _ = _adapter()
    assert isinstance(adapter, TelemetryPort)
    with adapter.span("stage.run", change_id="chg-001", stage="construction") as span:
        assert isinstance(span, Span)
        assert span.name == "stage.run"
        assert span.attributes == {"change_id": "chg-001", "stage": "construction"}


def test_trace_restores_change_to_deployment_chain() -> None:
    """T-060 DoD: the exported trace rebuilds change → … → deployment."""
    adapter, memory = _adapter()
    with ExitStack() as nested:
        for name in CHAIN:
            nested.enter_context(adapter.span(name))

    spans = memory.get_finished_spans()
    assert _restore_chain(spans) == list(CHAIN)
    assert _by_name(spans, "deployment").parent is not None
    assert _by_name(spans, "change").parent is None
    # One trace, not seven unrelated spans.
    assert len({span.context.trace_id for span in spans}) == 1
    assert spans[0].resource.attributes["service.name"] == SERVICE_NAME


def test_record_usage_attaches_to_the_current_span() -> None:
    adapter, memory = _adapter()
    with adapter.span("stage.construction"):
        adapter.record_usage(
            Usage(
                prompt_tokens=10,
                completion_tokens=20,
                total_tokens=30,
                cost=Decimal("0.25"),
            ),
            change_id="chg-001",
        )

    spans = memory.get_finished_spans()
    usage_span = _by_name(spans, USAGE_SPAN_NAME)
    stage_span = _by_name(spans, "stage.construction")
    assert usage_span.parent is not None
    assert usage_span.parent.span_id == stage_span.context.span_id
    assert usage_span.attributes == {
        "change_id": "chg-001",
        f"{USAGE_ATTRIBUTE_PREFIX}prompt_tokens": 10,
        f"{USAGE_ATTRIBUTE_PREFIX}completion_tokens": 20,
        f"{USAGE_ATTRIBUTE_PREFIX}total_tokens": 30,
        f"{USAGE_ATTRIBUTE_PREFIX}cost": "0.25",
    }


def test_record_usage_keeps_unknown_values_absent() -> None:
    adapter, memory = _adapter()
    adapter.record_usage(Usage(prompt_tokens=1, completion_tokens=2))

    attributes = _attributes(_by_name(memory.get_finished_spans(), USAGE_SPAN_NAME))
    assert attributes[f"{USAGE_ATTRIBUTE_PREFIX}prompt_tokens"] == 1
    assert f"{USAGE_ATTRIBUTE_PREFIX}total_tokens" not in attributes
    assert f"{USAGE_ATTRIBUTE_PREFIX}cost" not in attributes


def test_record_usage_without_active_span_is_not_lost() -> None:
    adapter, memory = _adapter()
    adapter.record_usage(Usage(prompt_tokens=3, completion_tokens=4))

    usage_span = _by_name(memory.get_finished_spans(), USAGE_SPAN_NAME)
    assert usage_span.parent is None
    assert _attributes(usage_span)[f"{USAGE_ATTRIBUTE_PREFIX}prompt_tokens"] == 3


def test_exception_marks_the_span_and_propagates() -> None:
    adapter, memory = _adapter()

    class _Boom(RuntimeError):
        pass

    with pytest.raises(_Boom), adapter.span("stage.construction"):
        raise _Boom("agent failed")

    span = _by_name(memory.get_finished_spans(), "stage.construction")
    assert span.status.status_code is StatusCode.ERROR
    assert [event.name for event in span.events] == ["exception"]


def test_config_defaults_when_environment_is_empty() -> None:
    config = TelemetryConfig.from_env({})
    assert config == TelemetryConfig()
    assert config.service_name == DEFAULT_SERVICE_NAME
    assert config.exporter is TelemetryExporter.CONSOLE
    assert config.artifact_path is None


def test_config_reads_service_name_and_file_exporter() -> None:
    config = TelemetryConfig.from_env(
        {
            SERVICE_NAME_ENV_VAR: "  dark-factory-pilot  ",
            EXPORTER_ENV_VAR: "FILE",
            FILE_ENV_VAR: "  artifacts/traces.jsonl  ",
        }
    )
    assert config.service_name == "dark-factory-pilot"
    assert config.exporter is TelemetryExporter.FILE
    assert config.artifact_path == Path("artifacts/traces.jsonl")


def test_config_rejects_unknown_exporter() -> None:
    with pytest.raises(ValueError, match=EXPORTER_ENV_VAR):
        TelemetryConfig.from_env({EXPORTER_ENV_VAR: "otlp"})


@pytest.mark.parametrize("path", [None, "", "   "])
def test_config_rejects_file_exporter_without_path(path: str | None) -> None:
    env = {EXPORTER_ENV_VAR: "file"}
    if path is not None:
        env[FILE_ENV_VAR] = path
    with pytest.raises(ValueError, match=FILE_ENV_VAR):
        TelemetryConfig.from_env(env)


def test_file_exporter_without_path_is_rejected_at_construction() -> None:
    with pytest.raises(ValueError, match=FILE_ENV_VAR):
        OtlpTelemetryAdapter(TelemetryConfig(exporter=TelemetryExporter.FILE))


def test_json_lines_exporter_writes_one_object_per_span(tmp_path: Path) -> None:
    target = tmp_path / "artifacts" / "traces.jsonl"
    adapter = OtlpTelemetryAdapter(TelemetryConfig(), exporter=JsonLinesSpanExporter(target))

    with adapter.span("change", change_id="chg-001"), adapter.span("run"):
        pass

    # The exporter creates its parent directories on the first write.
    assert target.parent.is_dir()
    lines = target.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    run, change = (json.loads(line) for line in lines)
    assert list(change) == [
        "trace_id",
        "span_id",
        "parent_span_id",
        "name",
        "attributes",
        "start_time_unix_nano",
        "end_time_unix_nano",
        "status",
    ]
    assert [change["name"], run["name"]] == ["change", "run"]
    assert change["parent_span_id"] is None
    assert run["parent_span_id"] == change["span_id"]
    assert run["trace_id"] == change["trace_id"]
    assert change["attributes"] == {"change_id": "chg-001"}
    assert change["status"] == "UNSET"
    assert isinstance(change["start_time_unix_nano"], int)


def test_json_lines_exporter_appends_across_exports(tmp_path: Path) -> None:
    target = tmp_path / "traces.jsonl"
    adapter = OtlpTelemetryAdapter(TelemetryConfig(), exporter=JsonLinesSpanExporter(target))

    with adapter.span("first"):
        pass
    with adapter.span("second"):
        pass

    names = [json.loads(line)["name"] for line in target.read_text(encoding="utf-8").splitlines()]
    assert names == ["first", "second"]


class _ExplodingExporter(SpanExporter):
    """Sink that fails every call, quoting payload data in its messages."""

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        raise RuntimeError(f"cannot write {SECRET}")

    def shutdown(self) -> None:
        raise RuntimeError(f"cannot close {SECRET}")

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        raise RuntimeError(f"cannot flush {SECRET}")


def test_safe_exporter_degrades_instead_of_raising(capsys: pytest.CaptureFixture[str]) -> None:
    adapter = OtlpTelemetryAdapter(TelemetryConfig(), exporter=_ExplodingExporter())

    with adapter.span("stage.construction", token=SECRET):
        pass
    adapter.record_usage(Usage(prompt_tokens=1), token=SECRET)
    adapter.shutdown()

    captured = capsys.readouterr()
    assert "span exporter export failed" in captured.err
    # Diagnostics carry the failure kind only: no payload, no attribute values.
    assert SECRET not in captured.err
    assert SECRET not in captured.out


def test_safe_exporter_falls_back_on_a_failing_delegate(
    capsys: pytest.CaptureFixture[str],
) -> None:
    safe = SafeSpanExporter(_ExplodingExporter())
    assert safe.export([]) is SpanExportResult.FAILURE
    assert safe.force_flush() is False
    safe.shutdown()

    errors = capsys.readouterr().err.splitlines()
    assert len(errors) == 3
    assert all(line.startswith("dark-factory telemetry: span exporter") for line in errors)
    assert "export" in errors[0]
    assert "force_flush" in errors[1]
    assert "shutdown" in errors[2]


def test_shutdown_flushes_and_is_repeatable() -> None:
    adapter, memory = _adapter()
    with adapter.span("change"):
        pass

    adapter.shutdown()
    adapter.shutdown()

    assert [span.name for span in memory.get_finished_spans()] == ["change"]


def test_file_exporter_is_selected_by_config(tmp_path: Path) -> None:
    """``exporter=file`` is the only non-default sink and must reach the artifact."""
    target = tmp_path / "traces.jsonl"
    adapter = OtlpTelemetryAdapter(
        TelemetryConfig(exporter=TelemetryExporter.FILE, artifact_path=target)
    )
    with adapter.span("change"):
        pass
    adapter.shutdown()

    assert [json.loads(line)["name"] for line in target.read_text().splitlines()] == ["change"]
