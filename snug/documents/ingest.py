"""Taking in an upload, a PDF, an Office file or an MP4 video: check, store and record it.

The kind is decided by the file's contents, never its name: a PDF starts
with %PDF- (within its first KiB), an Office file is a zip whose main part
says whether it is a workbook, a document or a presentation, and a video has
an ftyp box at byte 4. The confined worker checks each properly:
snug.rendering.worker, ooxml and video.
"""

import hashlib
import math
import os
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import Max

from snug.accounts.models import Admin
from snug.documents import storage
from snug.documents.models import (
    OFFICE,
    PART_SECONDS,
    Document,
    DocumentVersion,
    new_stored_name,
)
from snug.office import previews
from snug.rendering import sandbox

PDF_MAGIC = b"%PDF-"
ZIP_MAGIC = b"PK\x03\x04"
# Old .doc, .ppt and .xls files, and Office files saved with a password, are OLE compound files.
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
# The header may be preceded by a little junk; PDF readers search the first KiB.
HEADER_WINDOW = 1024
# A zip, until the worker has read which Office format it is.
OFFICE_ZIP = "office"


class UploadRejectedError(Exception):
    """The upload was refused. The message is safe to show to the admin."""


def _display_name(name: str) -> str:
    # Kept for admins to recognise the file; never used as a path.
    return PurePosixPath(name.replace("\\", "/")).name.strip()[:255]


def _is_video(head: bytes) -> bool:
    # An MP4 (or a QuickTime movie, which the worker then refuses) opens with an ftyp box.
    return head[4:8] == b"ftyp"


def _kind(head: bytes) -> str:
    if _is_video(head):
        return DocumentVersion.Kind.MP4
    if head.startswith(ZIP_MAGIC):
        return OFFICE_ZIP
    if PDF_MAGIC in head:
        return DocumentVersion.Kind.PDF
    if head.startswith(OLE_MAGIC):
        raise UploadRejectedError(
            "This is an older Office file (.doc, .ppt, or .xls), or one saved with a password. "
            "Save it as .docx, .pptx, or .xlsx without a password, then upload it again."
        )
    raise UploadRejectedError(
        "Snug takes PDFs, Word, PowerPoint, and Excel files (.docx, .pptx, and .xlsx), "
        "and MP4 videos. Export other files to PDF first."
    )


def _limit(head: bytes) -> tuple[int, str]:
    """The size limit for what the first bytes say this is: a video has its own."""
    if _is_video(head):
        return settings.MAX_VIDEO_BYTES, "a video"
    return settings.MAX_UPLOAD_BYTES, "a document"


def _receive(chunks: Iterable[bytes]) -> tuple[Path, int, str, str]:
    """Stream the upload to a temporary file, counting and hashing as it goes.

    Its first bytes say whether it is a video, and so which limit applies,
    before more than a chunk has been written.
    """
    storage.tmp_dir().mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(dir=storage.tmp_dir(), suffix=".upload")
    path = Path(name)
    digest, size, head = hashlib.sha256(), 0, b""
    try:
        with os.fdopen(handle, "wb") as out:
            for chunk in chunks:
                size += len(chunk)
                if len(head) < HEADER_WINDOW:
                    head += chunk[: HEADER_WINDOW - len(head)]
                limit, what = _limit(head)
                if size > limit:
                    raise UploadRejectedError(
                        f"This file is larger than {limit // (1024 * 1024)} MB, "
                        f"the most this instance accepts for {what}."
                    )
                digest.update(chunk)
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        if size == 0:
            raise UploadRejectedError("This file is empty.")
        kind = _kind(head)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path, size, digest.hexdigest(), kind


