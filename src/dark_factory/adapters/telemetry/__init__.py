"""OpenTelemetry adapter behind ``TelemetryPort`` (T-060, ADR-009).

Observability is the one port with a single real provider for now:
``OtlpTelemetryAdapter`` implements ``TelemetryPort`` over the OpenTelemetry SDK
and keeps every provider type inside this package (ADR-015 p.3). Export goes to
the job log or a JSON-lines artifact by default (``config.py``); a remote OTLP
backend is added later at the exporter level (TD-003).
"""

from dark_factory.adapters.telemetry.adapter import (
    USAGE_ATTRIBUTE_PREFIX,
    USAGE_SPAN_NAME,
    OtlpTelemetryAdapter,
)
from dark_factory.adapters.telemetry.config import (
    DEFAULT_SERVICE_NAME,
    EXPORTER_ENV_VAR,
    FILE_ENV_VAR,
    SERVICE_NAME_ENV_VAR,
    TelemetryConfig,
    TelemetryExporter,
)
from dark_factory.adapters.telemetry.exporters import (
    JsonLinesSpanExporter,
    SafeSpanExporter,
)

__all__ = [
    "DEFAULT_SERVICE_NAME",
    "EXPORTER_ENV_VAR",
    "FILE_ENV_VAR",
    "SERVICE_NAME_ENV_VAR",
    "USAGE_ATTRIBUTE_PREFIX",
    "USAGE_SPAN_NAME",
    "JsonLinesSpanExporter",
    "OtlpTelemetryAdapter",
    "SafeSpanExporter",
    "TelemetryConfig",
    "TelemetryExporter",
]
