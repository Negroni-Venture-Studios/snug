"""Admin pages in a real browser: what only a click can prove under the CSP."""

import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import FilePayload, Page, expect

pytestmark = [pytest.mark.django_db(transaction=True)]


def test_the_eye_shows_and_hides_the_password(live_server: Any, page: Page) -> None:
    page.goto(f"{live_server.url}/admin/login")
    field = page.get_by_label("Password", exact=True)
    eye = page.get_by_role("button", name="Show password")
    expect(eye).to_be_visible()  # the script ran, so the button appeared
    field.fill("correct horse battery staple")
    expect(field).to_have_attribute("type", "password")

    eye.click()
    expect(field).to_have_attribute("type", "text")
    hide = page.get_by_role("button", name="Hide password")
    expect(hide).to_have_attribute("aria-pressed", "true")
    expect(field).to_be_focused()

    hide.click()
    expect(field).to_have_attribute("type", "password")
    expect(page.get_by_role("button", name="Show password")).to_have_attribute(
        "aria-pressed", "false"
    )


# ---------------------------------------------------------------- writing the NDA in place

EMAIL = "ruth@fernlea.example"
PASSWORD = "correct horse battery staple"
NDA = "The recipient will keep everything in this data room confidential."


def sign_in(live_server: Any, page: Page) -> None:
    import pyotp
    from django.utils import timezone

    from snug.accounts import totp
    from snug.accounts.models import Admin

    admin = Admin(
        email=EMAIL, is_active=True, totp_secret=totp.new_secret(), totp_confirmed_at=timezone.now()
    )
    admin.set_password(PASSWORD)
    admin.save()
    page.goto(f"{live_server.url}/admin/login")
    page.get_by_label("Email address").fill(EMAIL)
    page.get_by_label("Password", exact=True).fill(PASSWORD)
    page.get_by_role("button", name="Continue").click()
    page.get_by_label("Six-digit code").fill(pyotp.TOTP(admin.totp_secret).now())
    page.get_by_role("button", name="Sign in").click()
    expect(page).to_have_url(f"{live_server.url}/admin/")


def test_writing_the_nda_on_a_room_with_none_yet(live_server: Any, page: Page) -> None:
    from snug.rooms.models import Room

    room = Room.objects.create(name="Seed round")
    sign_in(live_server, page)
    page.goto(f"{live_server.url}/admin/rooms/{room.token}")
    box = page.get_by_label("The NDA", exact=True)
    expect(box).to_be_hidden()  # until the option is ticked
    page.get_by_text("Ask for the NDA before anything is shown").click()
    expect(box).to_be_visible()
    expect(box).to_be_focused()
    box.fill(NDA)
    page.locator("#nda").get_by_role("button", name="Save").click()
    expect(page.locator("#nda .notice-ok")).to_contain_text(
        "Every link to this room asks for the NDA, version 1"
    )
    expect(page.locator(".page-meta")).to_contain_text("Asks for the NDA, version 1")
    expect(page.get_by_label("The NDA", exact=True)).to_have_count(0)  # it exists now


def test_the_link_form_shows_the_box_only_when_asked(live_server: Any, page: Page) -> None:
    from snug.documents.models import Document

    deck = Document.objects.create(title="Seed deck")
    sign_in(live_server, page)
    page.goto(f"{live_server.url}/admin/links/for/{deck.token}")
    box = page.get_by_label("The NDA", exact=True)
    expect(box).to_be_hidden()
    page.get_by_text("Ask for the NDA first").click()
    expect(box).to_be_visible()
    page.get_by_text("Ask for the NDA first").click()
    expect(box).to_be_hidden()


# ---------------------------------------------------------------- upload progress


def test_an_upload_shows_its_progress_and_cannot_be_sent_twice(
    live_server: Any, page: Page, data_folder: Any
) -> None:
    import time

    from tests.pdfs import make_pdf

    sign_in(live_server, page)
    page.goto(f"{live_server.url}/admin/documents/new")
    page.get_by_label("Title").fill("Seed deck")
    page.get_by_label("File").set_input_files(
        files=FilePayload(
            name="deck.pdf", mimeType="application/pdf", buffer=make_pdf((960, 540), (595, 842))
        )
    )

    def slowly(route: Any) -> None:
        time.sleep(1.5)  # long enough to see the form while it waits
        route.continue_()

    page.route("**/admin/documents/new", slowly)
    button = page.get_by_role("button", name="Upload")
    button.click()
    expect(page.locator("[data-upload-status]")).to_be_visible()
    expect(button).to_be_disabled()
    # The server's reply replaces the page, with its notice in view.
    notice = page.locator(".notice-ok")
    expect(notice).to_contain_text("Uploaded, 2 pages", timeout=15000)
    expect(notice).to_be_in_viewport()
    expect(page).to_have_url(re.compile(r"/admin/documents/[A-Za-z0-9_-]+#links$"))


