"""Excel workbooks as documents: uploaded and checked, then shared for download only.

Decided 9 November 2026: .xlsx only; no preview, since the working file is
what an investor needs; each download stamped for its reader in the file's
properties, with every sheet left as uploaded. A spreadsheet's analytics are
who opened its card, and its downloads.
"""

import hashlib
import io
import time
import zipfile
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.utils import timezone

from snug.accounts import sessions
from snug.accounts.models import Admin
from snug.analytics.models import PageView, Visit
from snug.audit.models import AuditEvent
from snug.documents import storage
from snug.documents.ingest import UploadRejectedError, ingest
from snug.documents.models import Document, DocumentVersion
from snug.links import service
from snug.rendering import ooxml
from snug.rooms.models import Room, RoomDocument
from tests.office_files import MACRO_MAIN, make_xlsx, parts_of
from tests.pdfs import chunks, make_pdf

pytestmark = pytest.mark.django_db

INVESTOR = "priya@northfield.example"
MODEL = make_xlsx((("Summary", "visible"), ("Workings", "hidden"), ("P&amp;L", "visible")))
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture(autouse=True)
def own_data_folder(data_folder: Path) -> None:
    """Every test here gets its own data folder (see conftest.py)."""


@pytest.fixture
def admin() -> Admin:
    return Admin.objects.create(email="ruth@fernlea.example", is_active=True)


@pytest.fixture
def client(admin: Admin) -> Client:
    browser = Client()
    browser.force_login(admin)
    session = browser.session
    session[sessions.LOGIN_AT] = session[sessions.LAST_ACTIVITY] = time.time()
    session.save()
    return browser


@pytest.fixture
def model() -> Document:
    document = Document.objects.create(title="Financial model")
    ingest(document, chunks(MODEL), filename="Model v4.xlsx", uploaded_by=None)
    return document


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


# ---------------------------------------------------------------- uploading


def test_a_workbook_becomes_a_spreadsheet_document(model: Document) -> None:
    version = model.latest_version()
    assert version is not None
    assert version.kind == DocumentVersion.Kind.XLSX
    assert version.sheets == ["Summary", "P&L"]  # hidden sheets are not named
    assert version.page_count == 0
    assert version.stored_name.endswith(".xlsx")
    assert storage.original_path(version).read_bytes() == MODEL
    assert version.original_filename == "Model v4.xlsx"


@pytest.mark.parametrize(
    ("data", "said"),
    [
        (make_xlsx(main=MACRO_MAIN), "This file contains macros"),
        (
            b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512,
            "an older Office file (.doc, .ppt, or .xls)",
        ),
        (b"hello, this is a text file", "Snug takes PDFs, Word, PowerPoint, and Excel files"),
    ],
)
def test_what_is_not_a_plain_workbook_or_pdf_is_refused(data: bytes, said: str) -> None:
    document = Document.objects.create(title="Model")
    with pytest.raises(UploadRejectedError) as refused:
        ingest(document, chunks(data), filename="model.xlsx", uploaded_by=None)
    assert said in str(refused.value)
    assert not DocumentVersion.objects.exists()


def test_a_document_keeps_its_kind_from_version_to_version(model: Document) -> None:
    with pytest.raises(UploadRejectedError) as refused:
        ingest(model, chunks(make_pdf((960, 540))), filename="model.pdf", uploaded_by=None)
    assert str(refused.value) == (
        "Financial model is an Excel workbook. Upload a workbook as its new version, "
        "or upload the PDF as a new document."
    )
    deck = Document.objects.create(title="Seed deck")
    ingest(deck, chunks(make_pdf((960, 540))), filename="deck.pdf", uploaded_by=None)
    with pytest.raises(UploadRejectedError) as refused:
        ingest(deck, chunks(MODEL), filename="model.xlsx", uploaded_by=None)
    assert "Seed deck is a PDF." in str(refused.value)


def test_a_new_workbook_becomes_the_next_version(model: Document) -> None:
    updated = make_xlsx((("Summary", "visible"), ("Scenarios", "visible")))
    version = ingest(model, chunks(updated), filename="Model v5.xlsx", uploaded_by=None)
    assert (version.number, version.sheets) == (2, ["Summary", "Scenarios"])


