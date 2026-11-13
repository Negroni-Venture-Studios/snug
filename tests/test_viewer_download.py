"""Downloads: the original PDF, stamped for the viewer in the confined worker.

Decided 5 November 2026: downloads are on by default, and a PDF download is
the original file (so its text can be selected and searched), with a line on
every page and the same details in its document properties. Each download is
audited with a hash of the exact file sent.
"""

import hashlib
import io
import math
from pathlib import Path
from typing import Any

import pytest
from django.test import Client
from pypdf import PdfReader

from snug.audit.models import AuditEvent
from snug.documents import ingest
from snug.documents.models import Document
from snug.links import service
from snug.rendering import pages, sandbox, worker
from snug.viewer import limits
from tests.pdfs import chunks, make_pdf

pytestmark = pytest.mark.django_db

INVESTOR = "priya@northfield.example"


@pytest.fixture
def document(data_folder: Any) -> Document:
    deck = Document.objects.create(title="Seed deck: Q3 (final)")
    pdf = make_pdf((960, 540), (595, 842))
    ingest.ingest(deck, chunks(pdf), filename="deck.pdf", uploaded_by=None)
    return deck


def issue(document: Document, **options: Any) -> service.Issued:
    fields: dict[str, Any] = {
        "email": INVESTOR,
        "note": "",
        "expires_at": None,
        "require_verification": False,
        "allow_download": True,
        "created_by": None,
    }
    fields.update(options)
    return service.create(document=document, **fields)


def opened(issued: service.Issued) -> Client:
    browser = Client()
    browser.post(f"/v/{issued.token}/open", secure=True)
    return browser


def download(browser: Client, issued: service.Issued) -> Any:
    return browser.get(f"/v/{issued.token}/download", secure=True)


# ---------------------------------------------------------------- the worker


@pytest.fixture
def deck(tmp_path: Path) -> Path:
    path = tmp_path / "deck.pdf"
    path.write_bytes(make_pdf((960, 540), (595, 842)))
    return path


def stamp_pdf(path: Path, email: str = INVESTOR) -> bytes:
    return sandbox.stamp_pdf(
        path, pages.WATERMARK_FONT, email=email, date="5 Nov 2026", ref="7Q2K", limit=10_000_000
    )


def test_every_page_carries_the_line_and_the_text_stays_text(deck: Path) -> None:
    reader = PdfReader(io.BytesIO(stamp_pdf(deck)))
    assert len(reader.pages) == 2
    for page in reader.pages:
        text = page.extract_text()
        assert f"Shared with {INVESTOR}" in text and "5 Nov 2026" in text and "Link 7Q2K" in text


