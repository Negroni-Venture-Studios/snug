"""Rendered pages: made on demand in the worker, then cached on disk.

The cache holds plain pages per version, page and width. Watermarks are
stamped on each request (step 4), so one cached page serves every viewer.
The worker returns the image through a pipe; Snug writes the cache file.
"""

import os
import tempfile
from pathlib import Path

from django.conf import settings

from snug.documents import storage
from snug.documents.models import DocumentVersion
from snug.rendering import sandbox

DEFAULT_WIDTH = 1600
# Geist Mono, vendored with the interface fonts (SIL OFL 1.1).
WATERMARK_FONT = (
    Path(__file__).resolve().parent.parent / "static/snug/fonts/GeistMono-Variable.woff2"
)


def page_image(version: DocumentVersion, page: int, width: int = DEFAULT_WIDTH) -> Path:
    """The rendered page as a WebP file, rendering it first if need be."""
    target = storage.render_path(version, page, width)
    if target.exists():
        return target
    image = sandbox.render(storage.original_path(version), page, width)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Write beside the target, then rename, so a reader never sees half a file.
    handle, partial = tempfile.mkstemp(dir=target.parent, prefix=".", suffix=".part")
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(image)
        Path(partial).replace(target)
    except BaseException:
        Path(partial).unlink(missing_ok=True)
        raise
    return target


def stamped_page(
    version: DocumentVersion, page: int, width: int, *, email: str, date: str, ref: str
) -> bytes:
    """The page with a viewer's watermark, as WebP bytes. Stamped on every request."""
    plain = page_image(version, page, width)
    return sandbox.stamp(plain, WATERMARK_FONT, email=email, date=date, ref=ref)


def stamped_pdf(version: DocumentVersion, *, email: str, date: str, ref: str) -> bytes:
    """The original PDF with a line naming the viewer on every page. Stamped on every download."""
    # Stamping adds a little to each page; twice the largest upload is ample.
    limit = 2 * max(settings.MAX_UPLOAD_BYTES, version.size_bytes) + 1024 * 1024
    return sandbox.stamp_pdf(
        storage.original_path(version), email=email, date=date, ref=ref, limit=limit
    )
