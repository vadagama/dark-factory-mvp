"""In-memory fake of the telemetry port (T-060).

The production adapter lives in ``dark_factory.adapters.telemetry``
(``OtlpTelemetryAdapter``); the same contract suite runs against both.
"""

from contextlib import AbstractContextManager

from dark_factory.ports import Span, TelemetryPort, Usage


class FakeTelemetry(TelemetryPort):
    """In-memory ``TelemetryPort`` recording spans and usage in lists."""

    def __init__(self) -> None:
        self._spans: list[Span] = []
        self._usage: list[tuple[Usage, dict[str, str]]] = []

    def span(self, name: str, /, **attributes: str) -> AbstractContextManager[Span]:
        span = Span(name=name, attributes=dict(attributes))
        self._spans.append(span)
        return span

    def record_usage(self, usage: Usage, /, **attributes: str) -> None:
        self._usage.append((usage, dict(attributes)))

    def recorded_spans(self) -> tuple[Span, ...]:
        """Read view of recorded spans (not part of the port)."""
        return tuple(self._spans)

    def recorded_usage(self) -> tuple[tuple[Usage, dict[str, str]], ...]:
        """Read view of recorded usage with attributes (not part of the port)."""
        return tuple(self._usage)
