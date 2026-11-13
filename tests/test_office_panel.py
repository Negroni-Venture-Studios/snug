"""The Preview panel on an Office file's page: live progress while the converter works.

Asked for 12 November 2026: the founder can write the description and make
links while the preview is made, so the upload returns at once and the
panel updates itself. admin.js asks for the panel every 2 seconds and swaps
it in; the server draws it, so there is no inline script or style.
"""

import pytest
from django.test import Client

from snug.audit.models import AuditEvent
from snug.documents.models import Document, DocumentVersion
from snug.office import previews
from tests.fake_converter import Converter
from tests.office_files import make_docx, make_pptx
from tests.pdfs import make_pdf
from tests.test_office_previews import fresh, previewed, upload

pytestmark = pytest.mark.django_db

Preview = DocumentVersion.Preview


def detail(browser: Client, document: Document) -> str:
    return browser.get(f"/admin/documents/{document.token}", secure=True).content.decode()


def panel(browser: Client, document: Document) -> str:
    response = browser.get(f"/admin/documents/{document.token}/preview", secure=True)
    assert response.status_code == 200
    return response.content.decode()


def test_while_it_waits_the_page_has_a_live_panel(
    converter: Converter, admin_browser: Client
) -> None:
    deck = Document.objects.create(title="Board deck")
    upload(deck, make_pptx(3))
    page = detail(admin_browser, deck)
    assert f'data-preview-poll="/admin/documents/{deck.token}/preview"' in page
    assert "<progress></progress>" in page
    assert 'role="status" aria-live="polite" data-preview-stage>Waiting for the converter' in page
    assert "You can write the description and create links while it is made." in page
    assert "Shared for download only" not in page  # a preview is coming
    assert "Investors see the new version once its preview is ready." in page


def test_asking_for_the_panel_moves_the_queue_on(
    converter: Converter, admin_browser: Client
) -> None:
    deck = Document.objects.create(title="Board deck")
    version = upload(deck, make_pptx(3))
    said = panel(admin_browser, deck)
    assert fresh(version).preview == Preview.CONVERTING
    assert "Converting in LibreOffice" in said
    assert said.lstrip().startswith('<section class="section preview-panel" id="preview"')


def test_the_panel_says_how_many_files_are_ahead(
    converter: Converter, admin_browser: Client
) -> None:
    first = Document.objects.create(title="Board deck")
    upload(first, make_pptx(3))
    second = Document.objects.create(title="Board memo")
    upload(second, make_docx(), "Memo.docx")
    third = Document.objects.create(title="Notes")
    upload(third, make_docx(), "Notes.docx")
    previews.pump()  # the deck goes to the converter
    assert "Waiting for the converter, 2 files ahead" in panel(admin_browser, third)
    assert "Waiting for the converter, 1 file ahead" in panel(admin_browser, second)


def test_when_the_preview_is_ready_the_panel_shows_the_first_page_and_stops(
    converter: Converter, admin_browser: Client
) -> None:
    deck = Document.objects.create(title="Board deck")
    version = upload(deck, make_pptx(3))
    previews.pump()
    converter.answer(make_pdf(*[(720, 540)] * 3))
    said = panel(admin_browser, deck)
    assert "data-preview-poll" not in said
    assert f"/admin/documents/{deck.token}/v{version.number}/page-1.webp" in said
    assert "Ready, 3 pages." in said


def test_a_failed_preview_says_why_and_can_be_tried_again(
    converter: Converter, admin_browser: Client
) -> None:
    deck = Document.objects.create(title="Board deck")
    version = upload(deck, make_pptx(3))
    previews.pump()
    converter.answer(error="timeout")
    said = panel(admin_browser, deck)
    assert "data-preview-poll" not in said
    assert "LibreOffice took longer than two minutes with this file." in said
    assert "Investors see a card describing the file instead." in said
    retry = f"/admin/documents/{deck.token}/v{version.number}/preview"
    assert f'action="{retry}#preview"' in said
    response = admin_browser.post(retry, secure=True)
    assert response.status_code == 302
    assert fresh(version).preview == Preview.PENDING
    assert AuditEvent.objects.filter(action="document.preview_retried").exists()
    assert "Trying again." in detail(admin_browser, deck)


def test_only_a_failed_preview_can_be_tried_again(
    converter: Converter, admin_browser: Client
) -> None:
    deck = Document.objects.create(title="Board deck")
    version = upload(deck, make_pptx(3))
    retry = f"/admin/documents/{deck.token}/v{version.number}/preview"
    assert admin_browser.post(retry, secure=True).status_code == 404


