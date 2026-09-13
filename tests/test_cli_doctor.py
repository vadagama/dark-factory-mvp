"""``factory doctor`` behavior: check statuses, exit codes and secret hygiene (T008)."""

import importlib.metadata
import json
import sys

import pytest

from dark_factory.cli import doctor
from dark_factory.cli.main import main
from dark_factory.orchestration.state.engine import DEFAULT_DATABASE_URL

SECRET_USER = "alice"
SECRET_PASSWORD = "s3cr3t-passw0rd"
SECRET_DATABASE_URL = (
    f"postgresql+psycopg://{SECRET_USER}:{SECRET_PASSWORD}@db.example.com:5432/factory"
)


def test_python_runtime_is_ok_and_reports_the_interpreter() -> None:
    check = doctor.check_python_runtime()
    assert check.name == "python_runtime"
    assert check.status is doctor.CheckStatus.OK  # the suite itself requires Python >= 3.12
    assert check.detail.startswith("Python ")


def test_python_runtime_fails_below_the_minimum(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "version_info", (3, 11, 5))
    check = doctor.check_python_runtime()
    assert check.status is doctor.CheckStatus.ERROR
    assert "3.12" in check.detail


def test_package_check_reports_the_installed_version() -> None:
    check = doctor.check_package()
    assert check.name == "package"
    assert check.status is doctor.CheckStatus.OK
    assert check.detail == f"dark_factory {importlib.metadata.version('dark-factory')}"


def test_state_store_warns_when_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    check = doctor.check_state_store_config()
    assert check.name == "state_store_config"
    assert check.status is doctor.CheckStatus.WARN
    assert "not configured" in check.detail


@pytest.mark.parametrize("value", ["", "   "])
def test_state_store_warns_on_blank_value(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("DATABASE_URL", value)
    assert doctor.check_state_store_config().status is doctor.CheckStatus.WARN


def test_state_store_ok_masks_user_and_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", SECRET_DATABASE_URL)
    check = doctor.check_state_store_config()
    assert check.status is doctor.CheckStatus.OK
    assert "db.example.com:5432/factory" in check.detail
    assert SECRET_USER not in check.detail
    assert SECRET_PASSWORD not in check.detail
    assert SECRET_DATABASE_URL not in check.detail


def test_state_store_error_on_unparseable_url_keeps_it_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "totally-not-a-url-password-leak-42")
    check = doctor.check_state_store_config()
    assert check.status is doctor.CheckStatus.ERROR
    assert "password-leak-42" not in check.detail


def test_state_store_error_on_non_postgres_scheme_keeps_it_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", f"mysql://{SECRET_USER}:{SECRET_PASSWORD}@localhost:3306/db")
    check = doctor.check_state_store_config()
    assert check.status is doctor.CheckStatus.ERROR
    assert "mysql" in check.detail
    assert SECRET_USER not in check.detail
    assert SECRET_PASSWORD not in check.detail


def test_doctor_all_checks_ok_and_exit_zero_on_standard_environment(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", DEFAULT_DATABASE_URL)
    assert main(["doctor", "--json"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert [check["name"] for check in payload["checks"]] == [
        "python_runtime",
        "package",
        "state_store_config",
    ]
    assert all(check["status"] == "ok" for check in payload["checks"])
    assert payload["summary"] == {"status": "ok", "ok": 3, "warn": 0, "error": 0}


def test_doctor_json_has_stable_schema(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", DEFAULT_DATABASE_URL)
    assert main(["doctor", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {"checks", "summary"}
    assert all(set(check) == {"name", "status", "detail"} for check in payload["checks"])
    assert set(payload["summary"]) == {"status", "ok", "warn", "error"}


def test_doctor_text_output_lists_check_names(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", DEFAULT_DATABASE_URL)
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    for name in ("python_runtime", "package", "state_store_config"):
        assert f"{name}: ok" in out
    assert "summary:" in out


def test_doctor_warns_and_exits_zero_without_database_url(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "state_store_config: warn" in out
    assert "summary: status=warn" in out


def test_doctor_invalid_configuration_exits_with_code_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", "totally-not-a-url")
    assert main(["doctor", "--json"]) == 2
    captured = capsys.readouterr()
    assert captured.err == ""
    payload = json.loads(captured.out)
    by_name = {check["name"]: check for check in payload["checks"]}
    assert by_name["state_store_config"]["status"] == "error"
    assert payload["summary"]["status"] == "error"
    assert payload["summary"]["error"] == 1


@pytest.mark.parametrize("argv", [["doctor"], ["doctor", "--json"]])
def test_doctor_output_never_leaks_database_url_secrets(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", SECRET_DATABASE_URL)
    assert main(argv) == 0
    out = capsys.readouterr().out
    assert SECRET_PASSWORD not in out
    assert SECRET_USER not in out
    assert SECRET_DATABASE_URL not in out
