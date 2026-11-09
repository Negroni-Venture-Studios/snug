"""Where files live. Every path is built from database records, never from a request.

<data>/originals/<generated name>.<ext>   uploaded .pdf, .xlsx and .mp4 files (backed up)
<data>/renders/<version id>/<page>-<width>.webp   rendered pages (a cache)
<data>/tmp/                                uploads in progress
"""

import re
import shutil
from pathlib import Path

from django.conf import settings

from snug.documents.models import DocumentVersion

STORED_NAME = re.compile(r"^[0-9a-f]{32}\.(pdf|xlsx|mp4)$")
RENDER_WIDTHS = (800, 1600)


def _data() -> Path:
    data_dir: Path = settings.CONFIG.data_dir
    return data_dir


def originals_dir() -> Path:
    return _data() / "originals"


def renders_dir() -> Path:
    return _data() / "renders"


def tmp_dir() -> Path:
    return _data() / "tmp"


def _inside(base: Path, path: Path) -> Path:
    # Defence in depth: names are generated and checked, but refuse anything
    # that would resolve outside its folder all the same.
    if not path.resolve().is_relative_to(base.resolve()):
        raise ValueError("Path escapes its storage folder.")
    return path


def original_path(version: DocumentVersion) -> Path:
    if not STORED_NAME.match(version.stored_name):
        raise ValueError("Stored name is not a generated name.")
    return _inside(originals_dir(), originals_dir() / version.stored_name)


def render_path(version: DocumentVersion, page: int, width: int) -> Path:
    if version.pk is None:
        raise ValueError("Version is not saved.")
    if not 1 <= page <= version.page_count:
        raise ValueError("Page number out of range.")
    if width not in RENDER_WIDTHS:
        raise ValueError("Unsupported render width.")
    folder = renders_dir() / str(int(version.pk))
    return _inside(renders_dir(), folder / f"{page}-{width}.webp")


def clear_render_cache(version: DocumentVersion | None = None) -> None:
    """Delete rendered pages. They are rebuilt when next needed."""
    target = renders_dir() if version is None else renders_dir() / str(int(version.pk))
    if target.exists():
        shutil.rmtree(_inside(renders_dir(), target))