def test_the_admin_can_upload_a_workbook(client: Client) -> None:
    upload = SimpleUploadedFile("Model.xlsx", MODEL, content_type=XLSX)
    response = client.post(
        "/admin/documents/new", {"title": "Financial model", "pdf": upload}, secure=True
    )
    assert response.status_code == 302
    page = client.get(response["Location"], secure=True).content.decode()
    assert "Uploaded, an Excel workbook with 2 sheets. Create a link to share it." in page
    event = AuditEvent.objects.get(action="document.version_uploaded")
    assert event.details["kind"] == "xlsx" and event.details["sheets"] == 2


def test_the_upload_forms_offer_workbooks(client: Client, model: Document) -> None:
    for url in ("/admin/documents/new", f"/admin/documents/{model.token}"):
        page = client.get(url, secure=True).content.decode()
        assert ",.docx," in page and ",.pptx," in page, url
        assert XLSX + ',.xlsx,video/mp4,.mp4"' in page, url


# ---------------------------------------------------------------- the admin's pages


def test_the_documents_list_says_what_a_workbook_is(client: Client, model: Document) -> None:
    page = client.get("/admin/documents/", secure=True).content.decode()
    assert "Excel workbook, 2 sheets" in page


def test_a_workbooks_page_lists_its_sheets_and_has_no_page_preview(
    client: Client, model: Document
) -> None:
    page = client.get(f"/admin/documents/{model.token}", secure=True).content.decode()
    assert "Excel workbook, 2 sheets" in page
    assert "Summary" in page and "P&amp;L" in page
    assert "Workings" not in page
    assert "/preview/" not in page
    assert "First page" not in page
    # Above a list of boxes, not a ruled list: the explanation sits under the heading.
    assert '<div class="section-head stacked"><h2>Sheets</h2>' in page
    assert "See who opened and downloaded it on the People page and in the audit log." in page


def test_the_link_form_says_a_spreadsheet_needs_downloads(client: Client, model: Document) -> None:
    page = client.get(f"/admin/links/for/{model.token}", secure=True).content.decode()
    assert "Without downloads, the link shows only the sheet names." in page


# ---------------------------------------------------------------- the investor's card


def test_a_workbook_link_shows_a_card_with_its_sheets(model: Document) -> None:
    issued = issue(model)
    browser = opened(issued)
    page = browser.get(f"/v/{issued.token}", secure=True).content.decode()
    assert "Financial model" in page and "Excel workbook" in page
    assert "Summary" in page and "P&amp;L" in page and "Workings" not in page
    assert f'href="/v/{issued.token}/download"' in page
    assert "the date, and this link are written into your copy's document properties" in page
    assert "/pages/" not in page  # no page images
    # The panel beside it describes a workbook, not pages and watermarks.
    assert "Financial model, an Excel workbook" in page
    assert "<dt>Access</dt><dd>Download</dd>" in page
    assert (
        "see when you open this workbook, how long its page is open, and when you download it"
        in page
    )
    # The card says it under the download button, so the panel does not (13 November 2026).
    assert page.count("document properties") == 1
    assert "watermark" not in page


def test_opening_a_workbook_is_counted_once_per_visit(model: Document) -> None:
    issued = issue(model)
    browser = opened(issued)
    browser.get(f"/v/{issued.token}", secure=True)
    browser.get(f"/v/{issued.token}", secure=True)
    assert Visit.objects.filter(document=model).count() == 1
    assert AuditEvent.objects.filter(action="viewer.document_viewed").count() == 1


def test_a_workbook_has_no_pages_to_fetch(model: Document) -> None:
    issued = issue(model)
    browser = opened(issued)
    assert browser.get(f"/v/{issued.token}/pages/1-800.webp", secure=True).status_code == 404


