"""``factory api serve``: run the factory REST API locally (T035, contract api.md, ADR-009 p.7).

The command serves the API over the PostgreSQL state store (``DATABASE_URL``,
ADR-004): the same authoritative store the CLI jobs write. Exit codes
(contract cli.md): 0 after the server stops, 2 when ``DATABASE_URL`` is
missing or the store is unreachable/misconfigured. Errors never echo the URL
or the raw exception, which may contain credentials (ADR-009).
"""

import os
import sys
from typing import Final

from sqlalchemy.exc import SQLAlchemyError

from dark_factory.api import create_app
from dark_factory.cli.main import EXIT_INVALID_INPUT, EXIT_OK, ApiServeArgs
from dark_factory.orchestration.state.engine import (
    create_session_factory,
    create_state_engine,
)

DATABASE_URL_ENV_VAR: Final[str] = "DATABASE_URL"


def run_api_serve_command(args: ApiServeArgs) -> int:
    """Serve the API until interrupted; fail fast with exit 2 on misconfiguration."""
    database_url = os.environ.get(DATABASE_URL_ENV_VAR, "").strip()
    if not database_url:
        print(f"factory api serve: {DATABASE_URL_ENV_VAR} is not configured", file=sys.stderr)
        return EXIT_INVALID_INPUT
    try:
        engine = create_state_engine(database_url)
        with engine.connect():
            pass  # liveness probe: fail fast with exit 2 when the store is unreachable
    except SQLAlchemyError:
        # The exception text may embed the URL with credentials (ADR-009).
        print(
            "factory api serve: the state store is not reachable or misconfigured",
            file=sys.stderr,
        )
        return EXIT_INVALID_INPUT
    app = create_app(create_session_factory(engine))
    try:
        import uvicorn

        uvicorn.run(app, host=args.host, port=args.port)
    finally:
        engine.dispose()
    return EXIT_OK
