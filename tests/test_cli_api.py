"""``factory api serve`` binds the composition seams into the app (T059/T066/T072)."""

from typing import Any, cast

import pytest
from sqlalchemy.orm import Session, sessionmaker

import dark_factory.cli.api as api_module
from dark_factory.cli._common import DATABASE_URL_ENV_VAR
from dark_factory.cli.main import EXIT_INVALID_INPUT, ApiServeArgs


class _Stop(Exception):
    """Raised by the app stub so the command never reaches uvicorn."""


def test_api_serve_forwards_every_seam_to_create_app(monkeypatch: pytest.MonkeyPatch) -> None:
    # T066 accepted ``provisioning`` here but never handed it to ``create_app``,
    # so ``POST /products/{id}/validate`` answered 503 on every served contour.
    # Every seam must reach the app, or the served API silently differs from the
    # one the tests build directly.
    seen: dict[str, Any] = {}
    factory = cast("sessionmaker[Session]", object())

    class _Store:
        def __enter__(self) -> sessionmaker[Session]:
            return factory

        def __exit__(self, *exc: object) -> None:
            return None

    def _create_app(session_factory: Any, **kwargs: Any) -> Any:
        seen["session_factory"] = session_factory
        seen.update(kwargs)
        raise _Stop()

    monkeypatch.setenv(DATABASE_URL_ENV_VAR, "postgresql+psycopg://x:y@localhost/db")
    monkeypatch.setattr(api_module, "open_state_store", lambda url: _Store())
    monkeypatch.setattr(api_module, "create_app", _create_app)
    provisioning, toggles, formulator = object(), object(), object()

    with pytest.raises(_Stop):
        api_module.run_api_serve_command(
            ApiServeArgs(host="127.0.0.1", port=8000),
            ci_toggles=cast("Any", toggles),
            ci_repository="org/repo",
            provisioning=cast("Any", provisioning),
            brief_formulator=cast("Any", formulator),
        )

    assert seen["session_factory"] is factory
    assert seen["ci_toggles"] is toggles
    assert seen["ci_repository"] == "org/repo"
    assert seen["provisioning"] is provisioning
    assert seen["brief_formulator"] is formulator


def test_api_serve_without_a_database_url_is_invalid_input(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(DATABASE_URL_ENV_VAR, raising=False)
    code = api_module.run_api_serve_command(ApiServeArgs(host="127.0.0.1", port=8000))
    assert code == EXIT_INVALID_INPUT
    assert "DATABASE_URL" in capsys.readouterr().err
