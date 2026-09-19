"""Environment and configuration checks for ``factory doctor`` (T008, contract cli.md).

The doctor verifies what a factory stage depends on at start-up: the Python
interpreter (``requires-python`` in pyproject.toml), the installed
``dark_factory`` distribution and the state store configuration
(``DATABASE_URL``, ADR-004). Checks are pure and offline: no database
connection is attempted — an invalid configuration is an error, an
unreachable database is not this command's concern (YAGNI for US1).

Secrets never reach the output (ADR-009, contract cli.md): the state store
URL is reported as scheme/host/port/database only, never the raw URL, the
username, the password or query parameters. In particular the exception text
of an unparseable URL (which embeds the raw value) is not surfaced.

Output: ``render_text`` for the default human-readable report, ``render_json``
for the stable machine-readable report (list of checks plus a summary).
Exit codes are chosen by ``dark_factory.cli.main``: 0 unless a check has
status ``error``, then 2 (contract cli.md).
"""

import importlib.metadata
import json
import os
import platform
import sys
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from sqlalchemy import URL, make_url
from sqlalchemy.exc import ArgumentError

MINIMUM_PYTHON_VERSION: Final[tuple[int, int]] = (3, 12)
"""Minimal interpreter version; mirrors ``requires-python >= 3.12`` (pyproject.toml)."""

DATABASE_URL_ENV_VAR: Final[str] = "DATABASE_URL"
"""State-store URL variable (ADR-004); the store-backed commands read it via ``cli._common``."""

DISTRIBUTION_NAME: Final[str] = "dark-factory"


class CheckStatus(StrEnum):
    """Outcome of a single doctor check (contract cli.md)."""

    OK = "ok"
    WARN = "warn"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class Check:
    """One doctor check outcome; ``detail`` never contains secrets (ADR-009)."""

    name: str
    status: CheckStatus
    detail: str


@dataclass(frozen=True, slots=True)
class DoctorReport:
    """Aggregated result of all doctor checks."""

    checks: tuple[Check, ...]

    @property
    def status_counts(self) -> dict[CheckStatus, int]:
        counts: dict[CheckStatus, int] = dict.fromkeys(CheckStatus, 0)
        for check in self.checks:
            counts[check.status] += 1
        return counts

    @property
    def overall_status(self) -> CheckStatus:
        """The worst check status: error beats warn beats ok."""
        if any(check.status is CheckStatus.ERROR for check in self.checks):
            return CheckStatus.ERROR
        if any(check.status is CheckStatus.WARN for check in self.checks):
            return CheckStatus.WARN
        return CheckStatus.OK

    @property
    def has_errors(self) -> bool:
        return self.overall_status is CheckStatus.ERROR


def check_python_runtime() -> Check:
    """The interpreter satisfies the minimal version from ``requires-python`` (pyproject.toml)."""
    version = platform.python_version()
    if sys.version_info[:2] >= MINIMUM_PYTHON_VERSION:
        return Check("python_runtime", CheckStatus.OK, f"Python {version}")
    required = ".".join(str(part) for part in MINIMUM_PYTHON_VERSION)
    return Check(
        "python_runtime",
        CheckStatus.ERROR,
        f"Python {version} is too old; Python {required}+ is required (pyproject requires-python)",
    )


def check_package() -> Check:
    """The ``dark_factory`` package is importable and its distribution metadata is readable."""
    try:
        importlib.import_module("dark_factory")
    except ImportError as exc:
        return Check("package", CheckStatus.ERROR, f"dark_factory is not importable: {exc}")
    try:
        version = importlib.metadata.version(DISTRIBUTION_NAME)
    except importlib.metadata.PackageNotFoundError:
        return Check(
            "package",
            CheckStatus.WARN,
            "dark_factory is importable, but its distribution metadata is unavailable",
        )
    return Check("package", CheckStatus.OK, f"dark_factory {version}")


def check_state_store_config() -> Check:
    """``DATABASE_URL`` is absent (warn), a valid postgres URL (ok) or broken (error).

    A missing value is a warning, not an error: the deterministic local stage
    of US1 runs without the state store. The raw URL is never included in the
    detail — only its scheme, host, port and database name (ADR-009).
    """
    raw = os.environ.get(DATABASE_URL_ENV_VAR)
    if raw is None or not raw.strip():
        return Check(
            "state_store_config",
            CheckStatus.WARN,
            f"{DATABASE_URL_ENV_VAR} is not configured;"
            " the deterministic local stage (US1) runs without the state store",
        )
    try:
        url = make_url(raw)
    except ArgumentError:
        # The exception text embeds the raw URL and may contain credentials (ADR-009).
        return Check(
            "state_store_config",
            CheckStatus.ERROR,
            f"{DATABASE_URL_ENV_VAR} is not a valid database URL;"
            " expected postgresql:// or postgresql+psycopg://",
        )
    if not _is_postgres_scheme(url.drivername):
        return Check(
            "state_store_config",
            CheckStatus.ERROR,
            f"{DATABASE_URL_ENV_VAR} uses unsupported scheme {url.drivername!r};"
            " expected postgresql:// or postgresql+psycopg://",
        )
    return Check(
        "state_store_config",
        CheckStatus.OK,
        f"{DATABASE_URL_ENV_VAR} is valid ({_masked_target(url)}; credentials are not shown)",
    )


def collect_report() -> DoctorReport:
    """Run every doctor check and aggregate the outcomes."""
    return DoctorReport(
        checks=(
            check_python_runtime(),
            check_package(),
            check_state_store_config(),
        )
    )


def render_text(report: DoctorReport) -> str:
    """Human-readable one-line-per-check report."""
    counts = report.status_counts
    lines = ["factory doctor"]
    lines.extend(f"{check.name}: {check.status.value} ({check.detail})" for check in report.checks)
    lines.append(
        f"summary: status={report.overall_status.value}"
        f" ok={counts[CheckStatus.OK]} warn={counts[CheckStatus.WARN]}"
        f" error={counts[CheckStatus.ERROR]}"
    )
    return "\n".join(lines)


def render_json(report: DoctorReport) -> str:
    """Stable JSON report: list of checks ``{name, status, detail}`` plus a summary."""
    counts = report.status_counts
    payload = {
        "checks": [
            {"name": check.name, "status": check.status.value, "detail": check.detail}
            for check in report.checks
        ],
        "summary": {
            "status": report.overall_status.value,
            "ok": counts[CheckStatus.OK],
            "warn": counts[CheckStatus.WARN],
            "error": counts[CheckStatus.ERROR],
        },
    }
    return json.dumps(payload)


def _is_postgres_scheme(drivername: str) -> bool:
    """True for ``postgresql`` with any driver and for the legacy ``postgres`` scheme."""
    return drivername == "postgres" or drivername.startswith("postgresql")


def _masked_target(url: URL) -> str:
    """Scheme, host, port and database of ``url``; user, password and query are dropped."""
    netloc = url.host or ""
    if url.port is not None:
        netloc = f"{netloc}:{url.port}"
    return f"{url.drivername}://{netloc}/{url.database or ''}"
