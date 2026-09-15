"""Span exporters of the telemetry adapter: job logs, JSON-lines artifact, safe wrapper.

``ConsoleSpanExporter`` (from the SDK) satisfies the "export to job logs"
default; ``JsonLinesSpanExporter`` writes the same spans as one JSON object per
line, ready to be published as a CI artifact. ``SafeSpanExporter`` is the layer
that keeps a broken sink from breaking the pipeline (FR-020 in spirit): a
telemetry failure degrades observability and never the run.

Both sinks are local to the job, so a single synchronous ``SimpleSpanProcessor``
is enough — there is no batching, no background thread and therefore no lost
spans on job exit (ADR-009 p.2).
"""

import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

DEFAULT_FLUSH_TIMEOUT_MILLIS: Final[int] = 30000
"""Flush deadline of the exporter protocol, mirrored from the SDK base class."""

_DIAGNOSTIC_PREFIX: Final[str] = "dark-factory telemetry"


class JsonLinesSpanExporter(SpanExporter):
    """Append finished spans to a JSON-lines file, one object per line.

    Every line is a flat, deterministic record of one span (fixed key order,
    primitive values only); attribute values are stringified because OTel
    attributes may hold non-JSON types such as sequences. The file is opened for
    append and closed per export, so several exports accumulate and a partly
    written file stays readable; parent directories are created on demand.

    I/O errors are raised as-is: the adapter always wraps this exporter in
    ``SafeSpanExporter``, which turns them into a degraded trace plus one
    diagnostic line.
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        """Append ``spans`` as JSON lines; returns ``SUCCESS`` when the write lands."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as stream:
            for span in spans:
                stream.write(json.dumps(_span_line(span)) + "\n")
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        """Nothing to release: every export opens and closes its own file handle."""

    def force_flush(self, timeout_millis: int = DEFAULT_FLUSH_TIMEOUT_MILLIS) -> bool:
        """Always true: exports are synchronous and already on disk."""
        return True


class SafeSpanExporter(SpanExporter):
    """Wrap a delegate so a failing sink never breaks the caller (ADR-009 p.2).

    ``export``/``force_flush``/``shutdown`` are called from the OTel processor
    while a factory span is ending; an exception escaping them would abort the
    run over observability. This wrapper reports the failure on ``stderr`` — the
    exception *type* only, never the span payload, attribute values or the
    exception message, which could quote them (ADR-009 p.8) — and returns a
    failure result instead.
    """

    def __init__(self, delegate: SpanExporter) -> None:
        self._delegate = delegate

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        """Delegate the export, degrading to ``FAILURE`` instead of raising."""
        try:
            return self._delegate.export(spans)
        except Exception as error:  # a sink must never raise into the run
            _report_failure("export", error)
            return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        """Delegate shutdown, reporting instead of raising."""
        try:
            self._delegate.shutdown()
        except Exception as error:  # a sink must never raise into the run
            _report_failure("shutdown", error)

    def force_flush(self, timeout_millis: int = DEFAULT_FLUSH_TIMEOUT_MILLIS) -> bool:
        """Delegate the flush, degrading to ``False`` instead of raising."""
        try:
            return self._delegate.force_flush(timeout_millis)
        except Exception as error:  # a sink must never raise into the run
            _report_failure("force_flush", error)
            return False


def _report_failure(operation: str, error: BaseException) -> None:
    """One diagnostic line per failed exporter call, carrying no payload."""
    print(
        f"{_DIAGNOSTIC_PREFIX}: span exporter {operation} failed "
        f"({type(error).__name__}); telemetry degraded",
        file=sys.stderr,
    )


def _span_line(span: ReadableSpan) -> dict[str, object]:
    """Flat, deterministic JSON record of one finished span.

    Identifiers use the canonical OTel hexadecimal form; ``parent_span_id`` is
    ``None`` for a root span, so a reader can rebuild the trace by following
    parent links without any extra index.
    """
    parent = span.parent
    attributes: Mapping[str, object] = span.attributes or {}
    return {
        "trace_id": format(span.context.trace_id, "032x"),
        "span_id": format(span.context.span_id, "016x"),
        "parent_span_id": None if parent is None else format(parent.span_id, "016x"),
        "name": span.name,
        "attributes": {key: str(attributes[key]) for key in sorted(attributes)},
        "start_time_unix_nano": span.start_time,
        "end_time_unix_nano": span.end_time,
        "status": span.status.status_code.name,
    }
