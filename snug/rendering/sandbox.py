"""Run the PDF worker in its own confined process, with a clean environment and a timeout.

The worker is handed the PDF as an already-open file descriptor and returns
its results through a pipe. It is never given a path, so with Landlock it has
no filesystem access at all beyond reading system fonts.
"""

import contextlib
import json
import logging
import os
import selectors
import signal
import subprocess
import sys
import time
from typing import Any

from snug.rendering.worker import ALLOW_UNCONFINED, MIN_LANDLOCK_ABI, OUTPUT_BYTES

log = logging.getLogger(__name__)

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
    "unconfined": (
        "This server cannot open documents safely: Snug reads every PDF in a confined "
        "process, which needs Linux 6.7 or later with Landlock. Ask whoever runs the server."
    ),
}


# And for an Excel workbook, where the PDF wording would mislead.
WORKBOOK_MESSAGES = {
    "not_xlsx": (
        "This file could not be read as an Excel workbook. Save it as an Excel Workbook "
        "(.xlsx), without a password, then upload it again."
    ),
    "macros": (
        "This workbook contains macros. Save it as an Excel Workbook (.xlsx), then upload it again."
    ),
    "no_sheets": "This workbook has no visible sheets.",
    "too_large": "This workbook unpacks to more than Snug allows.",
    "unreadable": (
        "This file could not be read as an Excel workbook. Save it as an Excel Workbook "
        "(.xlsx), without a password, then upload it again."
    ),
    "timeout": "This workbook took too long to read.",
    "memory": "This workbook needs more memory to read than Snug allows.",
}


class WorkerError(Exception):
    def __init__(self, code: str, messages: dict[str, str] | None = None) -> None:
        self.code = code
        found = (messages or {}).get(code) or MESSAGES.get(code, MESSAGES["unreadable"])
        super().__init__(found)


def worker_env() -> dict[str, str]:
    """The worker's whole environment: no secret key, no settings, no credentials."""
    return {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}


def command(*args: str) -> list[str]:
    from django.conf import settings

    # Only with SNUG_UNSAFE_UNCONFINED_RENDERING, for development without Landlock.
    # Without Snug's settings (a script calling the sandbox directly), confined.
    allowed = settings.configured and settings.CONFIG.unsafe_unconfined_rendering
    unsafe = [ALLOW_UNCONFINED] if allowed else []
    # -I: isolated mode, ignoring PYTHON* variables, the user site and the cwd.
    return [sys.executable, "-I", "-m", "snug.rendering.worker", *unsafe, *args]


def run(
    pdf: os.PathLike[str] | str,
    mode: str,
    *extra: str,
    timeout: float,
    limit: int = READ_LIMIT,
    also: tuple[os.PathLike[str] | str, ...] = (),
) -> bytes:
    """Run the worker on one PDF and return what it wrote. Raises WorkerError on any failure.

    The output is read in chunks against a deadline. If the worker sends more
    than `limit` bytes, or runs past the deadline, its whole process group is
    killed at once: a compromised worker cannot exhaust Snug's memory.
    """
    with contextlib.ExitStack() as files:
        # `also` passes further files, such as a font, the same way: open.
        opened = [files.enter_context(open(path, "rb")) for path in (pdf, *also)]  # noqa: PTH123
        descriptors = tuple(handle.fileno() for handle in opened)
        process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            command(mode, *(str(d) for d in descriptors), *extra),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=worker_env(),
            close_fds=True,
            pass_fds=descriptors,
            start_new_session=True,
        )
    try:
        output = _read_bounded(process, timeout=timeout, limit=limit)
    finally:
        # Unreaped means the group ID is still ours: kill whatever is left.
        if process.returncode is None:
            _kill(process)
        if process.stdout is not None:
            process.stdout.close()

    if process.returncode in (-signal.SIGKILL, -signal.SIGXCPU, -signal.SIGSEGV):
        raise WorkerError("memory" if process.returncode == -signal.SIGKILL else "unreadable")
    if process.returncode != 0:
        code = _error_code(output)
        if code == "unconfined":
            log.error(
                "The PDF worker could not confine itself (Landlock ABI %s or later and a "
                "seccomp filter are needed), so it refused to read the file.",
                MIN_LANDLOCK_ABI,
            )
        raise WorkerError(code)
    return output


def _kill(process: subprocess.Popen[bytes]) -> None:
    """Kill the worker's whole process group, then reap the worker.

    Call only while the worker is unreaped: until then its process group ID
    cannot be reused, so the signal can only reach the worker's own group.
    """
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signal.SIGKILL)  # its own session, so its whole group
    process.wait()


