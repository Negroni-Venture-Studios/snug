"""Run the PDF worker in its own confined process, with a clean environment and a timeout.

The worker is handed the PDF as an already-open file descriptor and returns
its results through a pipe. It is never given a path, so with Landlock it has
no filesystem access at all beyond reading system fonts.
"""

import contextlib
import json
import os
import selectors
import signal
import subprocess
import sys
import time
from typing import Any

from snug.rendering.worker import OUTPUT_BYTES

INSPECT_TIMEOUT = 60
RENDER_TIMEOUT = 30
# Snug never holds more of the worker's output than this, however much it sends.
READ_LIMIT = OUTPUT_BYTES + 64 * 1024
CHUNK = 64 * 1024

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


def run(
    pdf: os.PathLike[str] | str, mode: str, *extra: str, timeout: float, limit: int = READ_LIMIT
) -> bytes:
    """Run the worker on one PDF and return what it wrote. Raises WorkerError on any failure.

    The output is read in chunks against a deadline. If the worker sends more
    than `limit` bytes, or runs past the deadline, its whole process group is
    killed at once: a compromised worker cannot exhaust Snug's memory.
    """
    with open(pdf, "rb") as source:  # noqa: PTH123 - we need the raw descriptor
        descriptor = source.fileno()
        process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            command(mode, str(descriptor), *extra),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=worker_env(),
            close_fds=True,
            pass_fds=(descriptor,),
            start_new_session=True,
        )
    try:
        output = _read_bounded(process, timeout=timeout, limit=limit)
    finally:
        if process.poll() is None:
            _kill(process)
        if process.stdout is not None:
            process.stdout.close()

    if process.returncode in (-signal.SIGKILL, -signal.SIGXCPU, -signal.SIGSEGV):
        raise WorkerError("memory" if process.returncode == -signal.SIGKILL else "unreadable")
    if process.returncode != 0:
        raise WorkerError(_error_code(output))
    return output


def _kill(process: subprocess.Popen[bytes]) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)  # its own session, so its whole group
    process.wait()


def _read_bounded(process: subprocess.Popen[bytes], *, timeout: float, limit: int) -> bytes:
    assert process.stdout is not None  # noqa: S101 - Popen was given stdout=PIPE
    deadline = time.monotonic() + timeout
    chunks: list[bytes] = []
    received = 0
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _kill(process)
                raise WorkerError("timeout")
            if not selector.select(timeout=remaining):
                continue
            chunk = os.read(process.stdout.fileno(), CHUNK)
            if not chunk:
                break
            received += len(chunk)
            if received > limit:
                _kill(process)
                raise WorkerError("unreadable")
            chunks.append(chunk)
    try:
        process.wait(timeout=max(deadline - time.monotonic(), 0.1))
    except subprocess.TimeoutExpired as expired:
        _kill(process)
        raise WorkerError("timeout") from expired
    return b"".join(chunks)


def _error_code(output: bytes) -> str:
    try:
        reported = json.loads(output.decode("utf-8", errors="replace").strip().splitlines()[-1])
        return str(reported["error"])
    except (IndexError, KeyError, TypeError, json.JSONDecodeError):
        return "unreadable"


def inspect(pdf: os.PathLike[str] | str) -> dict[str, Any]:
    output = run(pdf, "inspect", timeout=INSPECT_TIMEOUT)
    try:
        facts: dict[str, Any] = json.loads(output.decode("utf-8").strip())
        if not isinstance(facts.get("pages"), int) or len(facts.get("sizes", [])) != facts["pages"]:
            raise ValueError("inconsistent")
    except (UnicodeDecodeError, ValueError, AttributeError) as error:
        raise WorkerError("unreadable") from error
    return facts


def render(pdf: os.PathLike[str] | str, page: int, width: int) -> bytes:
    """The page as WebP bytes. Only the container format is checked; nothing is decoded."""
    image = run(pdf, "render", str(page), str(width), timeout=RENDER_TIMEOUT)
    if len(image) > OUTPUT_BYTES or image[:4] != b"RIFF" or image[8:12] != b"WEBP":
        raise WorkerError("unreadable")
    return image
