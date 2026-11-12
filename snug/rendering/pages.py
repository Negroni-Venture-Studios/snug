"""Rendered pages: made in the worker, then kept on disk.

Plain pages are kept per version, page and width, so one serves every
viewer. Each link's investor gets their own stamped copy, kept for the rest
of the day, since the stamp carries the date. Page 1 is rendered once a
version can be read, and the first pages are stamped while the investor
opens the link. The worker returns each image through a pipe; Snug writes
the files.
"""

import contextlib
import fcntl
import hashlib
import logging
import os
import tempfile
import threading
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from django.conf import settings
from django.utils import timezone

from snug.documents import storage
from snug.documents.models import DocumentVersion
from snug.rendering import sandbox

if TYPE_CHECKING:
    from snug.links.models import Link

log = logging.getLogger(__name__)

DEFAULT_WIDTH = 1600
# Stamped while an investor opens a link: the first pages of a document, or
# page 1 of a room's first documents, one at a time on a one-CPU server.
AHEAD_PAGES = 2
AHEAD_DOCUMENTS = 3
# Geist Mono, vendored with the interface fonts (SIL OFL 1.1).
WATERMARK_FONT = (
    Path(__file__).resolve().parent.parent / "static/snug/fonts/GeistMono-Variable.woff2"
)


def page_image(version: DocumentVersion, page: int, width: int = DEFAULT_WIDTH) -> Path:
    """The rendered page as a WebP file, rendering it first if need be."""
    target = storage.render_path(version, page, width)
    if target.exists():
        return target
    _write(target, sandbox.render(storage.pdf_path(version), page, width))
    return target


def _write(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    # Write beside the target, then rename, so a reader never sees half a file.
    handle, partial = tempfile.mkstemp(dir=target.parent, prefix=".", suffix=".part")
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(data)
        Path(partial).replace(target)
    except BaseException:
        Path(partial).unlink(missing_ok=True)
        raise


def _in_background(task: Callable[[], None]) -> threading.Thread | None:
    """Run the task without making the request wait. It must not use the database."""
    thread = threading.Thread(target=task, daemon=True)
    thread.start()
    return thread


def warm(version: DocumentVersion) -> None:
    """Render page 1 now, so the first investor to open it waits only for the watermark."""
    if version.reads_as_pages and version.page_count >= 1:
        _render_first(version)


def warm_in_background(version: DocumentVersion) -> threading.Thread | None:
    """Warm the version without making the request wait.

    Whether it has pages is decided here, so the thread renders and touches
    no database.
    """
    if not (version.reads_as_pages and version.page_count >= 1):
        return None
    return _in_background(lambda: _render_first(version))


def _render_first(version: DocumentVersion) -> None:
    try:
        page_image(version, 1, DEFAULT_WIDTH)
    except (sandbox.WorkerError, OSError) as error:
        # Left for the first viewer, whose request renders it or shows the error.
        # OSError: a new version replaced an Office file's preview meanwhile.
        log.warning("Could not render the first page of version %s: %s", version.pk, _reason(error))


def _reason(error: Exception) -> str:
    return error.code if isinstance(error, sandbox.WorkerError) else type(error).__name__


def stamp_date(day: date) -> str:
    """The date as the watermark shows it: 12 Nov 2026."""
    return f"{day.day} {day:%b %Y}"


def stamped_path(
    link: "Link",
    version: DocumentVersion,
    page: int,
    width: int,
    *,
    day: date,
    email: str | None = None,
) -> Path:
    """Where the page stamped for this link's investor on this day is kept."""
    text = f"{email or link.email}\n{stamp_date(day)}\n{link.ref}"
    mark = hashlib.sha256(text.encode()).hexdigest()[:16]
    return storage.stamped_path(int(link.pk), version, page, width, day=day, mark=mark)


@contextlib.contextmanager
def _held(target: Path) -> Iterator[None]:
    """Hold the page's lock: a second request waits for a stamp in progress."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def stamped_page(
    version: DocumentVersion, page: int, width: int, *, link: "Link", email: str, day: date
) -> bytes:
    """The page with a viewer's watermark, as WebP bytes, stamped once a day for each link."""
    target = stamped_path(link, version, page, width, day=day, email=email)
    if target.exists():
        return target.read_bytes()
    with _held(target):
        if target.exists():
            return target.read_bytes()
        plain = page_image(version, page, width)
        data = sandbox.stamp(plain, WATERMARK_FONT, email=email, date=stamp_date(day), ref=link.ref)
        _write(target, data)
    return data


_ahead_lock = threading.Lock()
_ahead_running: set[tuple[int, date]] = set()


def _ahead(link: "Link") -> list[tuple[DocumentVersion, int]]:
    """The pages to stamp ahead: what the investor is likely to see first."""
    if link.document is not None:
        version = link.document.latest_version()
        if version is None or not version.reads_as_pages:
            return []
        return [(version, number) for number in range(1, min(AHEAD_PAGES, version.page_count) + 1)]
    if link.room is None:
        return []
    jobs: list[tuple[DocumentVersion, int]] = []
    for entry in link.room.entries.select_related("document").order_by("document__title"):
        version = entry.document.latest_version()
        if version is not None and version.reads_as_pages and version.page_count >= 1:
            jobs.append((version, 1))
            if len(jobs) == AHEAD_DOCUMENTS:
                break
    return jobs


def stamp_ahead(link: "Link") -> None:
    """Stamp the first pages for the link's investor while they open it.

    The pages are chosen here, so the thread stamps and touches no database.
    Nothing is recorded: an email scanner fetching the link costs a few
    stamps, once a day, and counts as no visit.
    """
    jobs = _ahead(link)
    day = timezone.localdate()
    key = (int(link.pk), day)
    if not jobs:
        return
    with _ahead_lock:
        if key in _ahead_running:
            return
        _ahead_running.add(key)
    email = link.email

    def stamp_all() -> None:
        try:
            for version, number in jobs:
                try:
                    stamped_page(version, number, DEFAULT_WIDTH, link=link, email=email, day=day)
                except (sandbox.WorkerError, OSError) as error:
                    # Left for the reader, whose request stamps it or shows the error.
                    code = (
                        error.code
                        if isinstance(error, sandbox.WorkerError)
                        else type(error).__name__
                    )
                    log.warning(
                        "Could not stamp ahead page %s of version %s: %s", number, version.pk, code
                    )
        finally:
            with _ahead_lock:
                _ahead_running.discard(key)

    if _in_background(stamp_all) is None:
        with _ahead_lock:
            _ahead_running.discard(key)


def stamped_office(version: DocumentVersion, *, email: str, date: str, ref: str) -> bytes:
    """The Office file with its properties naming the reader. Stamped on every download."""
    # Every part is deflated again, which can grow a stored part a little; twice is ample.
    limit = 2 * max(settings.MAX_UPLOAD_BYTES, version.size_bytes) + 1024 * 1024
    return sandbox.stamp_office(
        storage.original_path(version), email=email, date=date, ref=ref, limit=limit
    )


def stamped_pdf(version: DocumentVersion, *, email: str, date: str, ref: str) -> bytes:
    """The original PDF with a line naming the viewer on every page. Stamped on every download."""
    # Stamping adds a little to each page; twice the largest upload is ample.
    limit = 2 * max(settings.MAX_UPLOAD_BYTES, version.size_bytes) + 1024 * 1024
    return sandbox.stamp_pdf(
        storage.original_path(version), WATERMARK_FONT, email=email, date=date, ref=ref, limit=limit
    )