def test_every_page_carries_a_faint_diagonal_mark(deck: Path) -> None:
    # Decided 5 November 2026: the same tiled mark as the reader, so a page of
    # the download is marked wherever it is cropped or screenshotted.
    import pypdfium2
    from PIL import ImageChops

    def render(data: bytes) -> Any:
        return pypdfium2.PdfDocument(data)[0].render(scale=1).to_pil().convert("L")

    plain, stamped = render(deck.read_bytes()), render(stamp_pdf(deck))
    width, height = plain.size
    # The middle of the page, well away from the footer line, now differs.
    box = (width // 4, height // 4, 3 * width // 4, 3 * height // 4)
    changed = ImageChops.difference(plain.crop(box), stamped.crop(box)).point(
        lambda v: 255 if v > 10 else 0
    )
    assert changed.histogram()[255] > 50


def test_the_mark_is_an_image_so_copied_text_stays_clean(deck: Path) -> None:
    reader = PdfReader(io.BytesIO(stamp_pdf(deck)))
    for page in reader.pages:
        # Only the footer line names the viewer in the text; the mark does not.
        assert page.extract_text().count(INVESTOR) == 1
        resources: Any = page["/Resources"]
        images = [
            obj.get_object()
            for obj in resources["/XObject"].values()
            if obj.get_object().get("/Subtype") == "/Image"
        ]
        assert any("/SMask" in image for image in images)


def test_the_mark_adds_little_to_the_file(tmp_path: Path) -> None:
    long_deck = tmp_path / "long.pdf"
    long_deck.write_bytes(make_pdf(*[(960, 540)] * 20))
    before = len(long_deck.read_bytes())
    assert len(stamp_pdf(long_deck)) - before < 400_000


def test_the_document_properties_name_the_viewer(deck: Path) -> None:
    info = PdfReader(io.BytesIO(stamp_pdf(deck))).metadata
    assert info is not None
    assert INVESTOR in str(info.get("/Subject")) and "7Q2K" in str(info.get("/Subject"))
    assert info.get("/SnugLink") == "7Q2K"


# Width and height in points: a sliver a billion points long, and an ordinary page.
SLIVER, A4 = (1e9, 0.001), (595.0, 842.0)


@pytest.mark.parametrize("size", [SLIVER, SLIVER[::-1], (1e6, 1e6), A4])
def test_the_mark_is_drawn_on_a_bounded_square(
    size: tuple[float, float], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Security audit, 10 November 2026: the mark is drawn on a square as wide
    # as the page's diagonal, and only the page's area was capped, so a page
    # a billion points long asked for a square of 1.5 billion pixels a side.
    from PIL import Image

    drawn: list[tuple[int, int]] = []

    def mark_layer(width: int, height: int, *args: Any) -> Image.Image:
        side = math.isqrt(width * width + height * height) + 1
        assert side * side * 4 < 64 * 1024 * 1024, (width, height)  # RGBA, under 64 MiB
        drawn.append((width, height))
        return Image.new("RGBA", (width, height))

    monkeypatch.setattr(worker, "_mark_layer", mark_layer)
    worker._overlay_pdf(*size, None, INVESTOR, "5 Nov 2026")
    if size == A4:  # an ordinary page keeps the full density
        assert drawn == [(round(595 * worker.OVERLAY_DENSITY), round(842 * worker.OVERLAY_DENSITY))]


def test_a_page_of_extreme_shape_is_still_stamped(tmp_path: Path) -> None:
    from pypdf import PdfWriter
    from pypdf.generic import RectangleObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=72, height=72)
    page.mediabox = page.cropbox = RectangleObject([0, 0, *SLIVER])
    sliver = tmp_path / "sliver.pdf"
    with sliver.open("wb") as out:
        writer.write(out)
    reader = PdfReader(io.BytesIO(stamp_pdf(sliver)))
    assert f"Shared with {INVESTOR}" in reader.pages[0].extract_text()


# A PDF with no cross-reference table: PDFium shows it, pypdf alone cannot read it.
NO_XREF = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 100]>>endobj\n"
    b"trailer<</Root 1 0 R>>\n%%EOF\n"
)


def test_a_pdf_that_only_pdfium_can_read_is_still_stamped(tmp_path: Path) -> None:
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(NO_XREF)
    assert sandbox.inspect(broken)["pages"] == 1
    text = PdfReader(io.BytesIO(stamp_pdf(broken))).pages[0].extract_text()
    assert f"Shared with {INVESTOR}" in text


ENCRYPTED = Path(__file__).parent / "fixtures" / "aes256-owner-password.pdf"


def test_an_encrypted_pdf_that_opens_without_a_password_is_stamped() -> None:
    # Found 6 November 2026: a PDF with AES-256 and only an owner password
    # (it opens freely, but restricts copying) could not be downloaded. pypdf
    # needs the `cryptography` library for AES; Snug stamps PDFium's
    # unencrypted copy instead. The fixture's title is stored as UTF-16, whose
    # codec pypdf loads lazily, which confinement then refused.
    reader = PdfReader(io.BytesIO(stamp_pdf(ENCRYPTED)))
    assert not reader.is_encrypted
    assert len(reader.pages) == 2
    assert f"Shared with {INVESTOR}" in reader.pages[0].extract_text()