def test_a_refused_upload_shows_its_error(live_server: Any, page: Page, data_folder: Any) -> None:
    from tests.pdfs import make_pdf

    sign_in(live_server, page)
    page.goto(f"{live_server.url}/admin/documents/new")
    page.locator("header").evaluate("header => header.setAttribute('data-kept', 'yes')")
    deck = FilePayload(name="deck.pdf", mimeType="application/pdf", buffer=make_pdf((960, 540)))
    page.get_by_label("File").set_input_files(files=deck)
    page.get_by_role("button", name="Upload").click()  # no title
    expect(page.locator(".notice-error")).to_contain_text("Give the document a title.")
    expect(page.get_by_role("button", name="Upload")).to_be_enabled()
    # Only the page's main part was replaced: the header is the one it had.
    expect(page.locator("header[data-kept='yes']")).to_have_count(1)
    expect(page).to_have_title("Upload a document to Snug")

    # The form that came back is sent by the script too, and this time it works.
    page.get_by_label("Title").fill("Seed deck")
    page.get_by_label("File").set_input_files(files=deck)
    with page.expect_request("**/admin/documents/new") as sent:
        page.get_by_role("button", name="Upload").click()
    assert sent.value.headers.get("x-snug-upload") == "1"
    expect(page.locator(".notice-ok")).to_contain_text("Uploaded, 1 page", timeout=15000)


def test_a_refused_version_shows_its_error_in_view(
    live_server: Any, page: Page, data_folder: Any
) -> None:
    from snug.documents import ingest
    from snug.documents.models import Document
    from tests.pdfs import chunks, make_pdf

    deck = Document.objects.create(title="Seed deck")
    ingest.ingest(deck, chunks(make_pdf((960, 540))), filename="deck.pdf", uploaded_by=None)
    sign_in(live_server, page)
    page.goto(f"{live_server.url}/admin/documents/{deck.token}")
    page.locator("header").evaluate("header => header.setAttribute('data-kept', 'yes')")
    page.get_by_label("File").set_input_files(
        files=FilePayload(name="notes.txt", mimeType="text/plain", buffer=b"Not a PDF at all")
    )
    page.get_by_role("button", name="Upload version").click()
    error = page.locator(".notice-error")
    expect(error).to_contain_text("Snug takes PDFs, Excel workbooks (.xlsx) and MP4 videos")
    expect(error).to_be_in_viewport()
    expect(page.locator("header[data-kept='yes']")).to_have_count(1)


def test_admin_pages_fit_a_phone(live_server: Any, page: Page) -> None:
    """At phone width nothing pushes the page sideways (the tabs scroll in their own strip)."""
    from datetime import timedelta

    from snug.documents.models import Document
    from snug.rooms.models import Room, RoomDocument
    from tests.test_overview import make_link, visit

    deck = Document.objects.create(title="Seed deck with a long title for the narrow screen")
    room = Room.objects.create(name="Series A room")
    RoomDocument.objects.create(room=room, document=deck)
    link = make_link("priya.someone-long@northfield-capital.example", room=room)
    visit(link, deck, ago=timedelta(hours=1), seconds=95)
    sign_in(live_server, page)
    for path in ("", "documents/", "rooms/", "links/", "people/", "audit/", "settings/"):
        page.goto(f"{live_server.url}/admin/{path}")
        wide = page.evaluate("document.documentElement.scrollWidth")
        assert wide <= page.viewport_size["width"], (path, wide)  # type: ignore[index]


