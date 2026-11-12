"""Snug's side of the LibreOffice converter: a queue of Office files, one at a time.

When the converter is installed (SNUG_OFFICE), each Word, PowerPoint or
Excel upload is queued for a preview. `pump` moves the queue on one step:
it collects the converter's answer for the job in hand, if there is one, and
hands the converter the next file. Anything can call it (the thread an
upload starts, the document page while it waits, housekeeping); a lock
file keeps two processes from running it at once.

The converter reads LibreOffice's output from a hostile file, so its answer
is untrusted: Snug opens it without following links, takes only the answer
for the job it is waiting for, and has its own confined worker read the PDF
before it becomes the version's pages. The job and its answer are deleted
once read, so the converter only ever sees one file.
"""

import contextlib
import fcntl
import json
import logging
import os
import re
import secrets
import shutil
import stat
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.db import connections, transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone

from snug.documents import storage
from snug.documents.models import OFFICE, DocumentVersion
from snug.rendering import sandbox

log = logging.getLogger(__name__)

Preview = DocumentVersion.Preview
# The converter allows itself two minutes for a file and Docker a few
# seconds to start it again; an answer later than this is not coming.
STALE = timedelta(minutes=5)
MAX_ANSWER = 512 * 1024 * 1024
# The error codes the converter sends; anything else is read as "unreadable".
ERRORS = frozenset({"timeout", "unreadable", "too_large", "unconfined"})
STATUS = "status.json"
# How long the thread an upload starts keeps the queue moving.
DRIVE_FOR = timedelta(minutes=15)


# What the admin sees when a preview could not be made. The file is shared as a download either way.
MESSAGES = {
    "timeout": "LibreOffice took longer than two minutes with this file.",
    "unreadable": "LibreOffice could not read this file.",
    "too_large": "The PDF LibreOffice made is larger than Snug allows.",
    "unconfined": (
        "The converter could not confine LibreOffice, so it did not open the file. "
        "Run sudo snug doctor on the server."
    ),
    "no_answer": (
        "The converter did not answer within five minutes. Run sudo snug doctor on the server."
    ),
}
WORKER_FAILED = "Snug could not read the PDF LibreOffice made of this file."


def message(code: str) -> str:
    return MESSAGES.get(code, WORKER_FAILED)


def ahead(version: DocumentVersion) -> int:
    """How many files the converter will take before this one."""
    earlier = DocumentVersion.objects.filter(preview=Preview.PENDING).filter(
        uploaded_at__lt=version.uploaded_at
    ) | DocumentVersion.objects.filter(
        preview=Preview.PENDING, uploaded_at=version.uploaded_at, pk__lt=version.pk
    )
    converting = DocumentVersion.objects.filter(preview=Preview.CONVERTING).exclude(pk=version.pk)
    return earlier.count() + converting.count()


def stage(version: DocumentVersion) -> str:
    """Where a waiting preview has got to, in words for the Preview panel."""
    if version.preview == Preview.CONVERTING:
        return "Converting in LibreOffice"
    waiting = ahead(version)
    if waiting:
        return f"Waiting for the converter, {waiting} file{'' if waiting == 1 else 's'} ahead"
    return "Waiting for the converter"


def enabled() -> bool:
    return bool(settings.CONFIG.office)


def wanted(kind: str) -> bool:
    """Whether a new version of this kind is queued for a preview."""
    return enabled() and kind in OFFICE


def preview_path(version: DocumentVersion) -> Path:
    return storage.preview_path(version)


def _inbox() -> Path:
    path: Path = settings.CONFIG.office_dir / "inbox"
    return path


def _outbox() -> Path:
    path: Path = settings.CONFIG.office_dir / "outbox"
    return path


@contextlib.contextmanager
def _lock() -> Iterator[bool]:
    """True while this process holds the queue; False if another does."""
    path = settings.CONFIG.data_dir / "office.lock"
    with path.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def pump(*, now: datetime | None = None) -> None:
    """Move the queue on one step: collect an answer, then hand over the next file."""
    now = now or timezone.now()
    with _lock() as held:
        if not held:
            return
        if not enabled():
            _release_all()
            return
        current = DocumentVersion.objects.filter(preview=Preview.CONVERTING).first()
        _clear_strays(current.preview_token if current else "")
        if current is not None:
            answer = _answer(current.preview_token)
            if answer is None:
                if current.preview_since is None or now - current.preview_since > STALE:
                    _finish(current, "no_answer")
                return
            _finish(current, answer)
        _queue_unconverted()
        waiting = DocumentVersion.objects.filter(preview=Preview.PENDING)
        following = waiting.order_by("uploaded_at", "pk").first()
        if following is not None:
            _submit(following, now)


