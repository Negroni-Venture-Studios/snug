"""Word documents and PowerPoint presentations, checked and stamped like workbooks.

Decided 12 November 2026: .docx and .pptx are shared for download, with the
reader's details in the file's properties, as .xlsx is. The checker that reads
workbooks reads all three, and also refuses a file that would fetch a template,
picture or object from another computer when it is opened.
"""

import time
from pathlib import Path
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from snug.accounts import sessions
from snug.accounts.models import Admin
from snug.analytics.models import PageView
from snug.audit.models import AuditEvent
from snug.documents import storage
from snug.documents.ingest import UploadRejectedError, ingest
from snug.documents.models import Document, DocumentVersion
from snug.links import service
from snug.rendering import ooxml, sandbox, worker
from tests.office_files import (
    DOCM_MAIN,
    PPTM_MAIN,
    REL_HYPERLINK,
    REL_IMAGE,
    REL_TEMPLATE,
    make_docx,
    make_pptx,
    make_xlsx,
    parts_of,
    relationship,
)
from tests.pdfs import chunks
from tests.test_ooxml import STAMP, custom_properties
from tests.test_render_worker import _confined, linux_only

DOTX_MAIN = "application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml"


def refusal(data: bytes) -> str:
    with pytest.raises(ooxml.Refused) as refused:
        ooxml.inspect(data)
    return refused.value.code


# ---------------------------------------------------------------- which format it is


def test_each_format_is_told_apart_by_its_main_part() -> None:
    assert ooxml.inspect(make_docx()) == {"format": "docx"}
    assert ooxml.inspect(make_pptx(24)) == {"format": "pptx", "slides": 24}
    assert ooxml.inspect(make_xlsx()) == {"format": "xlsx", "sheets": ["Model"]}


def test_a_presentation_with_no_slides_is_refused() -> None:
    assert refusal(make_pptx(0)) == "no_slides"


@pytest.mark.parametrize("main", [DOCM_MAIN, PPTM_MAIN])
def test_macro_enabled_word_and_powerpoint_files_are_refused(main: str) -> None:
    data = make_docx(main=main) if "word" in main else make_pptx(main=main)
    assert refusal(data) == "macros"


def test_a_template_is_not_taken_for_a_document() -> None:
    """Only the three plain formats: templates and shows are saved as documents first."""
    assert refusal(make_docx(main=DOTX_MAIN)) == "not_office"


# ---------------------------------------------------------------- things fetched from elsewhere


@pytest.mark.parametrize(
    "target",
    [
        "https://templates.example/memo.dotm",
        "HTTP://templates.example/memo.dotm",
        "\\\\fileserver\\share\\memo.dotm",
        "//fileserver/share/memo.dotm",
        "file://fileserver/share/memo.dotm",
        "file:////fileserver/share/memo.dotm",
        "  https://templates.example/memo.dotm",
    ],
)
def test_a_template_on_another_computer_is_refused(target: str) -> None:
    """A remote template is a known way to deliver malware, or to leak a Windows password hash."""
    template = relationship("rId1", REL_TEMPLATE, target, external=True)
    assert refusal(make_docx(settings_rels=(template,))) == "external"


@pytest.mark.parametrize(
    "target",
    [
        "file:///C:/Users/ruth/AppData/Roaming/Microsoft/Templates/Normal.dotm",
        "C:\\Users\\ruth\\Templates\\Board.dotx",
        "Board.dotx",
    ],
)
def test_a_template_on_the_authors_own_computer_is_fine(target: str) -> None:
    """Word records the template a document was made from; on the author's disk it is harmless."""
    template = relationship("rId1", REL_TEMPLATE, target, external=True)
    assert ooxml.inspect(make_docx(settings_rels=(template,))) == {"format": "docx"}


def test_a_picture_linked_from_the_internet_is_refused() -> None:
    picture = relationship("rId9", REL_IMAGE, "https://cdn.example/chart.png", external=True)
    assert refusal(make_pptx(slide_rels=(picture,))) == "external"


