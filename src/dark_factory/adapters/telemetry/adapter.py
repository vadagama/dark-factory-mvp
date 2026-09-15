"""``OtlpTelemetryAdapter``: OpenTelemetry behind ``TelemetryPort`` (T-060, ADR-009).

The port is the factory's only observability contract (ADR-009 p.2): callers
open spans and record usage, the adapter decides how that becomes a trace. This
adapter keeps the port's value object — a plain ``dark_factory.ports.Span`` —
while an OTel span lives underneath, so no provider type ever reaches the core
(ADR-015 p.3).

Correlation is carried by span nesting, which is what makes a whole run
readable in one trace::

    change → run → stage → agent call → tool call → ci job → deployment

Every ``span`` call starts an OTel span as the *current* one, so a span opened
inside another becomes its child without the caller passing an id around; a
stage opened inside ``change`` lands in the same trace, and a tool call opened
inside an agent call nests one level deeper. Usage recorded while a span is
current attaches to it as the ``factory.usage`` child span, so tokens and cost
sit exactly where they were spent (ADR-009 p.3).

Two rules the caller owns:

- **Never put secrets or personal data in span names or attributes** — they
  travel to logs and artifacts unredacted (ADR-009 p.8).
- A failing sink must not fail the run: the exporter is always wrapped in
  ``SafeSpanExporter``, so telemetry degrades and the pipeline continues.

A remote OTLP endpoint is deliberately absent: the MVP exports to job logs and
a JSON-lines artifact (TD-003), and wiring the adapter into the stages is the
next task, not this one.
"""

from contextlib import AbstractContextManager
from types import TracebackType
from typing import Final

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor, SpanExporter
from opentelemetry.trace import Span as OtelSpan
from opentelemetry.trace import Tracer
from opentelemetry.util.types import AttributeValue

from dark_factory.adapters.telemetry.config import TelemetryConfig, TelemetryExporter
from dark_factory.adapters.telemetry.exporters import JsonLinesSpanExporter, SafeSpanExporter
from dark_factory.ports import Span, TelemetryPort, Usage

USAGE_ATTRIBUTE_PREFIX: Final[str] = "usage."
"""Attribute namespace of usage/cost values, e.g. ``usage.prompt_tokens``."""

USAGE_SPAN_NAME: Final[str] = "factory.usage"
"""Name of the span that carries one usage/cost record (ADR-009 p.3)."""

_TRACER_NAME: Final[str] = "dark_factory.adapters.telemetry"
"""Instrumentation scope of the factory's tracer."""


class OtlpTelemetryAdapter(TelemetryPort):
    """OpenTelemetry adapter implementing ``TelemetryPort`` (ADR-009 p.2).

    Construction builds the tracer provider and its synchronous processor; the
    provider owns the tracer, so ``shutdown`` releases it. The provider is
    private: callers interact with the port only.
    """

    def __init__(self, config: TelemetryConfig, *, exporter: SpanExporter | None = None) -> None:
        """Bind the adapter to ``config``; ``exporter`` replaces the sink in tests.

        Without an injected exporter the sink follows the config policy:
        ``console`` prints to the job log, ``file`` appends to the configured
        artifact path. The chosen exporter is always wrapped in
        ``SafeSpanExporter``.
        """
        sink = exporter if exporter is not None else _build_exporter(config)
        self._provider = TracerProvider(
            resource=Resource.create({"service.name": config.service_name})
        )
        self._provider.add_span_processor(SimpleSpanProcessor(SafeSpanExporter(sink)))
        self._tracer: Tracer = self._provider.get_tracer(_TRACER_NAME)

    def span(self, name: str, /, **attributes: str) -> AbstractContextManager[Span]:
        """Open an OTel span as the current one, exposing the port's value object.

        The returned handle yields a plain ``Span`` with exactly the caller's
        attributes and delegates entry/exit to the OTel context manager, so
        nesting establishes parent links and an exception inside the block is
        recorded on the span and still propagates to the caller.
        """
        scope = self._tracer.start_as_current_span(name, attributes=attributes)
        return _SpanScope(scope, Span(name=name, attributes=dict(attributes)))

    def record_usage(self, usage: Usage, /, **attributes: str) -> None:
        """Record tokens and cost as a short-lived ``factory.usage`` span.

        Emitted as a child of whatever span is current (the AI call or the
        stage), which is how usage stays correlated with the work that spent it;
        with no active span the record is still emitted as a root span, so usage
        is never silently dropped. ``total_tokens`` and ``cost`` are written only
        when known — a missing value is absent, not zero.
        """
        span_attributes: dict[str, AttributeValue] = {**attributes}
        span_attributes[f"{USAGE_ATTRIBUTE_PREFIX}prompt_tokens"] = usage.prompt_tokens
        span_attributes[f"{USAGE_ATTRIBUTE_PREFIX}completion_tokens"] = usage.completion_tokens
        if usage.total_tokens is not None:
            span_attributes[f"{USAGE_ATTRIBUTE_PREFIX}total_tokens"] = usage.total_tokens
        if usage.cost is not None:
            span_attributes[f"{USAGE_ATTRIBUTE_PREFIX}cost"] = str(usage.cost)
        with self._tracer.start_as_current_span(USAGE_SPAN_NAME, attributes=span_attributes):
            pass

    def shutdown(self) -> None:
        """Flush and release the tracer provider (not part of ``TelemetryPort``)."""
        self._provider.shutdown()


class _SpanScope(AbstractContextManager[Span]):
    """Port-level handle of one span: yields the port value, drives the OTel span.

    Kept separate so the port's ``Span`` stays a plain value object while its
    lifetime is still bound to the OTel context manager.
    """

    def __init__(self, scope: AbstractContextManager[OtelSpan], value: Span) -> None:
        self._scope = scope
        self._value = value

    def __enter__(self) -> Span:
        self._scope.__enter__()
        return self._value

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._scope.__exit__(exc_type, exc_value, traceback)


def _build_exporter(config: TelemetryConfig) -> SpanExporter:
    """Exporter of the configured policy; a missing artifact path is an error."""
    if config.exporter is TelemetryExporter.FILE:
        if config.artifact_path is None:
            raise ValueError(
                f"exporter {TelemetryExporter.FILE.value} requires an artifact path "
                "(DARK_FACTORY_TELEMETRY_FILE)"
            )
        return JsonLinesSpanExporter(config.artifact_path)
    return ConsoleSpanExporter()