def _queue_unconverted() -> None:
    """Queue Office files uploaded before the converter was installed.

    Only the version links show: an earlier one is never read, so it is not
    worth converting. A preview that failed is not queued again by itself;
    the document's page offers Try again.
    """
    ready = DocumentVersion.Status.READY
    newer = DocumentVersion.objects.filter(
        document=OuterRef("document"), status=ready, number__gt=OuterRef("number")
    )
    DocumentVersion.objects.filter(
        kind__in=list(OFFICE), preview=Preview.NONE, status=ready
    ).exclude(Exists(newer)).update(preview=Preview.PENDING)


def busy() -> bool:
    """Whether any version is still waiting for, or having, its preview made."""
    return DocumentVersion.objects.filter(
        preview__in=(Preview.PENDING, Preview.CONVERTING)
    ).exists()


def drive_in_background() -> threading.Thread | None:
    """Keep the queue moving for a while after an upload, so a preview arrives unwatched.

    Housekeeping picks up anything left once this stops.
    """
    if not enabled():
        return None

    def run() -> None:
        try:
            stop = time.monotonic() + DRIVE_FOR.total_seconds()
            while time.monotonic() < stop:
                pump()
                if not busy():
                    return
                time.sleep(1)
        except Exception:
            log.exception("The converter queue stopped")
        finally:
            connections.close_all()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def _submit(version: DocumentVersion, now: datetime) -> None:
    """Put one file in the inbox, under a fresh random name, all at once."""
    kind = version.kind
    token = secrets.token_urlsafe(24)
    inbox = _inbox()
    staging = inbox / f".staging-{token}"  # not a token: the converter skips it
    staging.mkdir(mode=0o755)
    try:
        shutil.copyfile(storage.original_path(version), staging / f"input.{kind}")
        (staging / f"input.{kind}").chmod(0o644)
        (staging / "job.json").write_text(json.dumps({"kind": kind}))
        (staging / "job.json").chmod(0o644)
        staging.replace(inbox / token)
    except OSError:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    version.preview = Preview.CONVERTING
    version.preview_token = token
    version.preview_since = now
    version.save(update_fields=["preview", "preview_token", "preview_since"])


def _read_answer(name: str, limit: int) -> bytes | None:
    """One answer file from the outbox, opened without following links, or None."""
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        folder = os.open(_outbox(), os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return None
    try:
        handle = os.open(name, os.O_RDONLY | nofollow | os.O_NONBLOCK, dir_fd=folder)
    except FileNotFoundError:
        return None
    except OSError:
        return b""  # there, but a link or worse: an answer, and not a usable one
    finally:
        os.close(folder)
    try:
        facts = os.fstat(handle)
        if not stat.S_ISREG(facts.st_mode) or facts.st_size > limit:
            return b""
        chunks, total = [], 0
        while chunk := os.read(handle, 1024 * 1024):
            total += len(chunk)
            if total > limit:
                return b""
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(handle)


def _answer(token: str) -> bytes | str | None:
    """The converter's answer for this job: a PDF's bytes, an error code, or None yet."""
    pdf = _read_answer(f"{token}.pdf", MAX_ANSWER)
    if pdf is not None:
        return pdf if pdf.startswith(b"%PDF-") else "unreadable"
    error = _read_answer(f"{token}.error", 4096)
    if error is None:
        return None
    try:
        code = json.loads(error).get("error")
    except (ValueError, AttributeError):
        return "unreadable"
    return code if code in ERRORS else "unreadable"


def _finish(version: DocumentVersion, answer: bytes | str) -> None:
    """Record the answer, and clear the job and its answer from the shared folders."""
    token = version.preview_token
    if token:
        shutil.rmtree(_inbox() / token, ignore_errors=True)
        for suffix in (".pdf", ".error"):
            (_outbox() / f"{token}{suffix}").unlink(missing_ok=True)
    outcome = _keep(version, answer) if isinstance(answer, bytes) else answer
    with transaction.atomic():
        version.preview_token = ""
        if isinstance(outcome, str):
            version.preview, version.preview_error = Preview.FAILED, outcome
            log.warning("No preview for version %s: %s", version.pk, outcome)
        else:
            version.preview, version.preview_error = Preview.READY, ""
            version.page_count, version.page_sizes = outcome
        if version.status == DocumentVersion.Status.PREPARING:
            version.status = DocumentVersion.Status.READY
        version.save()


def _keep(version: DocumentVersion, pdf: bytes) -> tuple[int, list[list[float]]] | str:
    """Store the PDF as the version's preview once Snug's own worker has read it."""
    path = preview_path(version)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".partial")
    partial.write_bytes(pdf)
    try:
        facts = sandbox.inspect(partial)
    except sandbox.WorkerError as error:
        partial.unlink(missing_ok=True)
        return error.code
    partial.replace(path)
    shutil.rmtree(storage.renders_dir() / str(int(version.pk)), ignore_errors=True)
    return int(facts["pages"]), facts["sizes"]


