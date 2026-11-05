"""The snug command: clear configuration errors, and serve migrates first."""

import os
import secrets
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

import pytest
from django.conf import settings
from django.core.management import call_command

from snug.__main__ import main
from snug.core.management.commands import serve


def run_snug(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("SNUG_", "DJANGO_"))}
    return subprocess.run(
        [sys.executable, "-m", "snug", *args],
        env={**clean, **(env or {})},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )


def write_env(tmp_path: Path) -> Path:
    path = tmp_path / "snug.env"
    path.write_text(
        f"SNUG_SECRET_KEY={secrets.token_urlsafe(64)}\n"
        "SNUG_DOMAIN=room.example.com\n"
        f"SNUG_DATA_DIR={tmp_path / 'data'}\n",
        encoding="utf-8",
    )
    return path


def test_missing_settings_are_explained_not_traced() -> None:
    result = run_snug("check")
    assert result.returncode == 2
    assert "Snug cannot start." in result.stderr
    assert "SNUG_SECRET_KEY is not set" in result.stderr
    assert "SNUG_DOMAIN is not set" in result.stderr
    assert "Traceback" not in result.stderr


def test_unreadable_env_file_is_explained(tmp_path: Path) -> None:
    result = run_snug("--env-file", str(tmp_path / "missing.env"), "check")
    assert result.returncode == 2
    assert "cannot read the environment file" in result.stderr


def test_production_settings_pass_djangos_deployment_checks(tmp_path: Path) -> None:
    result = run_snug(
        "--env-file", str(write_env(tmp_path)), "check", "--deploy", "--fail-level", "WARNING"
    )
    assert result.returncode == 0, result.stderr
    assert "no issues" in result.stdout


def test_env_file_flag_needs_a_path(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--env-file"]) == 2
    assert "--env-file needs a path" in capsys.readouterr().err


def test_malformed_env_file_is_explained(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "snug.env"
    path.write_text("this is not a setting\n", encoding="utf-8")
    assert main(["--env-file", str(path)]) == 2
    assert "line 1: expected KEY=value" in capsys.readouterr().err


def test_missing_env_file_is_explained_in_process(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--env-file", str(tmp_path / "missing.env"), "check"]) == 2
    assert "cannot read the environment file" in capsys.readouterr().err


@pytest.mark.django_db
def test_main_runs_management_commands(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check"]) == 0
    assert "no issues" in capsys.readouterr().out


def test_main_creates_a_private_data_folder(tmp_path: Path) -> None:
    env_file = write_env(tmp_path)
    result = run_snug("--env-file", str(env_file), "check")
    assert result.returncode == 0, result.stderr
    data_dir = tmp_path / "data"
    assert data_dir.is_dir()
    assert data_dir.stat().st_mode & 0o777 == 0o700


def test_unwritable_data_folder_is_explained(tmp_path: Path) -> None:
    blocker = tmp_path / "a-file"
    blocker.write_text("not a folder", encoding="utf-8")
    env_file = tmp_path / "snug.env"
    env_file.write_text(
        f"SNUG_SECRET_KEY={secrets.token_urlsafe(64)}\n"
        "SNUG_DOMAIN=room.example.com\n"
        f"SNUG_DATA_DIR={blocker / 'data'}\n",
        encoding="utf-8",
    )
    result = run_snug("--env-file", str(env_file), "check")
    assert result.returncode == 2
    assert "cannot create the data folder" in result.stderr
    assert "Traceback" not in result.stderr


def test_wsgi_application_loads() -> None:
    from snug.wsgi import application

    assert callable(application)


@pytest.mark.django_db
def test_serve_migrates_before_handing_over_to_gunicorn(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    argv: list[str] = []

    def fake_call_command(name: str, **options: object) -> None:
        events.append(name)

    def fake_execv(path: str, args: list[str]) -> NoReturn:
        events.append("exec")
        argv.extend(args)
        raise SystemExit(0)

    monkeypatch.setattr(serve, "call_command", fake_call_command)
    monkeypatch.setattr("os.execv", fake_execv)

    with pytest.raises(SystemExit):
        call_command("serve", "--bind", "127.0.0.1:9000", "--workers", "3")

    assert events == ["migrate", "exec"]
    assert argv[1:4] == ["-m", "gunicorn", "snug.wsgi:application"]
    assert argv[argv.index("--bind") + 1] == "127.0.0.1:9000"
    assert argv[argv.index("--workers") + 1] == "3"
    assert not any("access-log" in a for a in argv)
    assert settings.CONFIG.data_dir.is_dir()
