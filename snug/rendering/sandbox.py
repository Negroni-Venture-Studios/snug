"""Run the PDF worker in its own process, with a clean environment and a timeout."""

import json
import os
import signal
import subprocess
import sys
from typing import Any

INSPECT_TIMEOUT = 60
RENDER_TIMEOUT = 30

# What a user sees for each way a PDF can fail.
MESSAGES = {
    "password": "This PDF is protected by a password. Remove the password, then upload it again.",
    "empty": "This PDF has no pages.",
    "too_many_pages": "This PDF has more than 2,000 pages, which is more than Snug can show.",
    "unreadable": "This file could not be read as a PDF. Export it to PDF again, then upload it.",
    "timeout": "This PDF took too long to read. Export it to PDF again, then upload it.",
    "memory": "This PDF needs more memory to read than Snug allows.",
    "no_such_page": "That page does not exist.",
    "usage": "Snug could not read this PDF.",
}


class WorkerError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(MESSAGES.get(code, MESSAGES["unreadable"]))


def worker_env() -> dict[str, str]:
    """The worker's whole environment: no secret key, no settings, no credentials."""
    return {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}


def command(*args: str) -> list[str]:
    # -I: isolated mode, ignoring PYTHON* variables, the user site and the cwd.
    return [sys.executable, "-I", "-m", "snug.rendering.worker", *args]


def run(*args: str, timeout: float) -> dict[str, Any]:
    try:
        process = subprocess.run(  # noqa: S603 - fixed argv, no shell
            command(*args),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            env=worker_env(),
            close_fds=True,
            start_new_session=True,
            check=False,
        )
    except subprocess.TimeoutExpired as expired:
        raise WorkerError("timeout") from expired

    if process.returncode in (-signal.SIGKILL, -signal.SIGXCPU, -signal.SIGSEGV):
        raise WorkerError("memory" if process.returncode == -signal.SIGKILL else "unreadable")
    try:
        result: dict[str, Any] = json.loads(process.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as error:
        raise WorkerError("unreadable") from error
    if "error" in result:
        raise WorkerError(str(result["error"]))
    return result


def inspect(path: os.PathLike[str] | str) -> dict[str, Any]:
    return run("inspect", os.fspath(path), timeout=INSPECT_TIMEOUT)


def render(
    path: os.PathLike[str] | str, page: int, width: int, out: os.PathLike[str] | str
) -> dict[str, Any]:
    return run(
        "render", os.fspath(path), str(page), str(width), os.fspath(out), timeout=RENDER_TIMEOUT
    )
