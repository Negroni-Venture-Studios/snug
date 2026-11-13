"""The reader and the video page: About this link opens from the top right, and the controls lead.

Decided 13 November 2026. Investors mostly download, and use the reader for
quick reference, so the page keeps the whole width and the About this link
panel drops down from a button at the top right, beside Download: a
disclosure, which works without JavaScript and which a screen reader
announces as one. Previous, Next and the page picker sit above the page, so
flipping to page 17 needs no scrolling. Pages without a big image keep the
panel beside them.
"""

from pathlib import Path

import pytest
from django.test import Client

from snug.documents.ingest import ingest
from snug.documents.models import Document
from snug.links import service
from tests import test_video_documents as videos
from tests import test_viewer_reader as reader
from tests.pdfs import chunks, make_pdf

pytestmark = pytest.mark.django_db


@pytest.fixture
def document(data_folder: Path) -> Document:
    deck = Document.objects.create(title="Seed deck")
    ingest(deck, chunks(make_pdf((960, 540), (960, 540))), filename="deck.pdf", uploaded_by=None)
    return deck


@pytest.fixture
def issued(document: Document) -> service.Issued:
    return reader.issue(document)


@pytest.fixture
def demo(data_folder: Path) -> Document:
    video = Document.objects.create(title="Product demo")
    ingest(video, chunks(videos.CLIP_BYTES), filename="demo.mp4", uploaded_by=None)
    return video


TOGGLE = '<details class="about-toggle" data-about>'


def toggle_holds_the_panel(page: str) -> bool:
    start = page.index(TOGGLE)
    end = page.index("</details>", start)
    return 'aria-label="About this link"' in page[start:end]


def test_the_reader_has_the_toggle_at_the_top(issued: service.Issued) -> None:
    page = reader.get(reader.opened(issued), f"/v/{issued.token}").content.decode()
    assert TOGGLE in page and toggle_holds_the_panel(page)
    # In the header, beside Download, not below the page.
    assert page.index(TOGGLE) < page.index('<main class="v-main">')
    summary = page[page.index("<summary") : page.index("</summary>")]
    assert "About this link" in summary
    assert 'class="icon caret"' in summary  # it shows it opens


def test_the_reader_puts_its_controls_above_the_page(issued: service.Issued) -> None:
    page = reader.get(reader.opened(issued), f"/v/{issued.token}").content.decode()
    assert page.index('class="reader-controls"') < page.index('<div class="page-frame">')


def test_the_message_is_in_the_toggle(document: Document) -> None:
    sent = reader.issue(document, message="Ahead of Thursday's call.")
    page = reader.get(reader.opened(sent), f"/v/{sent.token}").content.decode()
    start = page.index(TOGGLE)
    assert "Ahead of Thursday&#x27;s call." in page[start : page.index("</details>", start)]


def test_the_video_page_has_the_toggle_too(demo: Document) -> None:
    sent = videos.issue(demo, message="The demo from the board meeting.")
    page = videos.opened(sent).get(f"/v/{sent.token}", secure=True).content.decode()
    assert TOGGLE in page and toggle_holds_the_panel(page)
    assert page.index(TOGGLE) < page.index('<main class="v-main">')


def test_the_landing_page_keeps_the_panel_beside_it(document: Document) -> None:
    sent = reader.issue(document)
    page = reader.get(Client(), f"/v/{sent.token}").content.decode()
    assert 'aria-label="About this link"' in page
    assert TOGGLE not in page