@pytest.mark.parametrize(
    "target", ["https://fernlea.example/demo", "\\\\fileserver\\share\\notes.docx"]
)
def test_a_hyperlink_is_only_followed_when_clicked_and_is_fine(target: str) -> None:
    link = relationship("rId2", REL_HYPERLINK, target, external=True)
    assert ooxml.inspect(make_docx(document_rels=(link,))) == {"format": "docx"}


# ---------------------------------------------------------------- stamping a download


@pytest.mark.parametrize(
    ("data", "body"),
    [(make_docx(), "word/document.xml"), (make_pptx(), "ppt/slides/slide2.xml")],
)
def test_a_download_names_its_reader_and_leaves_the_content_alone(data: bytes, body: str) -> None:
    stamped = ooxml.stamp(data, **STAMP)
    assert parts_of(stamped)[body] == parts_of(data)[body]
    assert custom_properties(stamped)["Shared with"] == "priya@northfield.example"
    assert ooxml.inspect(stamped) == ooxml.inspect(data)  # still the same kind of file


# ---------------------------------------------------------------- through the worker


@pytest.mark.parametrize(
    ("data", "facts"),
    [
        (make_docx(), {"format": "docx"}),
        (make_pptx(5), {"format": "pptx", "slides": 5}),
        (make_xlsx(), {"format": "xlsx", "sheets": ["Model"]}),
    ],
)
def test_the_worker_inspects_each_format(
    tmp_path: Path, data: bytes, facts: dict[str, Any]
) -> None:
    path = tmp_path / "upload"
    path.write_bytes(data)
    assert sandbox.inspect_office(path) == facts


def test_the_worker_stamps_a_presentation(tmp_path: Path) -> None:
    path = tmp_path / "deck.pptx"
    path.write_bytes(make_pptx())
    stamped = sandbox.stamp_office(path, **STAMP, limit=1024 * 1024)
    assert custom_properties(stamped)["Snug link"] == "L-7Q2K"


@pytest.mark.parametrize(
    ("data", "said"),
    [
        (make_pptx(main=PPTM_MAIN), "This file contains macros"),
        (
            make_docx(
                settings_rels=(
                    relationship("rId1", REL_TEMPLATE, "https://t.example/x.dotm", external=True),
                )
            ),
            "fetches a template, picture or object from another computer",
        ),
        (make_pptx(0), "This presentation has no slides."),
    ],
)
def test_a_refused_file_says_why_in_plain_words(tmp_path: Path, data: bytes, said: str) -> None:
    path = tmp_path / "upload"
    path.write_bytes(data)
    with pytest.raises(sandbox.WorkerError) as error:
        sandbox.inspect_office(path)
    assert said in str(error.value)


@pytest.mark.parametrize(
    "output",
    [b'{"format": "docm"}', b'{"format": "pptx", "slides": 0}', b'{"format": "pptx"}'],
)
def test_odd_answers_from_the_worker_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output: bytes
) -> None:
    path = tmp_path / "upload"
    path.write_bytes(make_docx())
    monkeypatch.setattr(sandbox, "run", lambda *args, **kwargs: output)
    with pytest.raises(sandbox.WorkerError):
        sandbox.inspect_office(path)


@pytest.mark.parametrize(
    "stamp",
    [
        {**STAMP, "email": "a\x07@b.example"},
        {**STAMP, "ref": "L-1/../x"},
        {**STAMP, "date": "x" * 500},
    ],
)
def test_the_stamp_text_is_checked_before_it_reaches_a_file(
    tmp_path: Path, stamp: dict[str, str]
) -> None:
    """The worker refuses a control character, an odd reference or an over-long stamp."""
    path = tmp_path / "memo.docx"
    path.write_bytes(make_docx())
    with pytest.raises(sandbox.WorkerError) as error:
        sandbox.stamp_office(path, **stamp, limit=1024 * 1024)
    assert error.value.code == "usage"