def test_odd_addresses_are_written_safely(deck: Path) -> None:
    # Parentheses and backslashes are PDF string syntax; they must not break out.
    odd = "o(d)d\\one@example.com"
    text = PdfReader(io.BytesIO(stamp_pdf(deck, email=odd))).pages[0].extract_text()
    assert f"Shared with {odd}" in text


def test_output_past_the_limit_is_refused(deck: Path) -> None:
    with pytest.raises(sandbox.WorkerError):
        sandbox.stamp_pdf(
            deck, pages.WATERMARK_FONT, email=INVESTOR, date="5 Nov 2026", ref="7Q2K", limit=100
        )


def test_only_pdf_comes_back(deck: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sandbox, "run", lambda *args, **kwargs: b"not a pdf")
    with pytest.raises(sandbox.WorkerError):
        stamp_pdf(deck)


def test_main_stamps_a_pdf_in_process(
    deck: Path, capsysbinary: pytest.CaptureFixture[bytes]
) -> None:
    import os

    font = str(os.open(pages.WATERMARK_FONT, os.O_RDONLY))
    args = ["stamp-pdf", str(os.open(deck, os.O_RDONLY)), font, INVESTOR, "5 Nov 2026", "7Q2K"]
    assert worker.main(args, confined=False) == 0
    assert capsysbinary.readouterr().out.startswith(b"%PDF-")


# ---------------------------------------------------------------- the download


def test_downloading_sends_the_stamped_original(document: Document) -> None:
    issued = issue(document)
    response = download(opened(issued), issued)
    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response["Content-Disposition"] == 'attachment; filename="Seed deck Q3 final.pdf"'
    assert "no-store" in response["Cache-Control"]
    body = b"".join(response.streaming_content) if response.streaming else response.content
    assert f"Shared with {INVESTOR}" in PdfReader(io.BytesIO(body)).pages[0].extract_text()

    event = AuditEvent.objects.get(action="viewer.downloaded")
    assert event.actor == INVESTOR
    assert event.details["sha256"] == hashlib.sha256(body).hexdigest()
    assert event.details["version"] == 1


def test_the_reader_offers_the_download(document: Document) -> None:
    issued = issue(document)
    page = opened(issued).get(f"/v/{issued.token}", secure=True).content.decode()
    assert f'href="/v/{issued.token}/download"' in page


def test_no_download_when_turned_off(document: Document) -> None:
    issued = issue(document, allow_download=False)
    browser = opened(issued)
    assert download(browser, issued).status_code == 404
    page = browser.get(f"/v/{issued.token}", secure=True).content.decode()
    assert "/download" not in page
    assert not AuditEvent.objects.filter(action="viewer.downloaded").exists()


def test_downloads_need_a_session(document: Document) -> None:
    issued = issue(document, require_verification=True)
    response = download(Client(), issued)
    assert response.status_code == 303 and response["Content-Type"] != "application/pdf"


def test_downloads_are_rate_limited_per_link(document: Document) -> None:
    from snug.core import ratelimit

    issued = issue(document)
    for _ in range(limits.DOWNLOAD_PER_LINK.limit):
        ratelimit.hit(limits.DOWNLOAD_PER_LINK, str(issued.link.pk))
    response = download(opened(issued), issued)
    assert response.status_code == 429
    # The window is an hour, not the 15 minutes of the gate's limits.
    assert response.content.decode() == "Too many downloads on this link. Try again in an hour."


def test_a_failed_stamp_says_so(document: Document, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: Any, **kwargs: Any) -> bytes:
        raise sandbox.WorkerError("timeout")

    monkeypatch.setattr("snug.rendering.pages.stamped_pdf", fail)
    issued = issue(document)
    assert download(opened(issued), issued).status_code == 503
    assert not AuditEvent.objects.filter(action="viewer.downloaded").exists()
