"""Admin pages in a real browser: what only a click can prove under the CSP."""

import re
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
    page.get_by_label("PDF").set_input_files(
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
    page.get_by_label("PDF").set_input_files(
        files=FilePayload(name="deck.pdf", mimeType="application/pdf", buffer=make_pdf((960, 540)))
    )
    page.get_by_role("button", name="Upload").click()  # no title
    expect(page.locator(".notice-error")).to_contain_text("Give the document a title.")
    expect(page.get_by_role("button", name="Upload")).to_be_enabled()


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
