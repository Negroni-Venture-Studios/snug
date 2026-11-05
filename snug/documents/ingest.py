"""Taking in an uploaded PDF: check it, store it, and record it as a new version."""

import hashlib
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

from django.conf import settings
from django.db import transaction
from django.db.models import Max

from snug.accounts.models import Admin
from snug.documents import storage
from snug.documents.models import Document, DocumentVersion
from snug.rendering import sandbox

PDF_MAGIC = b"%PDF-"
# The header may be preceded by a little junk; PDF readers search the first KiB.
HEADER_WINDOW = 1024


class UploadRejectedError(Exception):
    """The upload was refused. The message is safe to show to the admin."""


def _display_name(name: str) -> str:
    # Kept for admins to recognise the file; never used as a path.
    return PurePosixPath(name.replace("\\", "/")).name.strip()[:255]


def _receive(chunks: Iterable[bytes], limit: int) -> tuple[Path, int, str]:
    """Stream the upload to a temporary file, counting and hashing as it goes."""
    storage.tmp_dir().mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(dir=storage.tmp_dir(), suffix=".upload")
    path = Path(name)
    digest, size, head = hashlib.sha256(), 0, b""
    try:
        with os.fdopen(handle, "wb") as out:
            for chunk in chunks:
                size += len(chunk)
                if size > limit:
                    raise UploadRejectedError(
                        f"This file is larger than {limit // (1024 * 1024)} MB, "
                        "the most this instance accepts."
                    )
                if len(head) < HEADER_WINDOW:
                    head += chunk[: HEADER_WINDOW - len(head)]
                digest.update(chunk)
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        if size == 0:
            raise UploadRejectedError("This file is empty.")
        if PDF_MAGIC not in head:
            raise UploadRejectedError(
                "This file is not a PDF. Snug shows PDFs only: export the file to PDF first."
            )
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path, size, digest.hexdigest()


def ingest(
    document: Document,
    chunks: Iterable[bytes],
    *,
    filename: str,
    uploaded_by: Admin | None,
) -> DocumentVersion:
    """Add an uploaded PDF to a document as its next version."""
    limit: int = settings.MAX_UPLOAD_BYTES
    temporary, size, sha256 = _receive(chunks, limit)
    try:
        try:
            facts = sandbox.inspect(temporary)
        except sandbox.WorkerError as error:
            raise UploadRejectedError(str(error)) from error

        with transaction.atomic():
            highest = document.versions.aggregate(n=Max("number"))["n"] or 0
            version = DocumentVersion(
                document=document,
                number=highest + 1,
                original_filename=_display_name(filename),
                size_bytes=size,
                sha256=sha256,
                page_count=int(facts["pages"]),
                page_sizes=facts["sizes"],
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
