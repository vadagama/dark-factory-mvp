"""Helpers shared by the ``factory`` command modules (contract cli.md, ADR-009).

Every command module reports its errors the same way, reads the same
state-store configuration and opens the store through the same fail-fast
bootstrap; this module holds those pieces once so the commands differ only
in what they do, not in how they report.

- :func:`report_error` and its two fixed flavours
  :func:`report_invalid_input` (exit 2) / :func:`report_execution_error`
  (exit 1) emit one error report: in ``--json`` mode a single
  ``{"error": <tag>, "detail": <message>}`` object on stdout, otherwise the
  line ``factory <command>: <message>`` on stderr;
- :func:`os_error_reason` is the short, hygienic text of an ``OSError``;
- :func:`database_url` / :func:`default_owner_id` read the state-store URL
  (``DATABASE_URL`` — the variable ``factory doctor`` owns and checks; the
  local default when unset, ADR-004) and the lease owner id of a CLI pass;
- :func:`open_state_store` is the store bootstrap of every PostgreSQL-backed
  command: create the engine, probe it with one connection so an
  unreachable or misconfigured store fails fast, hand out the session
  factory and dispose the engine afterwards. The probe failure surfaces as
  :class:`StateStoreUnreachableError`, which the commands report through
  :func:`report_state_store_unreachable` (exit 2) — the exception text may
  embed the URL with credentials and is never echoed (ADR-009).
"""

import json
import os
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Final

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from dark_factory.cli.doctor import DATABASE_URL_ENV_VAR
from dark_factory.cli.main import EXIT_ERROR, EXIT_INVALID_INPUT
from dark_factory.orchestration.state.engine import (
    DEFAULT_DATABASE_URL,
    create_session_factory,
    create_state_engine,
)

__all__ = [
    "CLI_ACTOR",
    "DATABASE_URL_ENV_VAR",
    "STATE_STORE_UNREACHABLE",
    "StateStoreUnreachableError",
    "database_url",
    "default_owner_id",
    "open_state_store",
    "os_error_reason",
    "report_error",
    "report_execution_error",
    "report_invalid_input",
    "report_state_store_unreachable",
]

STATE_STORE_UNREACHABLE: Final[str] = "the state store is not reachable or misconfigured"
"""Fixed diagnostic of a failed store bootstrap; it never names the URL (ADR-009)."""

CLI_ACTOR: Final[str] = "cli"
"""``actor`` of the audit rows an operator command writes (T064, T070).

The CLI is the trusted local operator path: it carries no bearer token, so its
decisions are attributed to the command line itself, with no role claimed.
"""


class StateStoreUnreachableError(RuntimeError):
    """The state store could not be reached or its URL is misconfigured (exit 2).

    Raised by :func:`open_state_store` in place of the ``SQLAlchemyError`` it
    wraps: that exception's text may embed the database URL with credentials
    and must never reach the output (ADR-009).
    """


def report_error(command: str, tag: str, message: str, code: int, *, json_output: bool) -> int:
    """Emit one error report and return ``code`` (contract cli.md).

    ``--json`` mode prints ``{"error": tag, "detail": message}`` on stdout;
    text mode prints ``factory <command>: <message>`` on stderr. ``command``
    is the subcommand path as the user typed it (``"stage run"``,
    ``"run advance"``).
    """
    if json_output:
        print(json.dumps({"error": tag, "detail": message}))
    else:
        print(f"factory {command}: {message}", file=sys.stderr)
    return code


def report_invalid_input(command: str, message: str, *, json_output: bool) -> int:
    """Report invalid input/configuration (tag ``invalid_input``, exit 2; nothing ran)."""
    return report_error(
        command, "invalid_input", message, EXIT_INVALID_INPUT, json_output=json_output
    )


def report_execution_error(command: str, message: str, *, json_output: bool) -> int:
    """Report an execution failure after the command's work started (exit 1)."""
    return report_error(command, "execution_error", message, EXIT_ERROR, json_output=json_output)


def report_state_store_unreachable(command: str, *, json_output: bool) -> int:
    """Report an unreachable or misconfigured state store (exit 2, ADR-009 hygiene)."""
    return report_invalid_input(command, STATE_STORE_UNREACHABLE, json_output=json_output)


def os_error_reason(exc: OSError) -> str:
    """Short OS error text without echoing the raw exception (ADR-009 hygiene)."""
    return exc.strerror or exc.__class__.__name__


def database_url() -> str:
    """Configured state-store URL; the local default when unset (ADR-004)."""
    raw = os.environ.get(DATABASE_URL_ENV_VAR)
    return raw if raw and raw.strip() else DEFAULT_DATABASE_URL


def default_owner_id(env_var: str, prefix: str) -> str:
    """Lease owner id of one CLI pass: ``env_var`` when set, else unique per process.

    The unique default (``<prefix>-<pid>-<uuid hex chunk>``) guarantees that
    concurrent CLI passes never share a lease; the environment variable pins
    a stable id for a scheduled job (the CronJob reconciler, a CI runner).
    """
    configured = os.environ.get(env_var, "").strip()
    if configured:
        return configured
    return f"{prefix}-{os.getpid()}-{uuid.uuid4().hex[:8]}"


@contextmanager
def open_state_store(url: str | None = None) -> Iterator[sessionmaker[Session]]:
    """Open the state store for one command: engine, liveness probe, session factory.

    ``url`` defaults to :func:`database_url`. The engine is probed with one
    connection before the command runs so an unreachable or misconfigured
    store fails fast as :class:`StateStoreUnreachableError` (the caller
    reports exit 2) instead of a traceback from the first query; the engine
    is disposed when the block ends, whatever happened inside it.
    """
    try:
        engine = create_state_engine(url if url is not None else database_url())
        with engine.connect():
            pass  # liveness probe: fail fast with exit 2 when the store is unreachable
    except SQLAlchemyError as exc:
        # The exception text may embed the URL with credentials (ADR-009).
        raise StateStoreUnreachableError(STATE_STORE_UNREACHABLE) from exc
    try:
        yield create_session_factory(engine)
    finally:
        engine.dispose()