def test_the_download_is_the_workbook_stamped_for_its_reader(model: Document) -> None:
    issued = issue(model)
    response = opened(issued).get(f"/v/{issued.token}/download", secure=True)
    assert response.status_code == 200
    assert response["Content-Type"] == XLSX
    assert 'filename="Financial model.xlsx"' in response["Content-Disposition"]
    body = response.content
    before, after = parts_of(MODEL), parts_of(body)
    assert after["xl/worksheets/sheet1.xml"] == before["xl/worksheets/sheet1.xml"]
    props = ooxml._parse(after["docProps/custom.xml"])
    names = {p.get("name"): "".join(p.itertext()) for p in props}
    assert names["Shared with"] == INVESTOR
    assert names["Snug link"] == issued.link.ref
    event = AuditEvent.objects.get(action="viewer.downloaded")
    assert event.details["sha256"] == hashlib.sha256(body).hexdigest()
    with zipfile.ZipFile(io.BytesIO(body)) as package:
        assert package.testzip() is None


def test_with_downloads_off_the_card_says_so(model: Document) -> None:
    issued = issue(model, allow_download=False)
    browser = opened(issued)
    page = browser.get(f"/v/{issued.token}", secure=True).content.decode()
    assert "Downloads are turned off for this link" in page
    assert "/download" not in page
    assert browser.get(f"/v/{issued.token}/download", secure=True).status_code == 404


def test_a_room_lists_a_workbook_by_its_sheets(model: Document) -> None:
    room = Room.objects.create(name="Series A")
    RoomDocument.objects.create(room=room, document=model)
    issued = service.create(
        room=room,
        email=INVESTOR,
        note="",
        expires_at=None,
        require_verification=False,
        allow_download=True,
        created_by=None,
    )
    browser = opened(issued)
    page = browser.get(f"/v/{issued.token}", secure=True).content.decode()
    assert "Excel workbook, 2 sheets" in page
    card = browser.get(f"/v/{issued.token}/d/{model.token}", secure=True).content.decode()
    assert f'href="/v/{issued.token}/d/{model.token}/download"' in card


def _visit_on(html: str) -> str:
    start = html.index('data-visit="') + len('data-visit="')
    return html[start : html.index('"', start)]


def test_the_card_sends_heartbeats_for_its_one_page(model: Document) -> None:
    """Decided 9 November 2026: time on the workbook's page is counted, as one page."""
    issued = issue(model)
    html = opened(issued).get(f"/v/{issued.token}", secure=True).content.decode()
    assert 'data-page="1"' in html
    assert f'data-beat-url="/v/{issued.token}/beat"' in html
    assert 'name="csrfmiddlewaretoken"' in html
    assert "snug/js/viewer.js" in html
    assert _visit_on(html) == Visit.objects.get(document=model).token


def test_time_on_the_card_is_counted(model: Document, monkeypatch: pytest.MonkeyPatch) -> None:
    now = [timezone.now()]
    monkeypatch.setattr("django.utils.timezone.now", lambda: now[0])
    issued = issue(model)
    browser = opened(issued)
    visit = _visit_on(browser.get(f"/v/{issued.token}", secure=True).content.decode())
    for kind in ("beat", "beat", "end"):
        now[0] += timedelta(seconds=12)
        form = {"visit": visit, "page": "1", "kind": kind}
        assert browser.post(f"/v/{issued.token}/beat", form, secure=True).status_code == 204
    assert PageView.objects.get(visit__document=model, page=1).seconds == pytest.approx(36)


def test_the_people_page_shows_one_cell_for_a_workbook(client: Client, model: Document) -> None:
    issued = issue(model)
    browser = opened(issued)
    browser.get(f"/v/{issued.token}", secure=True)
    PageView.objects.filter(visit__document=model).update(seconds=95)
    browser.get(f"/v/{issued.token}/download", secure=True)
    page = client.get("/admin/people/", secure=True).content.decode()
    assert INVESTOR in page and "Financial model" in page
    assert "Spreadsheet" not in page
    strip = page[page.index("Financial model") :]
    strip = strip[strip.index("<svg") : strip.index("</svg>")]
    assert strip.count("<rect") == 1
    assert "<title>Page open for 1 min 35 s</title>" in strip
    assert (
        f'aria-label="{INVESTOR}, Financial model: had the workbook\'s page open for 1 min 35 s"'
        in strip
    )