def test_the_admin_rows_share_their_columns(live_server: Any, page: Page) -> None:
    """Your own row has no buttons, but its status and date line up with the rows that do."""
    from django.utils import timezone

    from snug.accounts.models import Admin

    sign_in(live_server, page)
    Admin.objects.create(email="b@x.example", is_active=True, totp_confirmed_at=timezone.now())
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{live_server.url}/admin/settings/")
    rows = page.locator(".admin-rows .item")
    expect(rows).to_have_count(2)
    for part in (".status", ".num"):
        lefts = {round(rows.nth(i).locator(part).bounding_box()["x"]) for i in range(2)}  # type: ignore[index]
        assert len(lefts) == 1, (part, lefts)


def test_email_is_set_up_in_steps_with_the_next_one_in_view(live_server: Any, page: Page) -> None:
    """Saving the Google client lands on step 2, with Connect Google on screen."""
    import pyotp

    from snug.accounts.models import Admin

    sign_in(live_server, page)
    page.goto(f"{live_server.url}/admin/settings/#email")
    mailbox = page.locator("#google-mailbox")
    expect(mailbox).to_be_hidden()  # SMTP is chosen until Google is
    page.get_by_label("Google Workspace").check()
    expect(mailbox).to_be_visible()
    expect(mailbox).to_contain_text("Save your Google client first.")
    expect(page.get_by_text("Your SMTP server")).to_be_hidden()

    page.get_by_label("Client ID").fill("123456789-abcdef.apps.googleusercontent.com")
    page.get_by_label("Client secret").fill("GOCSPX-e2e")
    admin = Admin.objects.get(email=EMAIL)
    Admin.objects.filter(pk=admin.pk).update(totp_last_step=0)
    page.locator("#email-code").fill(pyotp.TOTP(admin.totp_secret).now())
    page.locator("#email-setup").get_by_role("button", name="Save").click()

    expect(page).to_have_url(re.compile(r"#google-mailbox$"))
    expect(mailbox).to_contain_text("Saved your Google client. Now connect the mailbox")
    expect(page.get_by_role("link", name="Connect Google")).to_be_in_viewport()

    page.get_by_label("Another provider (SMTP)").check()
    expect(mailbox).to_be_hidden()
    # Email is on here (the outbox), so the test is ready for SMTP but waits for a Google mailbox.
    test = page.locator("#email-test")
    expect(test.get_by_role("button", name="Send a test")).to_be_visible()
    page.get_by_label("Google Workspace").check()
    expect(test.get_by_text("Connect a mailbox first.")).to_be_visible()
    expect(test.get_by_role("button", name="Send a test")).to_be_hidden()


def test_email_settings_can_be_cleared_from_beside_the_status(live_server: Any, page: Page) -> None:
    """The answer to 'how do I stop it sending like this?' is under the sentence that says so."""
    import pyotp

    from snug.accounts.models import Admin
    from snug.core.models import EmailSettings

    sign_in(live_server, page)
    row = EmailSettings.load()
    row.provider, row.from_address, row.smtp_host, row.smtp_port = (
        "smtp",
        "rooms@fernlea.example",
        "smtp.postmark.example",
        2525,
    )
    row.save()
    page.goto(f"{live_server.url}/admin/settings/#email")
    clear = page.get_by_role("button", name="Clear email settings")
    expect(clear).to_be_visible()  # its own panel, not folded away

    admin = Admin.objects.get(email=EMAIL)
    Admin.objects.filter(pk=admin.pk).update(totp_last_step=0)
    page.locator("#email-clear-code").fill(pyotp.TOTP(admin.totp_secret).now())
    clear.click()
    expect(page).to_have_url(re.compile(r"saved=email-cleared#email$"))
    expect(page.locator("#email .notice-ok").first).to_contain_text("Cleared.")
    expect(page.locator("#email-clear")).to_have_count(0)
    assert EmailSettings.load().smtp_host == ""


