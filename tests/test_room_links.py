"""Sharing a room: one link, every document in the room, and nothing else.

authorise() check 5: the requested document belongs to the link's document
or room. These tests walk it for room links, including a document outside
the room, one removed from the room while a session is open, and a document
link's session used to reach a room's documents.
"""

from typing import Any

import pytest
from django.test import Client

from snug.documents.models import Document, DocumentVersion
from snug.links import service
from snug.links.models import Link
from snug.rooms.models import Folder, Room, RoomDocument
from snug.viewer.access import NotVerified, Unavailable, authorise
from tests.test_viewer_access import verified_browser

pytestmark = pytest.mark.django_db

INVESTOR = "priya@northfield.example"


def deck(title: str) -> Document:
    document = Document.objects.create(title=title)
    DocumentVersion.objects.create(
        document=document,
        number=1,
        size_bytes=1,
        sha256="0" * 64,
        page_count=2,
        page_sizes=[[960, 540]] * 2,
    )
    return document


@pytest.fixture
def room() -> Room:
    room = Room.objects.create(name="Seed round")
    pitch = Folder.objects.create(room=room, name="Pitch")
    RoomDocument.objects.create(room=room, document=deck("Seed deck"), folder=pitch)
    RoomDocument.objects.create(room=room, document=deck("Cap table"))
    return room


@pytest.fixture
def outside() -> Document:
    return deck("Board minutes")


def share(**target: Any) -> service.Issued:
    return service.create(
        email=INVESTOR,
        note="",
        expires_at=None,
        require_verification=False,
        allow_download=True,
        created_by=None,
        **target,
    )


def member(room: Room, title: str) -> Document:
    return room.entries.get(document__title=title).document


def opened(issued: service.Issued) -> Client:
    browser = Client()
    browser.post(f"/v/{issued.token}/open", secure=True)
    return browser


# ---------------------------------------------------------------- the model


def test_a_link_points_at_a_document_or_a_room_never_both(room: Room, outside: Document) -> None:
    from django.db import IntegrityError, transaction

    issued = share(room=room)
    assert issued.link.room == room and issued.link.document is None
    assert issued.link.target_title == "Seed round"
    with pytest.raises(ValueError):
        share(room=room, document=outside)
    with pytest.raises(ValueError):
        share()
    with pytest.raises(IntegrityError), transaction.atomic():
        Link.objects.filter(pk=issued.link.pk).update(document=outside)


# ---------------------------------------------------------------- authorise(), check 5


def test_a_room_link_reaches_every_document_in_the_room(room: Room) -> None:
    issued = share(room=room)
    request = verified_browser(issued.link)
    assert authorise(request, issued.token).document is None  # the room index
    for title in ("Seed deck", "Cap table"):
        document = member(room, title)
        assert authorise(request, issued.token, document_token=document.token).document == document


def test_a_document_outside_the_room_is_unavailable(room: Room, outside: Document) -> None:
    issued = share(room=room)
    request = verified_browser(issued.link)
    with pytest.raises(Unavailable):
        authorise(request, issued.token, document_token=outside.token)


def test_a_document_in_another_room_is_unavailable(room: Room, outside: Document) -> None:
    other = Room.objects.create(name="Series A")
    RoomDocument.objects.create(room=other, document=outside)
    issued = share(room=room)
    with pytest.raises(Unavailable):
        authorise(verified_browser(issued.link), issued.token, document_token=outside.token)


def test_removing_a_document_from_the_room_ends_access_at_once(room: Room) -> None:
    issued = share(room=room)
    request = verified_browser(issued.link)
    document = member(room, "Cap table")
    assert authorise(request, issued.token, document_token=document.token).document == document
    RoomDocument.objects.filter(room=room, document=document).delete()
    with pytest.raises(Unavailable):
        authorise(request, issued.token, document_token=document.token)


def test_a_document_links_session_does_not_open_a_room(room: Room) -> None:
    document = member(room, "Seed deck")
    single = share(document=document)
    to_room = share(room=room)
    request = verified_browser(single.link)
    with pytest.raises(NotVerified):
        authorise(request, to_room.token)
    # And a document link reaches only its own document, whatever the room holds.
    other = member(room, "Cap table")
    with pytest.raises(Unavailable):
        authorise(request, single.token, document_token=other.token)


def test_unverified_requests_to_a_room_document_must_verify(room: Room) -> None:
    issued = share(room=room)
    with pytest.raises(NotVerified):
        authorise(
            Client().request().wsgi_request,
            issued.token,
            document_token=member(room, "Seed deck").token,
        )


# ---------------------------------------------------------------- the viewer's pages


def test_the_room_index_lists_the_room_by_folder(room: Room, outside: Document) -> None:
    issued = share(room=room)
    page = opened(issued).get(f"/v/{issued.token}", secure=True).content.decode()
    assert "<h1>Seed round</h1>" in page
    assert page.index("Cap table") < page.index("Pitch") < page.index("Seed deck")
    assert "Board minutes" not in page
    seed = member(room, "Seed deck")
    assert f'href="/v/{issued.token}/d/{seed.token}"' in page