def _same_kind(document: Document, kind: str) -> None:
    """A document's versions are all one kind: links, analytics and downloads depend on it."""
    latest = document.latest_version()
    if latest is None or latest.kind == kind or (kind == OFFICE_ZIP and latest.is_office):
        return  # an Office zip is checked again once the worker has said which format it is
    is_a = {"pdf": "a PDF", "mp4": "a video"} | {k: f.a_name for k, f in OFFICE.items()}
    short = {"pdf": "PDF", "mp4": "video", OFFICE_ZIP: "Word, PowerPoint, or Excel file"} | {
        k: f.short for k, f in OFFICE.items()
    }
    raise UploadRejectedError(
        f"{document.title} is {is_a[latest.kind]}. Upload a {short[latest.kind]} as its new "
        f"version, or upload the {short[kind]} as a new document."
    )


@dataclass(frozen=True)
class Checked:
    """An upload the worker has read, waiting in the temporary folder to be stored."""

    path: Path
    size: int
    sha256: str
    kind: str
    facts: dict[str, Any]

    def discard(self) -> None:
        self.path.unlink(missing_ok=True)


def check(chunks: Iterable[bytes], *, document: Document | None = None) -> Checked:
    """Receive an upload and have the worker read it.

    Nothing is written to the database here. SQLite runs write transactions in
    IMMEDIATE mode, so one held open while the worker reads the file would keep
    every other writer waiting. A new version must be the document's kind.
    """
    temporary, size, sha256, kind = _receive(chunks)
    try:
        if document is not None:
            _same_kind(document, kind)
        try:
            if kind == DocumentVersion.Kind.MP4:
                facts = sandbox.inspect_mp4(temporary)
            elif kind == OFFICE_ZIP:
                facts = sandbox.inspect_office(temporary)
                kind = facts["format"]
            else:
                facts = sandbox.inspect(temporary)
        except sandbox.WorkerError as error:
            raise UploadRejectedError(str(error)) from error
        if document is not None:
            _same_kind(document, kind)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return Checked(path=temporary, size=size, sha256=sha256, kind=kind, facts=facts)


def store(
    document: Document, checked: Checked, *, filename: str, uploaded_by: Admin | None
) -> DocumentVersion:
    """Add a checked upload to a document as its next version."""
    kind, facts = checked.kind, checked.facts
    office = kind in OFFICE
    video = kind == DocumentVersion.Kind.MP4
    try:
        if video:
            # A video's "pages" are its parts, so the reading analytics count time on each.
            pages = max(1, math.ceil(facts["seconds"] / PART_SECONDS))
            sizes = [[facts["width"], facts["height"]]]
        else:
            pages = 0 if office else int(facts["pages"])
            sizes = [] if office else facts["sizes"]

        queued = previews.wanted(kind)
        with transaction.atomic():
            highest = document.versions.aggregate(n=Max("number"))["n"] or 0
            # A new version waits for its preview before links show it; a first is shared at once.
            waits = queued and document.latest_version() is not None
            version = DocumentVersion(
                document=document,
                number=highest + 1,
                original_filename=_display_name(filename),
                size_bytes=checked.size,
                sha256=checked.sha256,
                stored_name=new_stored_name(kind),
                kind=kind,
                page_count=pages,
                page_sizes=sizes,
                sheets=facts.get("sheets", []) if office else [],
                slides=facts.get("slides", 0) if office else 0,
                seconds=float(facts["seconds"]) if video else 0,
                uploaded_by=uploaded_by,
                preview=DocumentVersion.Preview.PENDING if queued else DocumentVersion.Preview.NONE,
                status=DocumentVersion.Status.PREPARING if waits else DocumentVersion.Status.READY,
            )
            destination = storage.original_path(version)
            destination.parent.mkdir(parents=True, exist_ok=True)
            version.save()
            # Same disk, so this is an atomic rename, not a copy.
            checked.path.replace(destination)
        return version
    finally:
        checked.discard()


def ingest(
    document: Document,
    chunks: Iterable[bytes],
    *,
    filename: str,
    uploaded_by: Admin | None,
) -> DocumentVersion:
    """Add an uploaded PDF, Office file or video to a document as its next version."""
    checked = check(chunks, document=document)
    return store(document, checked, filename=filename, uploaded_by=uploaded_by)
