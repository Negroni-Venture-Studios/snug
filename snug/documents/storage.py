"""Where files live. Every path is built from database records, never from a request.

<data>/originals/<generated name>.<ext>   uploads: .pdf, .xlsx, .docx, .pptx and .mp4 (backed up)
<data>/renders/<version id>/<page>-<width>.webp   rendered pages (a cache)
<data>/stamped/<day>/<link id>/<version id>-<page>-<width>-<mark>.webp
                                           pages stamped for a link's investor, kept for the day
<data>/previews/<version id>.pdf           Office files' PDFs, from the converter (a cache)
<data>/tmp/                                uploads in progress
"""

import re
import shutil
from datetime import date
from pathlib import Path

from django.conf import settings

from snug.documents.models import DocumentVersion

STORED_NAME = re.compile(r"^[0-9a-f]{32}\.(pdf|xlsx|docx|pptx|mp4)$")
STAMP_MARK = re.compile(r"^[0-9a-f]{16}$")
RENDER_WIDTHS = (800, 1600)


def _data() -> Path:
    data_dir: Path = settings.CONFIG.data_dir
    return data_dir


def originals_dir() -> Path:
    return _data() / "originals"


def renders_dir() -> Path:
    return _data() / "renders"


def stamped_dir() -> Path:
    return _data() / "stamped"


def tmp_dir() -> Path:
    return _data() / "tmp"


def previews_dir() -> Path:
    return _data() / "previews"


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


def preview_path(version: DocumentVersion) -> Path:
    if version.pk is None:
        raise ValueError("Version is not saved.")
    return _inside(previews_dir(), previews_dir() / f"{int(version.pk)}.pdf")


def pdf_path(version: DocumentVersion) -> Path:
    """The PDF a version's pages are rendered from: the upload, or an Office file's preview."""
    return (
        original_path(version)
        if version.kind == DocumentVersion.Kind.PDF
        else preview_path(version)
    )


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


def stamped_path(
    link_id: int, version: DocumentVersion, page: int, width: int, *, day: date, mark: str
) -> Path:
    """A page stamped for one link's investor on one day.

    `mark` is a hash of the stamp's text, so a page is only ever served with
    the text it was stamped with.
    """
    render_path(version, page, width)  # the same checks on page and width
    if not STAMP_MARK.match(mark):
        raise ValueError("Stamp mark is not a hash.")
    folder = stamped_dir() / day.isoformat() / str(int(link_id))
    return _inside(stamped_dir(), folder / f"{int(version.pk)}-{page}-{width}-{mark}.webp")


def clear_stamped(*, link_id: int | None = None, keep: date | None = None) -> int:
    """Delete stamped pages: one link's, or every day's but `keep`. Returns folders removed."""
    base = stamped_dir()
    if not base.exists():
        return 0
    removed = 0
    for day in base.iterdir():
        if link_id is not None:
            target = day / str(int(link_id))
        elif keep is None or day.name != keep.isoformat():
            target = day
        else:
            continue
        if target.exists():
            shutil.rmtree(_inside(base, target))
            removed += 1
    return removed
