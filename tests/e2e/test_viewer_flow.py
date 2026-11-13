"""The investor's whole journey, in a real browser, as a person would take it."""

import re
from typing import Any

import pytest
from django.core import mail as django_mail
from playwright.sync_api import Page, Request, expect

from snug.documents import ingest
from snug.documents.models import Document
from snug.links import service
from tests.pdfs import chunks, make_pdf

pytestmark = [pytest.mark.django_db(transaction=True)]

INVESTOR = "priya@northfield.example"


@pytest.fixture
def document(data_folder: Any) -> Document:
    deck = Document.objects.create(title="Seed deck")
    ingest.ingest(
        deck,
        chunks(make_pdf((960, 540), (960, 540), (595, 842))),
        filename="d.pdf",
        uploaded_by=None,
    )
    return deck


def issue(document: Document, *, verify: bool) -> service.Issued:
    return service.create(
        document=document,
        email=INVESTOR,
        note="",
        expires_at=None,
        require_verification=verify,
        allow_download=True,
        created_by=None,
    )


def expect_page(page: Page, number: int, of: int) -> None:
    image = page.get_by_role("img", name=f"Page {number} of {of}")
    expect(image).to_be_visible()
    # Loaded, not just present: decode() waits for the watermarked image to
    # arrive, and fails if it is broken.
    assert image.evaluate("img => img.decode().then(() => img.naturalWidth)") > 0


@pytest.mark.usefixtures("email_on")
def test_verifying_by_email_then_reading(live_server: Any, page: Page, document: Document) -> None:
    issued = issue(document, verify=True)
    page.goto(f"{live_server.url}/v/{issued.token}")
    expect(page.get_by_role("heading", level=1)).to_contain_text("Seed deck")

    page.get_by_label("Email address").fill("priya@northfeld.example")
    page.get_by_role("button", name="Send code").click()
    expect(page.get_by_role("alert")).to_contain_text("not the address this link was sent to")

    page.get_by_label("Email address").fill(INVESTOR)
    page.get_by_role("button", name="Send code").click()
    expect(page.get_by_role("heading", name="Check your email")).to_be_visible()
    code = re.search(r"\d{6}", str(django_mail.outbox[-1].subject))
    assert code

    page.get_by_label("Six-digit code").fill("000000" if code.group(0) != "000000" else "111111")
    page.get_by_role("button", name="Continue").click()
    expect(page.get_by_role("alert")).to_contain_text("4 tries left")

    page.get_by_label("Six-digit code").fill(code.group(0))
    page.get_by_role("button", name="Continue").click()
    expect_page(page, 1, 3)

    # The arrow keys, the Next link and the page picker all move through it.
    page.keyboard.press("ArrowRight")
    expect_page(page, 2, 3)
    page.get_by_role("link", name="Next").click()
    expect_page(page, 3, 3)
    page.get_by_label("Go to page").select_option("1")
    expect_page(page, 1, 3)
    page.keyboard.press("ArrowLeft")  # nothing before page 1
    expect_page(page, 1, 3)

    # Revoking ends the open session at once.
    service.revoke(issued.link, by=None)
    page.reload()
    expect(page.get_by_role("heading", name="This link is not available")).to_be_visible()


def test_a_link_without_verification_opens_with_one_click(
    live_server: Any, page: Page, document: Document
) -> None:
    issued = issue(document, verify=False)
    page.goto(f"{live_server.url}/v/{issued.token}")
    page.get_by_role("button", name="Open Seed deck").click()
    expect_page(page, 1, 3)
    with page.expect_download() as download:
        page.get_by_role("link", name="Download").click()
    assert download.value.suggested_filename == "Seed deck.pdf"


def test_the_theme_toggle_works_without_inline_script(
    live_server: Any, page: Page, document: Document
) -> None:
    issued = issue(document, verify=False)
    page.goto(f"{live_server.url}/v/{issued.token}")
    toggle = page.locator(".theme-toggle")
    before = toggle.get_attribute("aria-label")
    toggle.click()
    expect(toggle).not_to_have_attribute("aria-label", before or "")


def test_reading_time_reaches_the_server(live_server: Any, page: Page, document: Document) -> None:
    from snug.analytics.models import PageView

    issued = issue(document, verify=False)
    page.clock.install()
    page.goto(f"{live_server.url}/v/{issued.token}")
    page.get_by_role("button", name="Open Seed deck").click()
    expect_page(page, 1, 3)
    # Fast-forward the page's own clock past one heartbeat; the beacon goes
    # through the CSP and CSRF checks like any real one.
    with page.expect_request(lambda request: request.url.endswith("/beat")) as sent:
        page.clock.run_for(16_000)
    assert sent.value.method == "POST"
    response = sent.value.response()
    assert response is not None and response.status == 204
    assert PageView.objects.get(page=1).seconds > 0


