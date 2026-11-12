"""The LibreOffice converter's supervisor: convert one file, then exit.

    python3 -I -m snug.office.supervisor

It runs in a container of its own (decided 12 November 2026), under gVisor,
with no network, a read-only root, its own user, and only two folders shared
with Snug: the inbox, which it can read and not change, and the outbox, which
it can write. Docker starts a fresh container each time it exits, so no
process and no temporary file survives from one conversion to the next.

Snug puts one job in the inbox at a time: a folder named by a random token,
holding `job.json` ({"kind": "docx"}, "pptx" or "xlsx") and `input.<kind>`.
The supervisor copies the input to its own temporary folder, gives
LibreOffice a fresh profile from a hardened template, and runs it in a child
process with a seccomp filter (Unix sockets only, no reach into another
process), CPU and file-size limits, and a deadline. It forces the import
filter from the kind Snug's own checker found, so LibreOffice never chooses
one from the file's contents. It writes `<token>.pdf`, or `<token>.error`
holding {"error": code}, to the outbox, renaming each into place, and Snug
reads the PDF in its own confined worker like any upload.

Standard library only: the image carries this module and
snug.rendering.seccomp, with empty package files, and none of Snug.
"""

import contextlib
import json
import os
import re
import resource
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from snug.rendering import seccomp

INBOX = Path("/exchange/inbox")
OUTBOX = Path("/exchange/outbox")
TEMPLATE = Path("/opt/snug-office/profile")
FACTS = Path("/etc/snug-office.json")
SOFFICE = "/usr/lib/libreoffice/program/soffice.bin"

# The import filter and the PDF export for each kind, forced so that
# LibreOffice never picks a filter from the file's contents.
FILTERS = {
    "docx": ("MS Word 2007 XML", "pdf:writer_pdf_Export"),
    "pptx": ("Impress MS PowerPoint 2007 XML", "pdf:impress_pdf_Export"),
    "xlsx": (
        "Calc MS Excel 2007 XML",
        'pdf:calc_pdf_Export:{"SinglePageSheets":{"type":"boolean","value":"true"}}',
    ),
}
TOKEN = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
DEADLINE = 120  # seconds of wall clock for one conversion
CPU_SECONDS = 120
WRITTEN_BYTES = 512 * 1024 * 1024  # the most any file LibreOffice writes may hold
MAX_PDF = 512 * 1024 * 1024
# Docker doubles its restart delay for a container that ran less than 10 s.
MIN_LIFETIME = 11
POLL = 0.5
HEARTBEAT = 30


class Job:
    """One file to convert, as Snug left it in the inbox."""

    def __init__(self, token: str, kind: str, source: Path) -> None:
        self.token, self.kind, self.source = token, kind, source


def next_job(inbox: Path, outbox: Path) -> Job | None:
    """The first job in the inbox that has no answer yet, or None.

    Anything else in the inbox is ignored: a name that is not a token, a
    folder still being written (no job.json yet), or an unknown kind.
    """
    for folder in sorted(inbox.iterdir()):
        token = folder.name
        if not TOKEN.match(token) or not folder.is_dir():
            continue
        if (outbox / f"{token}.pdf").exists() or (outbox / f"{token}.error").exists():
            continue
        try:
            with (folder / "job.json").open("rb") as handle:
                kind = json.loads(handle.read(4096)).get("kind")
        except (OSError, ValueError, AttributeError):
            continue
        if kind in FILTERS and (folder / f"input.{kind}").is_file():
            return Job(token, kind, folder / f"input.{kind}")
    return None


def _is_linux() -> bool:
    """A function, so type checkers on other systems still check the Linux branch."""
    return sys.platform == "linux"