@linux_only
def test_a_confined_worker_can_read_and_stamp_word_and_powerpoint(tmp_path: Path) -> None:
    script = (
        "from snug.rendering import ooxml\n"
        f"for data in ({make_docx()!r}, {make_pptx()!r}):\n"
        "    print(ooxml.inspect(data)['format'])\n"
        "    print(ooxml.stamp(data, email='a@b.example', date='1 Jan 2026', ref='L-1')[:2])\n"
    )
    result = _confined(script, tmp_path)
    lines = result.stdout.splitlines()
    assert worker.fully_confined(lines[0]), result.stdout + result.stderr
    assert lines[1:] == ["docx", "b'PK'", "pptx", "b'PK'"], result.stdout + result.stderr


# ---------------------------------------------------------------- uploading


@pytest.fixture
def own_data_folder(data_folder: Path) -> None:
    """A data folder of its own for each upload test (see conftest.py)."""


@pytest.mark.django_db
@pytest.mark.usefixtures("own_data_folder")
@pytest.mark.parametrize(
    ("data", "kind", "slides", "described"),
    [
        (make_docx(), "docx", 0, "Word document"),
        (make_pptx(24), "pptx", 24, "PowerPoint presentation, 24 slides"),
        (make_pptx(1), "pptx", 1, "PowerPoint presentation, 1 slide"),
    ],
)
def test_word_and_powerpoint_files_become_documents(
    data: bytes, kind: str, slides: int, described: str
) -> None:
    document = Document.objects.create(title="Board pack")
    version = ingest(document, chunks(data), filename=f"Board pack.{kind}", uploaded_by=None)
    assert (version.kind, version.slides, version.page_count) == (kind, slides, 0)
    assert version.describe == described
    assert version.stored_name.endswith(f".{kind}")
    assert storage.original_path(version).read_bytes() == data
    assert version.is_office and not version.is_workbook


@pytest.mark.django_db
@pytest.mark.usefixtures("own_data_folder")
@pytest.mark.parametrize(
    ("data", "said"),
    [
        (
            b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512,
            "This is an older Office file (.doc, .ppt or .xls), or one saved with a password. "
            "Save it as .docx, .pptx or .xlsx without a password, then upload it again.",
        ),
        (
            b"hello, this is a text file",
            "Snug takes PDFs, Word, PowerPoint and Excel files (.docx, .pptx and .xlsx), "
            "and MP4 videos. Export other files to PDF first.",
        ),
        (make_docx(main=DOCM_MAIN), "This file contains macros"),
    ],
)
def test_what_snug_cannot_share_is_refused(data: bytes, said: str) -> None:
    document = Document.objects.create(title="Board pack")
    with pytest.raises(UploadRejectedError) as refused:
        ingest(document, chunks(data), filename="upload", uploaded_by=None)
    assert said in str(refused.value)
    assert not DocumentVersion.objects.exists()


@pytest.mark.django_db
@pytest.mark.usefixtures("own_data_folder")
def test_a_document_keeps_its_office_format_from_version_to_version() -> None:
    memo = Document.objects.create(title="Board memo")
    ingest(memo, chunks(make_docx()), filename="memo.docx", uploaded_by=None)
    with pytest.raises(UploadRejectedError) as refused:
        ingest(memo, chunks(make_pptx()), filename="deck.pptx", uploaded_by=None)
    assert str(refused.value) == (
        "Board memo is a Word document. Upload a Word document as its new version, "
        "or upload the presentation as a new document."
    )


# ---------------------------------------------------------------- admins and investors

DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PPTX_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


@pytest.fixture
def deck(own_data_folder: None) -> Document:
    document = Document.objects.create(title="Board deck")
    ingest(document, chunks(make_pptx(12)), filename="Board deck.pptx", uploaded_by=None)
    return document


@pytest.fixture
def memo(own_data_folder: None) -> Document:
    document = Document.objects.create(title="Board memo")
    ingest(document, chunks(make_docx()), filename="Board memo.docx", uploaded_by=None)
    return document


@pytest.fixture
def client() -> Client:
    admin = Admin.objects.create(email="ruth@fernlea.example", is_active=True)
    browser = Client()
    browser.force_login(admin)
    session = browser.session
    session[sessions.LOGIN_AT] = session[sessions.LAST_ACTIVITY] = time.time()
    session.save()
    return browser