def test_a_workbook_is_uploaded_shared_and_downloaded(
    live_server: Any, page: Page, browser: Any, data_folder: Any
) -> None:
    """The whole journey for a spreadsheet, with real clicks: upload, share, download."""
    import io
    import zipfile

    from snug.documents.models import Document
    from snug.links import service
    from snug.rendering import workbook
    from tests.workbooks import make_xlsx

    sign_in(live_server, page)
    page.goto(f"{live_server.url}/admin/documents/new")
    page.get_by_label("Title").fill("Financial model")
    page.get_by_label("File").set_input_files(
        files=FilePayload(
            name="Model.xlsx",
            mimeType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            buffer=make_xlsx((("Summary", "visible"), ("Detail", "visible"))),
        )
    )
    page.get_by_role("button", name="Upload").click()
    expect(page.locator(".notice-ok")).to_contain_text(
        "Uploaded, an Excel workbook with 2 sheets", timeout=15000
    )
    expect(page.locator(".sheet-list")).to_contain_text("Summary")
    page.get_by_label("What investors should know").fill("Start on Summary.")
    page.get_by_role("button", name="Save description").click()
    expect(page.locator("#description .notice-ok")).to_contain_text("Description saved.")
    expect(page).to_have_url(re.compile(r"#description$"))

    issued = service.create(
        document=Document.objects.get(title="Financial model"),
        email="priya@northfield.example",
        note="",
        expires_at=None,
        require_verification=False,
        allow_download=True,
        created_by=None,
    )
    investor = browser.new_page()
    investor.goto(f"{live_server.url}/v/{issued.token}")
    investor.get_by_role("button", name="Open Financial model").click()
    expect(investor.locator(".sheet-list")).to_contain_text("Detail")
    expect(investor.locator(".description")).to_have_text("Start on Summary.")
    with investor.expect_download() as arriving:
        investor.get_by_role("link", name="Download the workbook").click()
    download = arriving.value
    assert download.suggested_filename == "Financial model.xlsx"
    data = Path(download.path()).read_bytes()
    with zipfile.ZipFile(io.BytesIO(data)) as package:
        properties = workbook._parse(package.read("docProps/custom.xml"))
    names = {p.get("name"): "".join(p.itertext()) for p in properties}
    assert names["Shared with"] == "priya@northfield.example"
    investor.close()


@pytest.mark.parametrize("width", [1440, 1100])
def test_every_row_list_keeps_its_columns(
    live_server: Any, page: Page, data_folder: Any, width: int
) -> None:
    """A row without buttons (a revoked link), or with longer text, keeps the others' columns."""
    from snug.documents import ingest
    from snug.documents.models import Document
    from snug.links import service
    from snug.rooms.models import Room, RoomDocument
    from tests.pdfs import chunks, make_pdf

    deck = Document.objects.create(title="Seed deck")
    ingest.ingest(deck, chunks(make_pdf((960, 540))), filename="deck.pdf", uploaded_by=None)
    many = make_pdf(*[(960, 540)] * 12)
    ingest.ingest(deck, chunks(many), filename="deck v2.pdf", uploaded_by=None)
    fields: dict[str, Any] = {
        "note": "",
        "expires_at": None,
        "require_verification": True,
        "allow_download": True,
        "created_by": None,
    }
    service.create(document=deck, email="sam@eastgate.example", **fields)
    gone = service.create(document=deck, email="flow-check@northfield.example", **fields)
    service.revoke(gone.link, by=None)
    room = Room.objects.create(name="Series A data room")
    model = Document.objects.create(title="Financial model with a much longer title")
    ingest.ingest(model, chunks(make_pdf((960, 540))), filename="model.pdf", uploaded_by=None)
    RoomDocument.objects.create(room=room, document=deck)
    RoomDocument.objects.create(room=room, document=model)

    sign_in(live_server, page)
    page.set_viewport_size({"width": width, "height": 900})
    for path, rows in (
        ("links/", ".link-rows .item"),
        (f"rooms/{room.token}", ".room-rows .item"),
        (f"documents/{deck.token}", ".version-rows .item"),
    ):
        page.goto(f"{live_server.url}/admin/{path}")
        lefts = page.evaluate(
            """(selector) => [...document.querySelectorAll(selector)].map(row =>
                [...row.children].map(cell => Math.round(cell.getBoundingClientRect().x)))""",
            rows,
        )
        assert len(lefts) >= 2, (path, lefts)
        # The second and third cells (status, details or counts) start at the same x in every row.
        for index in (1, 2):
            starts = {row[index] for row in lefts if len(row) > index}
            assert len(starts) == 1, (path, index, lefts)