def confine_child() -> None:  # pragma: no cover - runs only in the child, after fork
    """Limits and the seccomp filter, for the process about to become LibreOffice."""
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS))
    resource.setrlimit(resource.RLIMIT_FSIZE, (WRITTEN_BYTES, WRITTEN_BYTES))
    if not _is_linux():
        return
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
        raise OSError("no_new_privs refused")
    program = seccomp.seccomp_filter(os.uname().machine, os.getpid(), unix_sockets=True)
    if program is None:
        raise OSError("no seccomp filter for this architecture")

    class SockFprog(ctypes.Structure):
        _fields_ = (("len", ctypes.c_ushort), ("filter", ctypes.c_void_p))

    buffer = ctypes.create_string_buffer(program, len(program))
    fprog = SockFprog(len(program) // 8, ctypes.cast(buffer, ctypes.c_void_p))
    if libc.prctl(22, 2, ctypes.byref(fprog), 0, 0) != 0:  # PR_SET_SECCOMP, MODE_FILTER
        raise OSError("seccomp refused")


def convert(
    job: Job,
    *,
    work_root: Path,
    template: Path,
    soffice: str,
    confine: Callable[[], None] | None = confine_child,
    deadline: float = DEADLINE,
) -> bytes | str:
    """The PDF LibreOffice makes of one job, or an error code."""
    infilter, export = FILTERS[job.kind]
    work = Path(tempfile.mkdtemp(prefix="job-", dir=work_root))
    try:
        source = work / f"input.{job.kind}"
        shutil.copyfile(job.source, source)
        profile = work / "profile"
        shutil.copytree(template, profile / "user")  # fresh for every file
        (work / "home").mkdir()
        out = work / "out"
        out.mkdir()
        argv = [
            soffice,
            "--headless",
            "--invisible",
            "--nologo",
            "--norestore",
            "--nolockcheck",
            "--nodefault",
            "--nofirststartwizard",
            f"-env:UserInstallation={profile.as_uri()}",
            f"--infilter={infilter}",
            "--convert-to",
            export,
            "--outdir",
            str(out),
            str(source),
        ]
        environment = {
            "HOME": str(work / "home"),
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "SAL_USE_VCLPLUGIN": "svp",  # draw to memory: there is no display
            "TMPDIR": str(work),
        }
        # Held open from before LibreOffice runs and read from this handle, never
        # reopened by name: LibreOffice could swap the name for a link or a pipe.
        errors = os.open(
            work / "errors.log",
            os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            process = subprocess.Popen(  # noqa: S603 - a fixed program, with the job's paths only
                argv,
                cwd=work,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=errors,
                start_new_session=True,
                preexec_fn=confine,  # the supervisor runs no threads, so this is safe
            )
        except subprocess.SubprocessError:
            # The child could not apply its limits or filter: fail closed.
            os.close(errors)
            return "unconfined"
        try:
            try:
                code = process.wait(timeout=deadline)
            except subprocess.TimeoutExpired:
                _kill(process)
                return "timeout"
            _kill(process)  # anything it left in its group
            result = _read_output(out) if code == 0 else "unreadable"
            if isinstance(result, str):
                _report(job, code, errors)
            return result
        finally:
            os.close(errors)
    except OSError:
        return "unreadable"
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _report(job: Job, code: int, errors: int) -> None:
    """Say on the container's log why a conversion failed, with the end of LibreOffice's own errors.

    `errors` is the descriptor LibreOffice wrote its errors to. The log holds
    no document content: LibreOffice writes its warnings there, not the
    file's text.
    """
    tail = ""
    try:
        facts = os.fstat(errors)
        if stat.S_ISREG(facts.st_mode):
            os.lseek(errors, max(0, facts.st_size - 4096), os.SEEK_SET)
            tail = os.read(errors, 4096).decode("utf-8", errors="replace").strip()
    except OSError:
        pass
    sys.stderr.write(f"{job.kind} conversion failed, LibreOffice exit {code}\n{tail}\n")


def _read_output(out: Path) -> bytes | str:
    """The PDF LibreOffice wrote, read without following anything it could have planted.

    LibreOffice runs as the supervisor's own user, so a hostile file could
    swap the output folder or the PDF for a link, or leave a pipe that would
    block a read. The folder and the file are opened without following
    links, the file must be a regular one, and no more than MAX_PDF is read.
    """
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        folder = os.open(out, os.O_RDONLY | os.O_DIRECTORY | nofollow)
    except OSError:
        return "unreadable"
    try:
        handle = os.open("input.pdf", os.O_RDONLY | nofollow | os.O_NONBLOCK, dir_fd=folder)
    except OSError:
        return "unreadable"
    finally:
        os.close(folder)
    try:
        facts = os.fstat(handle)
        if not stat.S_ISREG(facts.st_mode):
            return "unreadable"
        if facts.st_size > MAX_PDF:
            return "too_large"
        chunks, total = [], 0
        while chunk := os.read(handle, 1024 * 1024):
            total += len(chunk)
            if total > MAX_PDF:
                return "too_large"
            chunks.append(chunk)
    finally:
        os.close(handle)
    data = b"".join(chunks)
    return data if data.startswith(b"%PDF-") else "unreadable"


def _kill(process: subprocess.Popen[bytes]) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signal.SIGKILL)
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=5)


