"""The reader: one watermarked page at a time, every request through authorise()."""

import io
from typing import Any

import pytest
from django.test import Client
from django.utils import timezone
from PIL import Image

from snug.documents import ingest
from snug.documents.models import Document
from snug.links import service
from snug.viewer import sessions
from tests.pdfs import chunks, make_pdf

pytestmark = pytest.mark.django_db

INVESTOR = "priya@northfield.example"


@pytest.fixture
def document(data_folder: Any) -> Document:
    deck = Document.objects.create(title="Seed deck")
    pdf = make_pdf((960, 540), (960, 540), (595, 842))
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


@pytest.fixture
def issued(document: Document) -> service.Issued:
    return issue(document)


def opened(issued: service.Issued) -> Client:
    browser = Client()
    response = browser.post(f"/v/{issued.token}/open", secure=True)
    assert sessions.COOKIE in response.cookies
    return browser


def get(browser: Client, url: str) -> Any:
    return browser.get(url, secure=True)


def image_url(issued: service.Issued, page: int = 1, width: int = 800) -> str:
    return f"/v/{issued.token}/pages/{page}-{width}.webp"


# ---------------------------------------------------------------- the reader page


def test_the_reader_shows_the_first_page(issued: service.Issued) -> None:
    page = get(opened(issued), f"/v/{issued.token}").content.decode()
    assert 'alt="Page 1 of 3"' in page
    assert f'src="{image_url(issued, 1, 1600)}"' in page
    assert f"{image_url(issued, 1, 800)} 800w" in page
    assert "1 of 3" in page
    assert f'href="/v/{issued.token}?page=2"' in page and "Previous" in page
    assert 'name="page"' in page  # the page picker


def test_the_reader_moves_through_pages(issued: service.Issued) -> None:
    browser = opened(issued)
    page = get(browser, f"/v/{issued.token}?page=3").content.decode()
    assert 'alt="Page 3 of 3"' in page
    assert f'href="/v/{issued.token}?page=2"' in page
    assert "?page=4" not in page


@pytest.mark.parametrize("asked", ["0", "4", "-1", "two", "", "1e3"])
def test_odd_page_numbers_show_the_first_page(issued: service.Issued, asked: str) -> None:
    page = get(opened(issued), f"/v/{issued.token}?page={asked}").content.decode()
    assert 'alt="Page 1 of 3"' in page


def test_the_reader_prefetches_the_next_page(issued: service.Issued) -> None:
    page = get(opened(issued), f"/v/{issued.token}").content.decode()
    assert f'<link rel="prefetch" href="{image_url(issued, 2, 1600)}"' in page


def test_the_reader_explains_images_and_screen_readers(issued: service.Issued) -> None:
    page = get(opened(issued), f"/v/{issued.token}").content.decode()
    assert "screen reader" in page


def test_a_document_still_being_prepared(document: Document) -> None:
    empty = Document.objects.create(title="Board pack")
    issued = issue(empty)
    page = get(opened(issued), f"/v/{issued.token}").content.decode()
    assert "not ready yet" in page and "<img" not in page


def test_without_a_session_there_is_no_reader(issued: service.Issued) -> None:
    gated = issue(issued.link.document, require_verification=True)
    page = get(Client(), f"/v/{gated.token}").content.decode()
    assert "<img" not in page


# ---------------------------------------------------------------- page images


def test_page_images_are_watermarked_for_the_viewer(issued: service.Issued) -> None:
    response = get(opened(issued), image_url(issued, 1, 800))
    assert response.status_code == 200
    assert response["Content-Type"] == "image/webp"
    assert response["Cache-Control"] == "private, max-age=300"
    with Image.open(io.BytesIO(response.content)) as picture:
        assert picture.format == "WEBP" and picture.width == 800
        assert picture.height > 450  # the page, plus the footer band


def test_two_viewers_get_different_images(document: Document) -> None:
    a = issue(document)
    b = issue(document, email="sam@eastgate.example")
    first = get(opened(a), image_url(a)).content
    second = get(opened(b), image_url(b)).content
    assert first != second


def test_the_plain_render_is_cached_but_never_served(issued: service.Issued) -> None:
    from snug.documents import storage

    browser = opened(issued)
    stamped = get(browser, image_url(issued)).content
    version = issued.link.document.latest_version()
    assert version is not None
    plain = storage.render_path(version, 1, 800)
    assert plain.exists() and plain.read_bytes() != stamped


@pytest.mark.parametrize(
    "url",
    ["/pages/4-800.webp", "/pages/0-800.webp", "/pages/1-1200.webp", "/pages/1-80000.webp"],
)
def test_page_images_outside_the_document_are_not_found(issued: service.Issued, url: str) -> None:
    assert get(opened(issued), f"/v/{issued.token}{url}").status_code == 404


def test_page_images_need_a_session(issued: service.Issued) -> None:
    gated = issue(issued.link.document, require_verification=True)
    response = get(Client(), image_url(gated))
    assert response.status_code == 303 and response["Content-Type"] != "image/webp"


def test_revoking_stops_page_images_at_once(issued: service.Issued) -> None:
    browser = opened(issued)
    assert get(browser, image_url(issued)).status_code == 200
    service.revoke(issued.link, by=None)
    assert get(browser, image_url(issued)).status_code == 404


def test_the_watermark_carries_todays_date(
    issued: service.Issued, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, str] = {}

    def capture(version: Any, page: int, width: int, **stamp: str) -> bytes:
        seen.update(stamp)
        return b"RIFF\x00\x00\x00\x00WEBPVP8 "

    monkeypatch.setattr("snug.rendering.pages.stamped_page", capture)
    get(opened(issued), image_url(issued))
    today = timezone.localdate()
    assert seen == {"email": INVESTOR, "date": f"{today.day} {today:%b %Y}", "ref": issued.link.ref}


def test_a_page_that_cannot_be_rendered_says_so(
    issued: service.Issued, monkeypatch: pytest.MonkeyPatch
) -> None:
    from snug.rendering import sandbox

    def fail(*args: Any, **kwargs: Any) -> bytes:
        raise sandbox.WorkerError("timeout")

    monkeypatch.setattr("snug.rendering.pages.stamped_page", fail)
    response = get(opened(issued), image_url(issued))
    assert response.status_code == 503
