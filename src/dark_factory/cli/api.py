"""``factory api serve``: run the factory REST API locally (T035, contract api.md, ADR-009 p.7).

The command serves the API over the PostgreSQL state store (``DATABASE_URL``,
ADR-004): the same authoritative store the CLI jobs write. Exit codes
(contract cli.md): 0 after the server stops, 2 when ``DATABASE_URL`` is
missing or the store is unreachable/misconfigured. Errors never echo the URL
or the raw exception, which may contain credentials (ADR-009).
"""

import os
from typing import Final

from dark_factory.api import create_app
from dark_factory.cli._common import (
    DATABASE_URL_ENV_VAR,
    StateStoreUnreachableError,
    open_state_store,
    report_invalid_input,
    report_state_store_unreachable,
)
from dark_factory.cli.main import EXIT_OK, ApiServeArgs
from dark_factory.ports import CiStageTogglePort, RepositoryProvisioningPort

_COMMAND: Final[str] = "api serve"
"""Subcommand name of the error reports (``factory api serve: ...``)."""


def run_api_serve_command(
    args: ApiServeArgs,
    *,
    ci_toggles: CiStageTogglePort | None = None,
    ci_repository: str | None = None,
    provisioning: RepositoryProvisioningPort | None = None,
) -> int:
    """Serve the API until interrupted; fail fast with exit 2 on misconfiguration.

    ``ci_toggles``/``ci_repository`` are the CI stage switchboard seam (T059,
    ADR-027): they arrive as arguments from the composition root
    (``runtime.entrypoint``) because this module is core and may not name the
    adapters (ADR-024 p.5). Both default to ``None`` — the ``/ci`` endpoints
    then serve the catalog as unavailable, which is exactly the behaviour of
    the runtime-free path (``python -m dark_factory.cli``). ``provisioning`` is
    the repository-provisioning seam of product validation (T066, ADR-031): with
    ``None`` the endpoint refuses instead of inventing readiness.
    """
    database_url = os.environ.get(DATABASE_URL_ENV_VAR, "").strip()
    if not database_url:
        return report_invalid_input(
            _COMMAND, f"{DATABASE_URL_ENV_VAR} is not configured", json_output=False
        )
    try:
        with open_state_store(database_url) as session_factory:
            app = create_app(
                session_factory,
                ci_toggles=ci_toggles,
                ci_repository=ci_repository,
            )
            import uvicorn

            uvicorn.run(app, host=args.host, port=args.port)
    except StateStoreUnreachableError:
        return report_state_store_unreachable(_COMMAND, json_output=False)
    return EXIT_OK
