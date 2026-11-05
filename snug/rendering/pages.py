"""Rendered pages: made on demand in the worker, then cached on disk.

The cache holds plain pages per version, page and width. Watermarks are
stamped on each request (step 4), so one cached page serves every viewer.
The worker returns the image through a pipe; Snug writes the cache file.
"""

import os
import tempfile
from pathlib import Path

from snug.documents import storage
from snug.documents.models import DocumentVersion
from snug.rendering import sandbox

DEFAULT_WIDTH = 1600


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