def test_a_new_version_being_prepared_is_named_on_the_page(
    converter: Converter, admin_browser: Client
) -> None:
    deck = previewed(converter, pages=3)
    upload(deck, make_pptx(5))
    page = detail(admin_browser, deck)
    assert "Version 2 is being prepared. Links show version 1 until it is ready." in page
    assert "Version 2" in page[page.index('id="versions"') :]


def test_the_upload_notice_says_a_preview_is_being_made(
    converter: Converter, admin_browser: Client
) -> None:
    from django.core.files.uploadedfile import SimpleUploadedFile

    upload_file = SimpleUploadedFile("Deck.pptx", make_pptx(3), "application/octet-stream")
    response = admin_browser.post(
        "/admin/documents/new", {"title": "Board deck", "pdf": upload_file}, secure=True
    )
    page = admin_browser.get(response["Location"], secure=True).content.decode()
    assert "Its preview is being made." in page


def test_the_documents_list_says_which_previews_are_being_made(
    converter: Converter, admin_browser: Client
) -> None:
    upload(Document.objects.create(title="Board deck"), make_pptx(3))
    page = admin_browser.get("/admin/documents/", secure=True).content.decode()
    assert "Preview being made" in page


def test_without_the_converter_there_is_no_panel(
    data_folder: object, admin_browser: Client
) -> None:
    deck = Document.objects.create(title="Board deck")
    upload(deck, make_pptx(3))
    assert 'id="preview"' not in detail(admin_browser, deck)


# ---------------------------------------------------------------- the upload page


NEW = "/admin/documents/new"
PAGE_BY_PAGE = "Investors read a PDF, Word, PowerPoint, or Excel file page by page"
DOWNLOADED = "download a Word, PowerPoint, or Excel file to open in its own app"


def test_with_previews_the_upload_page_says_office_files_are_read_page_by_page(
    converter: Converter, admin_browser: Client
) -> None:
    # Found on the rc.3 server, 12 November 2026: it said they were downloaded.
    page = admin_browser.get(NEW, secure=True).content.decode()
    assert PAGE_BY_PAGE in page and DOWNLOADED not in page


def test_with_previews_off_the_upload_page_says_office_files_are_downloaded(
    converter: Converter, admin_browser: Client
) -> None:
    from snug.core.models import SiteSettings

    site = SiteSettings.load()
    site.office_previews = False
    site.save()
    page = admin_browser.get(NEW, secure=True).content.decode()
    assert DOWNLOADED in page and PAGE_BY_PAGE not in page


def test_without_the_converter_the_upload_page_says_office_files_are_downloaded(
    data_folder: object, admin_browser: Client
) -> None:
    page = admin_browser.get(NEW, secure=True).content.decode()
    assert DOWNLOADED in page and PAGE_BY_PAGE not in page


# ---------------------------------------------------------------- the link form

READ_ONLY = "Without downloads, they can only read it in the browser."
CARD_ONLY = "Without downloads, the link shows only what the file is."
ROOM_CARD_ONLY = "Without downloads, an Office file shows only what it is."


def test_the_link_form_says_a_previewed_file_is_still_read_without_downloads(
    converter: Converter, admin_browser: Client
) -> None:
    # Found in the wording pass, 13 November 2026: it said investors saw only a card.
    deck = previewed(converter)
    page = admin_browser.get(f"/admin/links/for/{deck.token}", secure=True).content.decode()
    assert READ_ONLY in page and CARD_ONLY not in page


def test_the_link_form_says_a_file_without_a_preview_shows_only_a_card(
    data_folder: object, admin_browser: Client
) -> None:
    deck = Document.objects.create(title="Board deck")
    upload(deck, make_pptx(3))
    page = admin_browser.get(f"/admin/links/for/{deck.token}", secure=True).content.decode()
    assert CARD_ONLY in page and READ_ONLY not in page


def test_a_rooms_link_form_mentions_the_card_only_without_previews(
    data_folder: object, admin_browser: Client
) -> None:
    from snug.rooms.models import Room

    room = Room.objects.create(name="Seed round")
    page = admin_browser.get(f"/admin/links/for-room/{room.token}", secure=True)
    assert ROOM_CARD_ONLY in page.content.decode()


def test_with_previews_a_rooms_link_form_does_not_mention_the_card(
    converter: Converter, admin_browser: Client
) -> None:
    from snug.rooms.models import Room

    room = Room.objects.create(name="Seed round")
    page = admin_browser.get(f"/admin/links/for-room/{room.token}", secure=True)
    assert ROOM_CARD_ONLY not in page.content.decode()
