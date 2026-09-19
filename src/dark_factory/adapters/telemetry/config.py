"""Environment configuration and export policy of the OTLP telemetry adapter (T-060).

The factory emits OTel traces through ``TelemetryPort``; this module decides
*where* they go. There is no vendor backend in the MVP (ADR-009 p.1): by default
spans are printed to the job logs (stdout) so a CI job carries its own trace,
and a JSON-lines file can be written as an artifact. A remote OTLP backend is
added later at the exporter level, not here (TD-003).

Unlike the tracker and source-control configs, a broken telemetry configuration
is a hard error, not an absent adapter: the exporter name and the artifact path
are a closed policy decision, and a silent fallback would hide a typo. The
values carry no secrets, so nothing needs redaction.

Environment variables:

- ``DARK_FACTORY_TELEMETRY_SERVICE_NAME`` — ``service.name`` resource attribute
  of every span (default ``dark-factory``); one deployment, one service name.
- ``DARK_FACTORY_TELEMETRY_EXPORTER`` — export sink: ``console`` (default, job
  logs on stdout) or ``file`` (JSON lines, CI artifact). An unknown name is a
  configuration error, not a silent default.
- ``DARK_FACTORY_TELEMETRY_FILE`` — path of the JSON-lines artifact file; a blank
  value counts as unset, and a ``file`` exporter without it is a configuration
  error. The path only ever reaches the file exporter.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

from dark_factory.adapters._env import resolve_env

SERVICE_NAME_ENV_VAR: Final[str] = "DARK_FACTORY_TELEMETRY_SERVICE_NAME"
"""``service.name`` resource attribute of the emitted spans."""

EXPORTER_ENV_VAR: Final[str] = "DARK_FACTORY_TELEMETRY_EXPORTER"
"""Export sink of the adapter: ``console`` or ``file``."""

FILE_ENV_VAR: Final[str] = "DARK_FACTORY_TELEMETRY_FILE"
"""JSON-lines artifact path; required when the exporter is ``file``."""

DEFAULT_SERVICE_NAME: Final[str] = "dark-factory"
"""Service name assumed when ``DARK_FACTORY_TELEMETRY_SERVICE_NAME`` is unset."""


class TelemetryExporter(StrEnum):
    """Where finished spans are exported.

    Both sinks are local to the CI job (ADR-009 p.2): ``CONSOLE`` writes to the
    job log, ``FILE`` writes a JSON-lines artifact. A remote OTLP endpoint is a
    later addition (TD-003).
    """

    CONSOLE = "console"
    FILE = "file"


@dataclass(frozen=True, slots=True)
class TelemetryConfig:
    """Service identity and export policy of the telemetry adapter."""

    service_name: str = DEFAULT_SERVICE_NAME
    exporter: TelemetryExporter = TelemetryExporter.CONSOLE
    artifact_path: Path | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "TelemetryConfig":
        """Config read from ``env`` (default: the process environment), fail-closed.

        A blank value counts as unset, so a missing service name falls back to
        ``DEFAULT_SERVICE_NAME`` while a missing path for ``exporter=file`` has
        no valid default and raises ``ValueError``.
        """
        source = resolve_env(env)
        exporter_name = (source.get(EXPORTER_ENV_VAR) or "").strip().lower()
        exporter = _parse_exporter(exporter_name) if exporter_name else TelemetryExporter.CONSOLE
        artifact = (source.get(FILE_ENV_VAR) or "").strip()
        if exporter is TelemetryExporter.FILE and not artifact:
            raise ValueError(
                f"{FILE_ENV_VAR} must be a non-empty path when "
                f"{EXPORTER_ENV_VAR}={TelemetryExporter.FILE.value}"
            )
        return cls(
            service_name=(source.get(SERVICE_NAME_ENV_VAR) or "").strip() or DEFAULT_SERVICE_NAME,
            exporter=exporter,
            artifact_path=Path(artifact) if artifact else None,
        )


def _parse_exporter(value: str) -> TelemetryExporter:
    """Exporter for a configured name; an unknown name is a configuration error."""
    try:
        return TelemetryExporter(value)
    except ValueError as error:
        known = ", ".join(exporter.value for exporter in TelemetryExporter)
        raise ValueError(f"{EXPORTER_ENV_VAR} must be one of: {known}") from error
