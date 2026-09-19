"""FastAPI application factory of the factory API (T035, contract api.md, ADR-009 p.7).

The app is stateless with respect to the process: one SQLAlchemy session per
request, committed on success and rolled back on any error. Every error
response carries the RFC 7807-like body of the contract (``type``, ``title``,
``status``, ``detail``); the catch-all 500 handler never leaks internals.
"""

import http
from collections.abc import Callable, Iterator
from typing import Final

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, sessionmaker
from starlette.exceptions import HTTPException as StarletteHTTPException

from dark_factory.api.auth import ApiTokenStore
from dark_factory.api.dto import ErrorBody
from dark_factory.api.routes_changes import create_changes_router
from dark_factory.api.routes_ci import create_ci_router
from dark_factory.api.routes_products import create_products_router
from dark_factory.api.routes_runs import create_runs_router
from dark_factory.orchestration.intake import BriefFormulator
from dark_factory.ports import CiStageTogglePort, RepositoryProvisioningPort

API_PREFIX: Final[str] = "/api/v1"


def _error_response(
    status: int, detail: str, headers: dict[str, str] | None = None
) -> JSONResponse:
    """One RFC 7807-like error body with the HTTP status phrase as title."""
    try:
        title = http.HTTPStatus(status).phrase
    except ValueError:
        title = "Error"
    body = ErrorBody(type="about:blank", title=title, status=status, detail=detail)
    return JSONResponse(status_code=status, content=body.model_dump(), headers=headers)


def _http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, StarletteHTTPException):  # pragma: no cover - registered for it only
        return _error_response(500, "An unexpected error occurred.")
    detail = exc.detail if isinstance(exc.detail, str) else "Request failed"
    headers = dict(exc.headers) if exc.headers else None
    return _error_response(exc.status_code, detail, headers)


def _validation_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):  # pragma: no cover - registered for it only
        return _error_response(500, "An unexpected error occurred.")
    errors = "; ".join(
        "{}: {}".format(".".join(str(part) for part in error["loc"]), error["msg"])
        for error in exc.errors()
    )
    return _error_response(422, errors or "Request validation failed")


def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # Never echo the exception: its text may contain credentials (ADR-009).
    return _error_response(500, "An unexpected error occurred.")


def create_session_dependency(
    factory: sessionmaker[Session],
) -> Callable[..., Iterator[Session]]:
    """One session per request: commit on success, rollback on any error."""

    def dependency() -> Iterator[Session]:
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return dependency


def create_app(
    session_factory: sessionmaker[Session],
    tokens: ApiTokenStore | None = None,
    *,
    ci_toggles: CiStageTogglePort | None = None,
    ci_repository: str | None = None,
    provisioning: RepositoryProvisioningPort | None = None,
    brief_formulator: BriefFormulator | None = None,
) -> FastAPI:
    """Build the API app over the given session factory and token store.

    ``tokens=None`` builds the store from ``DARK_FACTORY_API_TOKENS``; an
    absent variable yields an empty store, which fails closed on every
    mutating request (ADR-009 p.7). ``ci_toggles`` is the repository-variable
    port of the CI stage switches (T059, ADR-027): with ``None`` the ``/ci``
    endpoints still serve the stage catalog but report themselves unavailable
    and refuse writes — the core path (``python -m dark_factory.cli``) and any
    contour without GitHub credentials stay honest instead of failing later.
    ``provisioning`` is the repository-provisioning port of product validation
    (T066, ADR-031): with ``None`` the ``/products`` registry still works, but
    ``POST /products/{id}/validate`` refuses with 503 instead of inventing a
    readiness the factory cannot observe. ``brief_formulator`` is the
    harness-backed «Помоги сформулировать» seam (T072): with ``None`` the
    ``POST /briefs/formulate`` endpoint answers an honest draft.
    """
    token_store = tokens if tokens is not None else ApiTokenStore.from_env()
    session_dependency = create_session_dependency(session_factory)

    app = FastAPI(
        title="Dark Factory API",
        version="0.1.0",
        description="Operator control plane of the Software Dark Factory (contract api.md).",
    )
    app.state.session_factory = session_factory
    app.state.token_store = token_store
    app.include_router(create_runs_router(session_dependency, token_store), prefix=API_PREFIX)
    app.include_router(
        create_changes_router(session_dependency, token_store, brief_formulator),
        prefix=API_PREFIX,
    )
    app.include_router(
        create_products_router(session_dependency, token_store, provisioning), prefix=API_PREFIX
    )
    app.include_router(create_ci_router(token_store, ci_toggles, ci_repository), prefix=API_PREFIX)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)
    return app