def test_the_next_page_is_fetched_once_this_one_has_arrived(
    live_server: Any, page: Page, document: Document
) -> None:
    # On a one-CPU server, fetching both at once made page 1 take 11 s (12 November 2026).
    issued = issue(document, verify=False)
    events: list[str] = []

    def arrived(request: Request) -> None:
        if "/pages/1-" in request.url:
            events.append("page 1 arrived")

    def asked(request: Request) -> None:
        if "/pages/2-" in request.url:
            events.append("page 2 asked for")

    page.on("requestfinished", arrived)
    page.on("request", asked)
    page.goto(f"{live_server.url}/v/{issued.token}")
    page.get_by_role("button", name="Open Seed deck").click()
    expect_page(page, 1, 3)
    page.wait_for_function("() => document.querySelector('link[rel=prefetch]') !== null")
    page.wait_for_timeout(500)
    assert events[:2] == ["page 1 arrived", "page 2 asked for"], events


def test_the_loading_note_is_in_view_on_a_tall_page(
    live_server: Any, page: Page, data_folder: Any
) -> None:
    # On a portrait page the frame is taller than the window, and a note at its
    # middle was out of sight (12 November 2026).
    tall = Document.objects.create(title="Board pack")
    ingest.ingest(tall, chunks(make_pdf((595, 842))), filename="b.pdf", uploaded_by=None)
    issued = issue(tall, verify=False)
    page.set_viewport_size({"width": 1280, "height": 700})
    page.goto(f"{live_server.url}/v/{issued.token}")
    page.get_by_role("button", name="Open Board pack").click()
    expect_page(page, 1, 1)
    image = page.get_by_role("img", name="Page 1 of 1")
    image.evaluate("img => { img.style.visibility = 'hidden' }")  # as before it arrives
    note = page.locator(".page-loading")
    expect(note).to_have_text("Loading page 1")
    box = note.bounding_box()
    assert box is not None and box["y"] + box["height"] < 700, box


def test_a_rooms_folders_read_as_folders(live_server: Any, page: Page, document: Document) -> None:
    import os

    from snug.rooms.models import Folder, Room, RoomDocument

    room = Room.objects.create(name="Seed round")
    memo = Document.objects.create(title="Board memo")
    ingest.ingest(memo, chunks(make_pdf((595, 842))), filename="m.pdf", uploaded_by=None)
    RoomDocument.objects.create(room=room, document=memo)
    RoomDocument.objects.create(
        room=room, document=document, folder=Folder.objects.create(room=room, name="Decks")
    )
    issued = service.create(
        room=room,
        email=INVESTOR,
        note="",
        expires_at=None,
        require_verification=False,
        allow_download=True,
        created_by=None,
    )
    page.goto(f"{live_server.url}/v/{issued.token}")
    page.get_by_role("button", name="Open Seed round").click()
    folder = page.get_by_role("heading", name="Decks")
    expect(folder).to_contain_text("1 document")
    if shots := os.environ.get("SNUG_SHOT_DIR"):
        page.locator(".room-index").screenshot(path=f"{shots}/room-folders.png")
    page.get_by_role("link", name="Seed deck").click()
    expect_page(page, 1, 3)


def test_about_this_link_opens_from_the_top_right(
    live_server: Any, page: Page, document: Document
) -> None:
    # Decided 13 November 2026: the panel drops down from a button beside
    # Download, and Previous and Next sit above the page.
    import os

    shots = os.environ.get("SNUG_SHOT_DIR")
    issued = issue(document, verify=False)
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(f"{live_server.url}/v/{issued.token}")
    page.get_by_role("button", name="Open Seed deck").click()
    expect_page(page, 1, 3)
    toggle = page.locator("summary", has_text="About this link")
    panel = page.get_by_role("complementary", name="About this link")
    expect(panel).to_be_hidden()
    controls = page.get_by_role("navigation", name="Pages").bounding_box()
    image = page.get_by_role("img", name="Page 1 of 3").bounding_box()
    assert controls is not None and image is not None and controls["y"] < image["y"]
    if shots:
        page.screenshot(path=f"{shots}/reader-closed.png")
    toggle.click()
    expect(panel).to_be_visible()
    fits_on_screen(page, panel)
    button, box = toggle.bounding_box(), panel.bounding_box()
    assert button is not None and box is not None
    assert box["x"] + box["width"] >= button["x"] + button["width"]  # at the right, under it
    if shots:
        page.screenshot(path=f"{shots}/reader-open.png")
    page.get_by_role("img", name="Page 1 of 3").click()  # anywhere else closes it
    expect(panel).to_be_hidden()
    toggle.click()
    expect(panel).to_be_visible()
    page.keyboard.press("Escape")
    expect(panel).to_be_hidden()
    expect(toggle).to_be_focused()
    page.get_by_role("link", name="Next").click()
    expect_page(page, 2, 3)
    page.set_viewport_size({"width": 390, "height": 800})
    words = page.locator(".about-toggle .label").bounding_box()  # kept for screen readers only
    assert words is not None and words["width"] <= 1
    expect(toggle).to_be_visible()  # the icon and caret
    toggle.click()
    expect(panel).to_be_visible()
    fits_on_screen(page, panel)
    if shots:
        page.screenshot(path=f"{shots}/reader-phone.png")


def fits_on_screen(page: Page, panel: Any) -> None:
    box, width = panel.bounding_box(), page.viewport_size
    assert box is not None and width is not None
    assert box["x"] >= 0 and box["x"] + box["width"] <= width["width"], (box, width)
