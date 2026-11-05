"""Rendered pages: made on demand in the worker, then cached on disk.

The cache holds plain pages per version, page and width. Watermarks are
stamped on each request (step 4), so one cached page serves every viewer.
"""

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
    target.parent.mkdir(parents=True, exist_ok=True)
    sandbox.render(storage.original_path(version), page, width, target)
    return target