def _await_exit(process: subprocess.Popen[bytes], deadline: float) -> None:
    """Wait for the worker to exit without reaping it, so its group ID stays reserved.

    Linux has waitid(WNOWAIT). Python on macOS does not, so there the worker is
    reaped, and the group is swept afterwards (a negligible race on a developer's
    machine; production runs on Linux).
    """
    waitid = getattr(os, "waitid", None)
    if waitid is None:
        try:
            process.wait(timeout=max(deadline - time.monotonic(), 0.1))
        except subprocess.TimeoutExpired as expired:
            _kill(process)
            raise WorkerError("timeout") from expired
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGKILL)
        return
    flags = os.WEXITED | os.WNOHANG | os.WNOWAIT
    while waitid(os.P_PID, process.pid, flags) is None:
        if time.monotonic() > deadline:
            _kill(process)
            raise WorkerError("timeout")
        time.sleep(0.01)


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
    # The worker has closed its output. Anything it started (it should not be
    # able to: its process limit is zero) is killed before the worker is reaped.
    _await_exit(process, deadline)
    if process.returncode is None:
        _kill(process)
    return b"".join(chunks)


def _error_code(output: bytes) -> str:
    try:
        reported = json.loads(output.decode("utf-8", errors="replace").strip().splitlines()[-1])
        return str(reported["error"])
    except (IndexError, KeyError, TypeError, json.JSONDecodeError):
        return "unreadable"


def stamp(
    page: os.PathLike[str] | str,
    font: os.PathLike[str] | str,
    *,
    email: str,
    date: str,
    ref: str,
) -> bytes:
    """A rendered page with the viewer's watermark, as WebP, stamped in a confined worker."""
    image = run(page, "stamp", email, date, ref, timeout=RENDER_TIMEOUT, also=(font,))
    if len(image) > OUTPUT_BYTES or image[:4] != b"RIFF" or image[8:12] != b"WEBP":
        raise WorkerError("unreadable")
    return image


def stamp_pdf(
    pdf: os.PathLike[str] | str,
    font: os.PathLike[str] | str,
    *,
    email: str,
    date: str,
    ref: str,
    limit: int,
) -> bytes:
    """The original PDF, marked for the viewer on every page, from a confined worker."""
    stamped = run(
        pdf, "stamp-pdf", email, date, ref, timeout=INSPECT_TIMEOUT, limit=limit, also=(font,)
    )
    if len(stamped) > limit or not stamped.startswith(b"%PDF-"):
        raise WorkerError("unreadable")
    return stamped


def _picture(image: os.PathLike[str] | str, mode: str) -> bytes:
    drawn = run(image, mode, timeout=RENDER_TIMEOUT)
    if len(drawn) > OUTPUT_BYTES or not drawn.startswith(b"\x89PNG\r\n\x1a\n"):
        raise WorkerError("unreadable")
    return drawn


def logo(image: os.PathLike[str] | str) -> bytes:
    """The founder's logo, re-encoded as PNG by a confined worker."""
    return _picture(image, "logo")


def icon(image: os.PathLike[str] | str) -> bytes:
    """The founder's tab icon, re-encoded as a square PNG by a confined worker."""
    return _picture(image, "icon")


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


def _for_workbook(error: WorkerError) -> WorkerError:
    return WorkerError(error.code, WORKBOOK_MESSAGES)


def inspect_xlsx(workbook: os.PathLike[str] | str) -> dict[str, Any]:
    """The names of an Excel workbook's visible sheets, read in a confined worker."""
    try:
        output = run(workbook, "inspect-xlsx", timeout=INSPECT_TIMEOUT)
        facts: dict[str, Any] = json.loads(output.decode("utf-8").strip())
        sheets = facts.get("sheets")
        if (
            not isinstance(sheets, list)
            or not sheets
            or not all(isinstance(n, str) for n in sheets)
        ):
            raise ValueError("inconsistent")
    except WorkerError as error:
        raise _for_workbook(error) from error
    except (UnicodeDecodeError, ValueError, AttributeError) as error:
        raise WorkerError("unreadable", WORKBOOK_MESSAGES) from error
    return facts


def stamp_xlsx(
    workbook: os.PathLike[str] | str, *, email: str, date: str, ref: str, limit: int
) -> bytes:
    """The workbook with its properties naming the reader, from a confined worker."""
    try:
        stamped = run(
            workbook, "stamp-xlsx", email, date, ref, timeout=INSPECT_TIMEOUT, limit=limit
        )
    except WorkerError as error:
        raise _for_workbook(error) from error
    if len(stamped) > limit or not stamped.startswith(b"PK\x03\x04"):
        raise WorkerError("unreadable", WORKBOOK_MESSAGES)
    return stamped


def confinement() -> str:
    """How a fresh worker confines itself, such as "landlock v6, no sockets".

    For `snug doctor`. The worker reads no file in this mode, so its output
    is trusted as far as its length.
    """
    try:
        done = subprocess.run(  # noqa: S603 - fixed argv, no shell
            command("confinement"),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            env=worker_env(),
            timeout=RENDER_TIMEOUT,
            check=False,
            start_new_session=True,
        )
    except subprocess.TimeoutExpired:
        return "unconfined: the worker did not answer"
    how = done.stdout.decode("ascii", "replace").strip()[:100]
    return how if done.returncode == 0 and how else "unconfined: the worker failed"
