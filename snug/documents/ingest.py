"""Taking in an upload, a PDF, an Excel workbook or an MP4 video: check, store and record it.

The kind is decided by the file's first bytes, never its name: a PDF starts
with %PDF- (within its first KiB), a workbook is a zip, and a video has an
ftyp box at byte 4. The confined worker then checks it properly:
snug.rendering.worker, workbook and video.
"""

import hashlib
import math
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

from django.conf import settings
from django.db import transaction
from django.db.models import Max

from snug.accounts.models import Admin
from snug.documents import storage
from snug.documents.models import PART_SECONDS, Document, DocumentVersion, new_stored_name
from snug.rendering import sandbox

PDF_MAGIC = b"%PDF-"
ZIP_MAGIC = b"PK\x03\x04"
# Old .xls files, and workbooks saved with a password, are OLE compound files.
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
# The header may be preceded by a little junk; PDF readers search the first KiB.
HEADER_WINDOW = 1024


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
        return DocumentVersion.Kind.XLSX
    if PDF_MAGIC in head:
        return DocumentVersion.Kind.PDF
    if head.startswith(OLE_MAGIC):
        raise UploadRejectedError(
            "This is an older Excel file (.xls), or a workbook saved with a password. "
            "Save it as an Excel Workbook (.xlsx) without a password, then upload it again."
        )
    raise UploadRejectedError(
        "Snug takes PDFs, Excel workbooks (.xlsx) and MP4 videos. Export other files to PDF first."
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
    if latest is None or latest.kind == kind:
        return
    is_a = {"pdf": "a PDF", "xlsx": "an Excel workbook", "mp4": "a video"}
    short = {"pdf": "PDF", "xlsx": "workbook", "mp4": "video"}
    raise UploadRejectedError(
        f"{document.title} is {is_a[latest.kind]}. Upload a {short[latest.kind]} as its new "
        f"version, or upload the {short[kind]} as a new document."
    )


def ingest(
    document: Document,
    chunks: Iterable[bytes],
    *,
    filename: str,
    uploaded_by: Admin | None,
) -> DocumentVersion:
    """Add an uploaded PDF, workbook or video to a document as its next version."""
    temporary, size, sha256, kind = _receive(chunks)
    try:
        _same_kind(document, kind)
        workbook = kind == DocumentVersion.Kind.XLSX
        video = kind == DocumentVersion.Kind.MP4
        try:
            if video:
                facts = sandbox.inspect_mp4(temporary)
            elif workbook:
                facts = sandbox.inspect_xlsx(temporary)
            else:
                facts = sandbox.inspect(temporary)
        except sandbox.WorkerError as error:
            raise UploadRejectedError(str(error)) from error
        if video:
            # A video's "pages" are its parts, so the reading analytics count time on each.
            pages = max(1, math.ceil(facts["seconds"] / PART_SECONDS))
            sizes = [[facts["width"], facts["height"]]]
        else:
            pages = 0 if workbook else int(facts["pages"])
            sizes = [] if workbook else facts["sizes"]

        with transaction.atomic():
            highest = document.versions.aggregate(n=Max("number"))["n"] or 0
            version = DocumentVersion(
                document=document,
                number=highest + 1,
                original_filename=_display_name(filename),
                size_bytes=size,
                sha256=sha256,
                stored_name=new_stored_name(kind),
                kind=kind,
                page_count=pages,
                page_sizes=sizes,
                sheets=facts["sheets"] if workbook else [],
                seconds=float(facts["seconds"]) if video else 0,
                uploaded_by=uploaded_by,
            )
            destination = storage.original_path(version)
            destination.parent.mkdir(parents=True, exist_ok=True)
            version.save()
            # Same disk, so this is an atomic rename, not a copy.
            temporary.replace(destination)
        return version
    finally:
        temporary.unlink(missing_ok=True)
