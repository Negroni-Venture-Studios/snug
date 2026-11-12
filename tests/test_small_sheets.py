"""A small sheet is shown at its own size, on a page as wide as A4.

LibreOffice gives each sheet of a workbook a page the size of the cells it
uses, and the reader drew every page as wide as the frame. On the rc.3
server a one-cell sheet filled the screen with a single number (12 November
2026). A sheet narrower than A4 is now drawn at A4 scale, at the top left of
a white page as wide as A4. Only workbooks: a narrow PDF page, such as A5,
is the author's choice and still fills the frame.
"""

import io
from pathlib import Path

import pytest
from django.test import Client
from PIL import Image

from snug.documents import storage
from snug.documents.models import SHEET_WIDTH, Document, DocumentVersion
from snug.office import previews
from snug.rendering import pages, sandbox, worker
from tests.fake_converter import Converter
from tests.office_files import make_xlsx
from tests.pdfs import make_pdf
from tests.test_office_previews import link_to, opened, upload

pytestmark = pytest.mark.django_db

CELL = (60, 20)  # one cell, in points
WIDE = (1200, 400)  # a wide model


def size_of(webp: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(webp)) as image:
        return image.size


def ink_right_of(webp: bytes, x: int) -> bool:
    with Image.open(io.BytesIO(webp)) as image:
        right = image.convert("L").crop((x, 0, image.width, image.height))
        darkest = right.getextrema()[0]  # a greyscale image: (lowest, highest)
        assert isinstance(darkest, int | float)
        return darkest < 250


def test_the_worker_draws_a_narrow_page_at_the_width_asked_for(tmp_path: Path) -> None:
    pdf = tmp_path / "sheet.pdf"
    pdf.write_bytes(make_pdf(CELL))
    padded = sandbox.render(pdf, 1, 800, at_least=SHEET_WIDTH)
    assert size_of(padded) == (800, round(CELL[1] * 800 / SHEET_WIDTH))
    # The cell is drawn at A4 scale, at the left; the rest is white.
    assert not ink_right_of(padded, round(CELL[0] * 800 / SHEET_WIDTH) + 4)


def test_without_a_minimum_a_narrow_page_fills_the_width(tmp_path: Path) -> None:
    pdf = tmp_path / "a5.pdf"
    pdf.write_bytes(make_pdf((420, 595)))
    width, height = size_of(sandbox.render(pdf, 1, 800))
    assert width == 800 and abs(height - 595 * 800 / 420) <= 1  # PDFium rounds up


def test_a_page_wider_than_the_minimum_is_drawn_as_before(tmp_path: Path) -> None:
    pdf = tmp_path / "wide.pdf"
    pdf.write_bytes(make_pdf(WIDE))
    assert size_of(sandbox.render(pdf, 1, 800, at_least=SHEET_WIDTH)) == (800, 267)


@pytest.mark.parametrize("asked", ["-1", "100000", "a4"])
def test_odd_minimums_are_refused(tmp_path: Path, asked: str) -> None:
    pdf = tmp_path / "sheet.pdf"
    pdf.write_bytes(make_pdf(CELL))
    with pytest.raises(sandbox.WorkerError):
        sandbox.run(pdf, "render", "1", "800", asked, timeout=30)


def previewed_workbook(converter: Converter) -> DocumentVersion:
    book = Document.objects.create(title="Cap table")
    version = upload(book, make_xlsx(), name="Cap table.xlsx")
    previews.pump()
    converter.answer(make_pdf(CELL, WIDE))
    previews.pump()
    version.refresh_from_db()
    assert version.reads_as_pages
    return version


def test_a_workbooks_sizes_as_shown(converter: Converter) -> None:
    version = previewed_workbook(converter)
    assert version.shown_size(1) == (SHEET_WIDTH, CELL[1])
    assert version.shown_size(2) == WIDE


def test_a_pdfs_sizes_are_its_own(data_folder: Path) -> None:
    from snug.documents.ingest import ingest
    from tests.pdfs import chunks

    deck = Document.objects.create(title="A5 booklet")
    version = ingest(deck, chunks(make_pdf((420, 595))), filename="b.pdf", uploaded_by=None)
    assert version.shown_size(1) == (420, 595)


def test_a_small_sheet_is_rendered_on_an_a4_wide_page(converter: Converter) -> None:
    version = previewed_workbook(converter)
    rendered = pages.page_image(version, 1, 1600).read_bytes()
    assert size_of(rendered) == (1600, round(CELL[1] * 1600 / SHEET_WIDTH))
    assert storage.render_path(version, 1, 1600).exists()


def test_the_reader_reserves_the_padded_size(converter: Converter) -> None:
    version = previewed_workbook(converter)
    issued = link_to(version.document)
    page = opened(issued).get(f"/v/{issued.token}", secure=True).content.decode()
    footer = worker_footer(1600)
    assert f'height="{round(1600 * CELL[1] / SHEET_WIDTH) + footer}"' in page


def worker_footer(width: int) -> int:
    return max(28, round(width * 0.03))


def test_the_admin_preview_reserves_the_padded_size(
    converter: Converter, admin_browser: Client
) -> None:
    version = previewed_workbook(converter)
    page = admin_browser.get(f"/admin/documents/{version.document.token}", secure=True)
    assert f'width="800" height="{round(800 * CELL[1] / SHEET_WIDTH)}"' in page.content.decode()


def test_the_worker_mode_is_still_only_render(tmp_path: Path) -> None:
    # The minimum rides on the render mode: no new mode, so its preload is unchanged.
    assert worker.PRELOADS["render"] == ("pil", "pdfium")