def issue(document: Document, **options: Any) -> service.Issued:
    fields: dict[str, Any] = {
        "email": STAMP["email"],
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


@pytest.mark.django_db
@pytest.mark.usefixtures("own_data_folder")
@pytest.mark.parametrize(
    ("name", "data", "said", "details"),
    [
        (
            "Deck.pptx",
            make_pptx(12),
            "Uploaded, a PowerPoint presentation with 12 slides.",
            {"slides": 12},
        ),
        ("Memo.docx", make_docx(), "Uploaded, a Word document.", {}),
    ],
)
def test_the_admin_can_upload_word_and_powerpoint(
    client: Client, name: str, data: bytes, said: str, details: dict[str, int]
) -> None:
    upload = SimpleUploadedFile(name, data, content_type="application/octet-stream")
    response = client.post(
        "/admin/documents/new", {"title": "Board pack", "pdf": upload}, secure=True
    )
    assert response.status_code == 302
    page = client.get(response["Location"], secure=True).content.decode()
    assert f"{said} Create a link to share it." in page
    event = AuditEvent.objects.get(action="document.version_uploaded")
    assert {k: event.details.get(k) for k in details} == details
    assert "pages" not in event.details and "sheets" not in event.details


@pytest.mark.django_db
def test_a_presentations_page_has_a_description_and_no_page_preview(
    client: Client, deck: Document
) -> None:
    page = client.get(f"/admin/documents/{deck.token}", secure=True).content.decode()
    assert "PowerPoint presentation, 12 slides" in page
    assert 'id="description"' in page and "above the download button" in page
    assert "Shared for download only, as the presentation itself." in page
    assert "First page" not in page and "How it was read" not in page
    response = client.post(
        f"/admin/documents/{deck.token}/description",
        {"description": "Start at slide 3."},
        secure=True,
    )
    shown = client.get(response["Location"], secure=True).content.decode()
    assert "Description saved. Investors see it above the download button." in shown


@pytest.mark.django_db
def test_a_presentation_link_shows_a_card_and_downloads_the_stamped_file(deck: Document) -> None:
    deck.description = "Start at slide 3."
    deck.save()
    issued = issue(deck)
    browser = opened(issued)
    page = browser.get(f"/v/{issued.token}", secure=True).content.decode()
    assert "A PowerPoint presentation of 12 slides" in page
    assert "Start at slide 3." in page
    assert "Download the presentation" in page
    assert "Sheets" not in page and "/page/" not in page
    assert "Board deck, a PowerPoint presentation" in page
    assert "when you open this presentation, how long its page is open" in page
    response = browser.get(f"/v/{issued.token}/download", secure=True)
    assert response["Content-Type"] == PPTX_TYPE
    assert 'filename="Board deck.pptx"' in response["Content-Disposition"]
    assert custom_properties(response.content)["Shared with"] == STAMP["email"]
    assert ooxml.inspect(response.content) == {"format": "pptx", "slides": 12}


@pytest.mark.django_db
def test_a_word_document_downloads_as_one(memo: Document) -> None:
    issued = issue(memo)
    browser = opened(issued)
    page = browser.get(f"/v/{issued.token}", secure=True).content.decode()
    assert "A Word document," in page and "Download the Word document" in page
    response = browser.get(f"/v/{issued.token}/download", secure=True)
    assert response["Content-Type"] == DOCX_TYPE
    assert 'filename="Board memo.docx"' in response["Content-Disposition"]


@pytest.mark.django_db
def test_with_downloads_off_a_presentation_says_so(deck: Document) -> None:
    issued = issue(deck, allow_download=False)
    page = opened(issued).get(f"/v/{issued.token}", secure=True).content.decode()
    assert "so this presentation cannot be opened" in page


@pytest.mark.django_db
def test_the_people_page_shows_one_cell_for_a_presentation(client: Client, deck: Document) -> None:
    issued = issue(deck)
    opened(issued).get(f"/v/{issued.token}", secure=True)
    PageView.objects.filter(visit__document=deck).update(seconds=95)
    page = client.get("/admin/people/", secure=True).content.decode()
    strip = page[page.index("Board deck") :]
    strip = strip[strip.index("<svg") : strip.index("</svg>")]
    assert strip.count("<rect") == 1
    assert "Board deck: had the presentation's page open for 1 min 35 s" in strip


@pytest.mark.django_db
def test_the_link_form_says_what_a_download_of_an_office_file_is(
    client: Client, memo: Document
) -> None:
    page = client.get(f"/admin/links/for/{memo.token}", secure=True).content.decode()
    assert (
        "The file itself, with their email address, the date and the link in its properties."
        in page
    )


# ---------------------------------------------------------------- fields and links in the content


def field(instruction: str) -> str:
    """A Word field, its instruction split across runs as Word writes it."""
    half = len(instruction) // 2
    return (
        '<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        f'<w:r><w:instrText xml:space="preserve">{instruction[:half]}</w:instrText></w:r>'
        f'<w:r><w:instrText xml:space="preserve">{instruction[half:]}</w:instrText></w:r>'
        '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>shown</w:t></w:r>'
        '<w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>'
    )


@pytest.mark.parametrize(
    "body",
    [
        field(' INCLUDEPICTURE "https://tracker.example/pixel.png" \\d '),
        field(' INCLUDETEXT "\\\\\\\\fileserver\\\\share\\\\terms.docx" '),
        field(' LINK Excel.Sheet.12 "https://files.example/model.xlsx" "Sheet1!R1C1" '),
        field(' includepicture "http://tracker.example/pixel.png" '),
        '<w:p><w:fldSimple w:instr=" INCLUDEPICTURE &quot;https://tracker.example/a.png&quot; ">'
        "<w:r><w:t>x</w:t></w:r></w:fldSimple></w:p>",
    ],
)
def test_a_field_that_fetches_from_another_computer_is_refused(body: str) -> None:
    """Word updates INCLUDEPICTURE, INCLUDETEXT and LINK fields from wherever they point."""
    assert refusal(make_docx(body=body)) == "external"


@pytest.mark.parametrize(
    "body",
    [
        field(' DDEAUTO c:\\\\windows\\\\system32\\\\cmd.exe "/k calc.exe" '),
        field(' DDE excel "C:\\\\model.xlsx" "Sheet1!R1C1" '),
    ],
)
def test_a_dde_field_is_refused(body: str) -> None:
    """A DDE field asks Word to start another program, which has delivered malware."""
    assert refusal(make_docx(body=body)) == "dde"


@pytest.mark.parametrize(
    "body",
    [
        field(' INCLUDEPICTURE "C:\\\\Users\\\\ruth\\\\Pictures\\\\logo.png" '),
        field(' HYPERLINK "https://fernlea.example/demo" '),
        field(" PAGE "),
        field(' TOC \\o "1-3" '),
    ],
)
def test_ordinary_fields_are_fine(body: str) -> None:
    assert ooxml.inspect(make_docx(body=body)) == {"format": "docx"}


def test_a_picture_cannot_borrow_a_hyperlinks_relationship() -> None:
    """A relationship typed as a hyperlink may point anywhere, so only a hyperlink may use it."""
    link = relationship("rId7", REL_HYPERLINK, "https://tracker.example/pixel.png", external=True)
    picture = '<p:spTree><p:pic><p:blipFill><a:blip r:link="rId7"/></p:blipFill></p:pic></p:spTree>'
    assert refusal(make_pptx(slide_rels=(link,), slide_body=picture)) == "external"


def test_a_shapes_hyperlink_is_fine() -> None:
    link = relationship("rId7", REL_HYPERLINK, "https://fernlea.example/demo", external=True)
    shape = (
        '<p:spTree><p:sp><p:nvSpPr><p:cNvPr id="2" name="Link">'
        '<a:hlinkClick r:id="rId7"/></p:cNvPr></p:nvSpPr></p:sp></p:spTree>'
    )
    assert ooxml.inspect(make_pptx(slide_rels=(link,), slide_body=shape))["format"] == "pptx"


@pytest.mark.parametrize("element", ["ddeLink", "oleLink"])
def test_a_workbook_linked_to_another_program_is_refused(element: str) -> None:
    """Excel's DDE and OLE links start or reach another program when the workbook opens."""
    link = (
        '<?xml version="1.0" encoding="UTF-8"?><externalLink xmlns="http://schemas.openxmlformats'
        f'.org/spreadsheetml/2006/main"><{element} ddeService="cmd" ddeTopic="/c calc"/>'
        "</externalLink>"
    ).encode()
    assert refusal(make_xlsx(extra={"xl/externalLinks/externalLink1.xml": link})) == "dde"


# ---------------------------------------------------------------- ways round those checks


def nested(before: str, inner: str, after: str) -> str:
    """A field whose instruction has another field inside it, as Word writes { A { B } C }."""
    return (
        '<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        f'<w:r><w:instrText xml:space="preserve">{before}</w:instrText></w:r>'
        '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        f'<w:r><w:instrText xml:space="preserve">{inner}</w:instrText></w:r>'
        '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>x</w:t></w:r>'
        '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
        f'<w:r><w:instrText xml:space="preserve">{after}</w:instrText></w:r>'
        '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>y</w:t></w:r>'
        '<w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>'
    )


@pytest.mark.parametrize(
    ("body", "code"),
    [
        # The address comes from a nested field, so the outer field cannot be checked.
        (nested(' INCLUDEPICTURE "', ' QUOTE "https://tracker.example/p.png" ', '" '), "external"),
        # The keyword itself is built by a nested field.
        (nested(" ", ' QUOTE "INCLUDEPICTURE" ', ' "https://tracker.example/p.png" '), "field"),
        (nested(" INCLUDE", ' QUOTE "PICTURE" ', ' "https://tracker.example/p.png" '), "field"),
    ],
)
def test_a_field_built_from_other_fields_cannot_hide_what_it_does(body: str, code: str) -> None:
    assert refusal(make_docx(body=body)) == code


def test_a_mail_merge_if_field_is_fine() -> None:
    body = nested(" IF ", " MERGEFIELD Name ", ' = "Ruth" "Dear Ruth" "Dear investor" ')
    assert ooxml.inspect(make_docx(body=body)) == {"format": "docx"}


def test_an_unquoted_network_path_is_not_mistaken_for_a_switch() -> None:
    body = field(" INCLUDEPICTURE \\\\\\\\fileserver\\\\share\\\\p.png \\d ")
    assert refusal(make_docx(body=body)) == "external"


def test_fields_are_found_in_any_part_whatever_its_folder() -> None:
    """Office finds parts by relationship, not folder, so every XML part is read."""
    header = (
        f'<?xml version="1.0"?><w:hdr xmlns:w="http://schemas.openxmlformats.org/'
        f'wordprocessingml/2006/main">{field(" DDEAUTO cmd /c calc ")}</w:hdr>'
    ).encode()
    data = make_docx(
        document_rels=(
            relationship(
                "rId9",
                "http://schemas.openxmlformats.org/officeDocument/2006/relationships/header",
                "../elsewhere/header.xml",
            ),
        ),
        extra={"elsewhere/header.xml": header},
    )
    assert refusal(data) == "dde"


def test_an_excel_link_to_another_program_is_found_wherever_it_is() -> None:
    link = (
        b'<?xml version="1.0"?><externalLink xmlns="http://schemas.openxmlformats.org/'
        b'spreadsheetml/2006/main"><ddeLink ddeService="cmd" ddeTopic="/c calc"/></externalLink>'
    )
    assert refusal(make_xlsx(extra={"xl/elsewhere/link.xml": link})) == "dde"


def test_a_hyperlink_cannot_be_borrowed_through_another_namespace() -> None:
    link = relationship("rId7", REL_HYPERLINK, "https://tracker.example/pixel.png", external=True)
    picture = (
        '<p:spTree><p:pic><p:blipFill><a:blip xmlns:x="urn:example" x:link="rId7"/>'
        "</p:blipFill></p:pic></p:spTree>"
    )
    assert refusal(make_pptx(slide_rels=(link,), slide_body=picture)) == "external"
