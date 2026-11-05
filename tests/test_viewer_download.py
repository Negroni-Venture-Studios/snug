"""Downloads: the original PDF, stamped for the viewer in the confined worker.

Decided 5 November 2026: downloads are on by default, and a PDF download is
the original file (so its text can be selected and searched), with a line on
every page and the same details in its document properties. Each download is
audited with a hash of the exact file sent.
"""

import hashlib
import io
from pathlib import Path
from typing import Any

import pytest
from django.test import Client
from pypdf import PdfReader

from snug.audit.models import AuditEvent
from snug.documents import ingest
from snug.documents.models import Document
from snug.links import service
from snug.rendering import sandbox, worker
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
    return sandbox.stamp_pdf(path, email=email, date="5 Nov 2026", ref="7Q2K", limit=10_000_000)


def test_every_page_carries_the_line_and_the_text_stays_text(deck: Path) -> None:
    reader = PdfReader(io.BytesIO(stamp_pdf(deck)))
    assert len(reader.pages) == 2
    for page in reader.pages:
        text = page.extract_text()
        assert f"Shared with {INVESTOR}" in text and "5 Nov 2026" in text and "Link 7Q2K" in text


def test_the_document_properties_name_the_viewer(deck: Path) -> None:
    info = PdfReader(io.BytesIO(stamp_pdf(deck))).metadata
    assert info is not None
    assert INVESTOR in str(info.get("/Subject")) and "7Q2K" in str(info.get("/Subject"))
    assert info.get("/SnugLink") == "7Q2K"


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


def test_odd_addresses_are_written_safely(deck: Path) -> None:
    # Parentheses and backslashes are PDF string syntax; they must not break out.
    odd = "o(d)d\\one@example.com"
    text = PdfReader(io.BytesIO(stamp_pdf(deck, email=odd))).pages[0].extract_text()
    assert f"Shared with {odd}" in text


def test_output_past_the_limit_is_refused(deck: Path) -> None:
    with pytest.raises(sandbox.WorkerError):
        sandbox.stamp_pdf(deck, email=INVESTOR, date="5 Nov 2026", ref="7Q2K", limit=100)


def test_only_pdf_comes_back(deck: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sandbox, "run", lambda *args, **kwargs: b"not a pdf")
    with pytest.raises(sandbox.WorkerError):
        stamp_pdf(deck)


def test_main_stamps_a_pdf_in_process(
    deck: Path, capsysbinary: pytest.CaptureFixture[bytes]
) -> None:
    import os

    args = ["stamp-pdf", str(os.open(deck, os.O_RDONLY)), INVESTOR, "5 Nov 2026", "7Q2K"]
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
    assert download(opened(issued), issued).status_code == 429


def test_a_failed_stamp_says_so(document: Document, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: Any, **kwargs: Any) -> bytes:
        raise sandbox.WorkerError("timeout")

    monkeypatch.setattr("snug.rendering.pages.stamped_pdf", fail)
    issued = issue(document)
    assert download(opened(issued), issued).status_code == 503
    assert not AuditEvent.objects.filter(action="viewer.downloaded").exists()