def test_a_folder_reads_as_a_folder_with_its_documents_inside(room: Room) -> None:
    # Found 12 November 2026: folder names were quieter than document titles.
    issued = share(room=room)
    page = opened(issued).get(f"/v/{issued.token}", secure=True).content.decode()
    heading = page[page.index('<h2 class="room-folder">') :].split("</h2>", 1)[0]
    assert 'class="icon"' in heading and "Pitch" in heading
    assert '<span class="count">1 document</span>' in heading
    after = page[page.index('<h2 class="room-folder">') :]
    assert after.index('<ul class="room-documents in-folder">') < after.index("Seed deck")


def test_a_room_document_opens_in_the_reader(room: Room) -> None:
    issued = share(room=room)
    seed = member(room, "Seed deck")
    browser = opened(issued)
    page = browser.get(f"/v/{issued.token}/d/{seed.token}?page=2", secure=True).content.decode()
    assert 'alt="Page 2 of 2"' in page
    assert f'src="/v/{issued.token}/d/{seed.token}/pages/2-1600.webp"' in page
    assert f'href="/v/{issued.token}/d/{seed.token}?page=1"' in page
    assert f'href="/v/{issued.token}"' in page  # back to the room
    assert f'href="/v/{issued.token}/d/{seed.token}/download"' in page


@pytest.mark.parametrize("suffix", ["", "/pages/1-800.webp", "/download"])
def test_nothing_outside_the_room_is_served(room: Room, outside: Document, suffix: str) -> None:
    issued = share(room=room)
    response = opened(issued).get(f"/v/{issued.token}/d/{outside.token}{suffix}", secure=True)
    assert response.status_code == 404
    assert "Board minutes" not in response.content.decode(errors="replace")


def test_the_landing_page_names_the_room(room: Room) -> None:
    issued = share(room=room)
    page = Client().get(f"/v/{issued.token}", secure=True).content.decode()
    assert "<strong>Seed round</strong>" in page and "2 documents" in page
    assert "Open Seed round" in page


def test_a_rooms_privacy_line_does_not_assume_every_document_has_pages(room: Room) -> None:
    """A room can hold workbooks and videos beside PDFs."""
    issued = share(room=room)
    page = Client().get(f"/v/{issued.token}", secure=True).content.decode()
    assert (
        "when you open each document, how long you spend on it, and when you download it." in page
    )
    assert "Every page and video shows your email address as a watermark." in page


def test_an_empty_room_says_so() -> None:
    issued = share(room=Room.objects.create(name="Empty"))
    page = opened(issued).get(f"/v/{issued.token}", secure=True).content.decode()
    assert "Nothing has been added to this room yet" in page


# ---------------------------------------------------------------- the admin


def test_a_room_link_is_created_from_the_room(room: Room) -> None:
    import time

    from snug.accounts import sessions
    from snug.accounts.models import Admin

    admin = Admin.objects.create(email="ruth@fernlea.example", is_active=True)
    browser = Client()
    browser.force_login(admin)
    session = browser.session
    session[sessions.LOGIN_AT] = session[sessions.LAST_ACTIVITY] = time.time()
    session.save()
    page = browser.get(f"/admin/rooms/{room.token}", secure=True).content.decode()
    assert f'href="/admin/links/for-room/{room.token}"' in page
    response = browser.post(f"/admin/links/for-room/{room.token}", {"email": INVESTOR}, secure=True)
    assert response.status_code == 302
    link = Link.objects.get()
    assert link.room == room and link.document is None
    listing = browser.get("/admin/links/", secure=True).content.decode()
    assert "Seed round" in listing
    detail = browser.get(f"/admin/rooms/{room.token}", secure=True).content.decode()
    assert INVESTOR in detail


def test_a_room_download_records_which_document(room: Room, data_folder: Any) -> None:
    from snug.audit.models import AuditEvent
    from snug.documents import ingest
    from tests.pdfs import chunks, make_pdf

    pdf_deck = Document.objects.create(title="Term sheet")
    ingest.ingest(pdf_deck, chunks(make_pdf((595, 842))), filename="t.pdf", uploaded_by=None)
    RoomDocument.objects.create(room=room, document=pdf_deck)
    issued = share(room=room)
    response = opened(issued).get(f"/v/{issued.token}/d/{pdf_deck.token}/download", secure=True)
    assert response.status_code == 200
    event = AuditEvent.objects.get(action="viewer.downloaded")
    assert event.details["room"] == room.token and event.details["document"] == pdf_deck.token


def test_the_issued_page_for_a_room_link_points_to_the_rooms_page(
    room: Room, admin_browser: Client
) -> None:
    # Found in the wording pass, 13 November 2026: it said "the document's page".
    data = {"email": "ana@northfield.example", "note": "", "action": "copy"}
    admin_browser.post(f"/admin/links/for-room/{room.token}", data, secure=True)
    page = admin_browser.get("/admin/links/issued", secure=True).content.decode()
    assert "send a new link from the room's page" in page
    assert "the document's page" not in page