def _write(outbox: Path, name: str, data: bytes) -> None:
    """Write a file to the outbox under a temporary name, then rename it into place."""
    partial = outbox / f".{name}.partial"
    partial.write_bytes(data)
    partial.chmod(0o644)
    partial.replace(outbox / name)


def publish(outbox: Path, token: str, result: bytes | str) -> None:
    if isinstance(result, bytes):
        _write(outbox, f"{token}.pdf", result)
    else:
        _write(outbox, f"{token}.error", json.dumps({"error": result}).encode())


def status(facts_file: Path, started: datetime) -> dict[str, object]:
    """For Settings and `snug doctor`: the LibreOffice version, the image's age, the kernel."""
    try:
        facts = json.loads(facts_file.read_text())
    except (OSError, ValueError):
        facts = {}
    return {
        "libreoffice": str(facts.get("libreoffice", "")),
        "built": str(facts.get("built", "")),
        "started": started.isoformat(timespec="seconds"),
        "heartbeat": datetime.now(UTC).isoformat(timespec="seconds"),
        # gVisor reports its own kernel release, not the server's.
        "kernel": os.uname().release,
        "seccomp": "unix sockets only" if _is_linux() else "unavailable",
    }


def serve(
    *,
    inbox: Path = INBOX,
    outbox: Path = OUTBOX,
    work_root: Path | None = None,
    template: Path = TEMPLATE,
    facts_file: Path = FACTS,
    soffice: str = SOFFICE,
    confine: Callable[[], None] | None = confine_child,
    min_lifetime: float = MIN_LIFETIME,
    poll: float = POLL,
    patience: float | None = None,
) -> int:
    """Wait for one job, convert it, answer, and return once the container has lived long enough.

    `patience` bounds the wait, for tests; the container waits for ever.
    """
    began = time.monotonic()
    started = datetime.now(UTC)
    _write(outbox, "status.json", json.dumps(status(facts_file, started)).encode())
    beat = time.monotonic()
    root = work_root or Path(tempfile.gettempdir())
    while (job := next_job(inbox, outbox)) is None:
        if time.monotonic() - beat >= HEARTBEAT:
            _write(outbox, "status.json", json.dumps(status(facts_file, started)).encode())
            beat = time.monotonic()
        if patience is not None and time.monotonic() - began > patience:
            return 1
        time.sleep(poll)
    result = convert(job, work_root=root, template=template, soffice=soffice, confine=confine)
    publish(outbox, job.token, result)
    time.sleep(max(0.0, min_lifetime - (time.monotonic() - began)))
    return 0


if __name__ == "__main__":  # pragma: no cover - the container's command
    sys.exit(serve())