def _clear_strays(keep: str) -> None:
    """Remove anything shared with the converter but the job in hand and its status."""
    for folder, wanted_names in (
        (_inbox(), {keep}),
        (_outbox(), {STATUS, f"{keep}.pdf", f"{keep}.error"}),
    ):
        with contextlib.suppress(FileNotFoundError):
            for entry in folder.iterdir():
                if (keep and entry.name in wanted_names) or entry.name == STATUS:
                    continue
                if entry.is_dir() and not entry.is_symlink():
                    shutil.rmtree(entry, ignore_errors=True)
                else:
                    entry.unlink(missing_ok=True)


# The converter rewrites its status every 30 seconds while it waits, and on each fresh start.
QUIET = timedelta(minutes=5)
# Its image is rebuilt every week with LibreOffice's security fixes.
OUT_OF_DATE = timedelta(days=14)
_VERSION = re.compile(r"^(?:\d+:)?(\d+(?:\.\d+)*)")


@dataclass(frozen=True)
class ConverterStatus:
    """What the converter last said about itself. It reads hostile files, so this is untrusted."""

    libreoffice: str  # the Debian package's version, such as 4:25.2.3-2+deb13u9
    built: date | None
    heartbeat: datetime | None

    @property
    def version(self) -> str:
        """LibreOffice's own version: 25.2.3."""
        found = _VERSION.match(self.libreoffice)
        return found.group(1) if found else ""


def _text(value: object) -> str:
    return value if isinstance(value, str) and len(value) <= 64 and value.isprintable() else ""


def converter_status() -> ConverterStatus | None:
    """The converter's status file, read without following links, or None if there is none."""
    raw = _read_answer(STATUS, 4096)
    if not raw:
        return None
    try:
        facts = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(facts, dict):
        return None
    try:
        built = date.fromisoformat(_text(facts.get("built")))
    except ValueError:
        built = None
    try:
        heartbeat = datetime.fromisoformat(_text(facts.get("heartbeat")))
    except ValueError:
        heartbeat = None
    if heartbeat is not None and heartbeat.tzinfo is None:
        heartbeat = None
    return ConverterStatus(_text(facts.get("libreoffice")), built, heartbeat)


@dataclass(frozen=True)
class ConverterState:
    """How the converter is, for Settings and snug doctor."""

    installed: bool
    status: ConverterStatus | None = None
    problem: str = ""
    out_of_date: bool = False


def state(now: datetime) -> ConverterState:
    if not enabled():
        return ConverterState(installed=False)
    status = converter_status()
    if status is None or status.heartbeat is None:
        return ConverterState(True, status, "The converter has not started.")
    quiet = now - status.heartbeat
    if quiet > QUIET:
        minutes = int(quiet.total_seconds() // 60)
        said = f"The converter has not reported for {minutes} minutes."
        return ConverterState(True, status, said)
    old = status.built is None or now.date() - status.built > OUT_OF_DATE
    return ConverterState(True, status, out_of_date=old)


def _release_all() -> None:
    """The converter is gone: nothing waits for it, and every version is shown."""
    DocumentVersion.objects.filter(preview__in=(Preview.PENDING, Preview.CONVERTING)).update(
        preview=Preview.NONE, preview_token=""
    )
    DocumentVersion.objects.filter(status=DocumentVersion.Status.PREPARING).update(
        status=DocumentVersion.Status.READY
    )
    with contextlib.suppress(FileNotFoundError):
        for entry in _inbox().iterdir():
            shutil.rmtree(entry, ignore_errors=True)