def test_a_workbook_opened_before_its_time_was_counted_still_shows(
    client: Client, model: Document
) -> None:
    """Visits from before 9 November 2026 have no page: the cell shows it was opened."""
    issued = issue(model)
    opened(issued).get(f"/v/{issued.token}", secure=True)
    PageView.objects.filter(visit__document=model).delete()
    Visit.objects.filter(document=model).update(current_page=0)
    page = client.get("/admin/people/", secure=True).content.decode()
    strip = page[page.index("Financial model") :]
    strip = strip[strip.index("<svg") : strip.index("</svg>")]
    assert strip.count("<rect") == 1
    assert "<title>Page open for 0 s</title>" in strip


# ---------------------------------------------------------------- the founder's description


def describe(client: Client, document: Document, text: str) -> Any:
    url = f"/admin/documents/{document.token}/description"
    return client.post(url, {"description": text}, secure=True)


def test_a_founder_can_describe_a_workbook(client: Client, model: Document) -> None:
    response = describe(client, model, "  Base case on Summary.  ")
    assert response["Location"] == f"/admin/documents/{model.token}#description"
    model.refresh_from_db()
    assert model.description == "Base case on Summary."
    page = client.get(response["Location"], secure=True).content.decode()
    assert "Description saved. Investors see it above the list of sheets." in page
    assert ">Base case on Summary.</textarea>" in page
    event = AuditEvent.objects.get(action="document.description_changed")
    assert event.details == {"document": model.token, "characters": 21}


def test_the_card_shows_the_description_above_the_sheets(model: Document) -> None:
    model.description = "Base case on Summary.\nScenarios use the <b>toggle</b> in B2."
    model.save()
    issued = issue(model)
    page = opened(issued).get(f"/v/{issued.token}", secure=True).content.decode()
    said = "Base case on Summary.<br>Scenarios use the &lt;b&gt;toggle&lt;/b&gt; in B2."
    assert said in page
    assert page.index(said) < page.index("Sheets")


def test_without_a_description_the_card_has_none(model: Document) -> None:
    issued = issue(model)
    page = opened(issued).get(f"/v/{issued.token}", secure=True).content.decode()
    assert 'class="description"' not in page


def test_a_description_over_the_limit_is_refused_beside_the_field(
    client: Client, model: Document
) -> None:
    response = describe(client, model, "x" * 1001)
    assert response.status_code == 400
    text = response.content.decode()
    assert 'id="description-error"' in text
    assert "Keep the description to 1,000 characters or fewer." in text
    model.refresh_from_db()
    assert model.description == ""


def test_an_empty_description_removes_it(client: Client, model: Document) -> None:
    describe(client, model, "Base case on Summary.")
    response = describe(client, model, "   ")
    model.refresh_from_db()
    assert model.description == ""
    page = client.get(response["Location"], secure=True).content.decode()
    assert "Description removed." in page


def test_a_pdf_has_no_description(client: Client) -> None:
    deck = Document.objects.create(title="Seed deck")
    ingest(deck, chunks(make_pdf((960, 540))), filename="deck.pdf", uploaded_by=None)
    assert describe(client, deck, "Hello").status_code == 404
    page = client.get(f"/admin/documents/{deck.token}", secure=True).content.decode()
    assert 'id="description"' not in page


def test_every_admin_page_works_once_a_workbook_has_been_opened(
    client: Client, model: Document
) -> None:
    """Found on the dev instance: the workbook's page drew a reading chart with no pages."""
    issued = issue(model)
    browser = opened(issued)
    browser.get(f"/v/{issued.token}", secure=True)
    browser.get(f"/v/{issued.token}/download", secure=True)
    for path in ("", "documents/", f"documents/{model.token}", "people/", "links/", "audit/"):
        response = client.get(f"/admin/{path}", secure=True)
        assert response.status_code == 200, path
    page = client.get(f"/admin/documents/{model.token}", secure=True).content.decode()
    assert "How it was read" not in page  # no reading chart for a workbook