def test_a_video_is_uploaded_and_shown_with_its_watermark(
    live_server: Any, page: Page, browser: Any, data_folder: Any
) -> None:
    """Real clicks: upload, then the player with the mark over it and a range request.

    Playwright's Chromium has no H.264 decoder, so playback itself is checked by hand.
    """
    from snug.documents.models import Document
    from snug.links import service
    from tests.videos import CLIP

    sign_in(live_server, page)
    page.goto(f"{live_server.url}/admin/documents/new")
    page.get_by_label("Title").fill("Product demo")
    page.get_by_label("File").set_input_files(
        files=FilePayload(name="demo.mp4", mimeType="video/mp4", buffer=CLIP.read_bytes())
    )
    page.get_by_role("button", name="Upload").click()
    expect(page.locator(".notice-ok")).to_contain_text("Uploaded, an MP4 video, 0:02 long")

    issued = service.create(
        document=Document.objects.get(title="Product demo"),
        email="priya@northfield.example",
        note="",
        expires_at=None,
        require_verification=False,
        allow_download=True,
        created_by=None,
    )
    investor = browser.new_page()
    investor.goto(f"{live_server.url}/v/{issued.token}")
    investor.get_by_role("button", name="Open Product demo").click()
    video, mark = investor.locator(".video-frame video"), investor.locator(".video-mark span")
    expect(mark).to_contain_text("Shared with priya@northfield.example")
    expect(investor.locator(".video-fullscreen")).to_be_visible()  # the script ran
    box, over = video.bounding_box(), mark.bounding_box()
    assert box and over
    assert box["x"] <= over["x"] and over["x"] + over["width"] <= box["x"] + box["width"] + 1
    piece = investor.request.get(
        f"{live_server.url}/v/{issued.token}/video", headers={"Range": "bytes=0-99"}
    )
    assert piece.status == 206 and len(piece.body()) == 100
    investor.close()


def test_settings_shows_when_the_backups_last_reached_the_bucket(
    live_server: Any, page: Page, data_folder: Path
) -> None:
    copied = datetime.now(UTC) - timedelta(minutes=4)
    (data_folder / "backup").mkdir()
    (data_folder / "backup" / "last-success").write_text(
        copied.strftime("%Y-%m-%dT%H:%M:%SZ"), encoding="utf-8"
    )
    sign_in(live_server, page)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{live_server.url}/admin/settings/#backups")
    section = page.locator("#backups")
    expect(section).to_be_in_viewport()
    expect(section.locator(".status")).to_have_text("Working")
    expect(section).to_contain_text("Last copied to the bucket 4\u00a0minutes ago.")
    # As wide as the Privacy panel above it, not the whole page.
    panel = section.locator(".backup-status").bounding_box()
    above = page.locator("#privacy .panel").first.bounding_box()
    assert panel is not None and above is not None
    assert round(panel["width"]) == round(above["width"])
    guide = section.get_by_role("link", name="The backups guide")
    expect(guide).to_have_attribute("href", re.compile(r"/docs/backups\.md$"))
    expect(guide).to_have_attribute("target", "_blank")
    for name, anchor in (("branding", "branding"), ("privacy", "privacy-and-reading-data")):
        link = page.locator(f"#{name} .section-head .doc-link")
        expect(link).to_have_attribute("href", re.compile(rf"/docs/using-snug\.md#{anchor}$"))
    shots = os.environ.get("SNUG_SHOT_DIR")
    if shots:
        section.screenshot(path=f"{shots}/settings-backups.png")

    (data_folder / "backup" / "last-success").unlink()
    (data_folder / "backup" / "problem").write_text(
        "Backups are not set up. Run sudo snug setup to add a bucket.", encoding="utf-8"
    )
    page.reload()
    expect(section.locator(".status")).to_have_text("Problem")
    beside = section.locator(".backup-status").get_by_role("link", name="The backups guide")
    expect(beside).to_be_visible()
    expect(beside).to_have_attribute("target", "_blank")
    if shots:
        section.screenshot(path=f"{shots}/settings-backups-problem.png")


def test_the_overview_shows_the_version_under_the_status(
    live_server: Any, page: Page, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SNUG_BUILD_VERSION", "1.4.2")
    sign_in(live_server, page)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{live_server.url}/admin/")
    status = page.locator(".system-status")
    line = page.locator(".system-version")
    expect(line).to_contain_text("Snug 1.4.2.")
    status_box, line_box = status.bounding_box(), line.bounding_box()
    assert status_box is not None and line_box is not None
    assert line_box["y"] > status_box["y"] + status_box["height"] - 1
    guide = line.get_by_role("link", name="Updating Snug")
    expect(guide).to_have_attribute("target", "_blank")
    if shots := os.environ.get("SNUG_SHOT_DIR"):
        page.locator("main").screenshot(path=f"{shots}/overview-version.png")
