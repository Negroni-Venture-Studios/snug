"""The NDA: authorise() check 3, the acceptance page, and the admin's text.

Decided 5 November 2026: one NDA for the instance, set in Settings and
versioned; a room, or a link to a single document, requires it or not;
acceptance is per link and per version.
"""

import hashlib
import time
from typing import Any

import pytest
from django.test import Client

from snug.accounts import sessions
from snug.accounts.models import Admin
from snug.audit.models import AuditEvent
from snug.documents.models import Document, DocumentVersion
from snug.links import service
from snug.nda import terms
from snug.nda.models import NdaAcceptance, NdaVersion
from snug.rooms.models import Room, RoomDocument
from snug.viewer.access import NeedsNda, authorise
from tests.test_viewer_access import verified_browser

pytestmark = pytest.mark.django_db

INVESTOR = "priya@northfield.example"
TEXT = "The recipient will keep the contents of this room confidential."


@pytest.fixture
def nda() -> NdaVersion:
    return terms.publish(TEXT, by=None)


@pytest.fixture
def document() -> Document:
    deck = Document.objects.create(title="Seed deck")
    DocumentVersion.objects.create(
        document=deck,
        number=1,
        size_bytes=1,
        sha256="0" * 64,
        page_count=2,
        page_sizes=[[960, 540]] * 2,
    )
    return deck


@pytest.fixture
def room(document: Document) -> Room:
    room = Room.objects.create(name="Seed round", require_nda=True)
    RoomDocument.objects.create(room=room, document=document)
    return room


def share(**options: Any) -> service.Issued:
    fields: dict[str, Any] = {
        "email": INVESTOR,
        "note": "",
        "expires_at": None,
        "require_verification": False,
        "allow_download": True,
        "created_by": None,
    }
    fields.update(options)
    return service.create(**fields)


def opened(issued: service.Issued) -> Client:
    browser = Client(REMOTE_ADDR="203.0.113.9", HTTP_USER_AGENT="Investor's browser")
    browser.post(f"/v/{issued.token}/open", secure=True)
    return browser


def accept(browser: Client, issued: service.Issued, **fields: str) -> Any:
    data = {"name": "Priya Shah", "accept": "on"}
    data.update(fields)
    return browser.post(f"/v/{issued.token}/nda", data, secure=True)


# ---------------------------------------------------------------- versions


def test_publishing_versions_the_text() -> None:
    first = terms.publish(TEXT, by=None)
    assert (first.number, first.sha256) == (1, hashlib.sha256(TEXT.encode()).hexdigest())
    assert terms.publish(f"  {TEXT}  ", by=None) == first  # unchanged text, no new version
    second = terms.publish(TEXT + " For two years.", by=None)
    assert second.number == 2 and terms.current() == second


# ---------------------------------------------------------------- authorise(), check 3


@pytest.mark.usefixtures("nda")
def test_a_room_that_requires_the_nda_is_closed_until_it_is_accepted(room: Room) -> None:
    issued = share(room=room)
    request = verified_browser(issued.link)
    with pytest.raises(NeedsNda):
        authorise(request, issued.token)
    version = terms.current()
    assert version is not None
    terms.accept(
        issued.link, version, name="Priya Shah", email=INVESTOR, ip="203.0.113.9", user_agent="x"
    )
    assert authorise(request, issued.token).link == issued.link


def test_editing_the_nda_asks_for_acceptance_again(room: Room, nda: NdaVersion) -> None:
    issued = share(room=room)
    request = verified_browser(issued.link)
    terms.accept(issued.link, nda, name="Priya Shah", email=INVESTOR, ip=None, user_agent="")
    authorise(request, issued.token)
    terms.publish(TEXT + " Amended.", by=None)
    with pytest.raises(NeedsNda):
        authorise(request, issued.token)


def test_acceptance_on_one_link_does_not_count_for_another(room: Room, nda: NdaVersion) -> None:
    first = share(room=room)
    second = share(room=room)
    terms.accept(first.link, nda, name="Priya Shah", email=INVESTOR, ip=None, user_agent="")
    with pytest.raises(NeedsNda):
        authorise(verified_browser(second.link), second.token)


@pytest.mark.usefixtures("nda")
def test_turning_the_requirement_on_applies_to_open_sessions(document: Document) -> None:
    room = Room.objects.create(name="Open room")
    RoomDocument.objects.create(room=room, document=document)
    issued = share(room=room)
    request = verified_browser(issued.link)
    authorise(request, issued.token)
    Room.objects.filter(pk=room.pk).update(require_nda=True)
    with pytest.raises(NeedsNda):
        authorise(request, issued.token)


@pytest.mark.usefixtures("nda")
def test_a_document_link_can_require_the_nda(document: Document) -> None:
    issued = share(document=document, require_nda=True)
    with pytest.raises(NeedsNda):
        authorise(verified_browser(issued.link), issued.token)
    plain = share(document=document)
    assert authorise(verified_browser(plain.link), plain.token).document == document


@pytest.mark.usefixtures("nda")
def test_the_nda_comes_after_the_session_check(room: Room) -> None:
    from snug.viewer.access import NotVerified

    issued = share(room=room)
    with pytest.raises(NotVerified):
        authorise(Client().request().wsgi_request, issued.token)


# ---------------------------------------------------------------- the acceptance page


@pytest.mark.usefixtures("nda")
def test_the_reader_sends_you_to_the_nda_first(room: Room, document: Document) -> None:
    issued = share(room=room)
    browser = opened(issued)
    for path in ("", f"/d/{document.token}", f"/d/{document.token}/pages/1-800.webp"):
        response = browser.get(f"/v/{issued.token}{path}", secure=True)
        assert response.status_code == 303 and response["Location"] == f"/v/{issued.token}/nda"
    page = browser.get(f"/v/{issued.token}/nda", secure=True).content.decode()
    assert TEXT in page and 'name="name"' in page and 'name="accept"' in page
    assert "record of acceptance, not a signature" in page
    assert "IP address" in page


@pytest.mark.usefixtures("nda")
def test_accepting_records_everything_the_brief_asks(room: Room) -> None:
    issued = share(room=room)
    browser = opened(issued)
    response = accept(browser, issued, name="  Priya   Shah ")
    assert response.status_code == 303 and response["Location"] == f"/v/{issued.token}"
    record = NdaAcceptance.objects.get()
    assert (record.name, record.email, record.link) == ("Priya Shah", INVESTOR, issued.link)
    assert record.ip_address == "203.0.113.9" and record.user_agent == "Investor's browser"
    assert record.text_sha256 == hashlib.sha256(TEXT.encode()).hexdigest()
    assert record.version.number == 1
    assert AuditEvent.objects.filter(action="viewer.nda_accepted", actor=INVESTOR).exists()
    assert browser.get(f"/v/{issued.token}", secure=True).status_code == 200


@pytest.mark.usefixtures("nda")
@pytest.mark.parametrize(
    ("fields", "message"), [({"name": ""}, "Enter your name"), ({"accept": ""}, "Tick the box")]
)
def test_name_and_tick_are_both_needed(room: Room, fields: dict[str, str], message: str) -> None:
    issued = share(room=room)
    response = accept(opened(issued), issued, **fields)
    assert response.status_code == 400 and message in response.content.decode()
    assert not NdaAcceptance.objects.exists()


@pytest.mark.usefixtures("nda")
def test_the_nda_page_needs_a_session(room: Room) -> None:
    issued = share(room=room, require_verification=True)
    response = Client().get(f"/v/{issued.token}/nda", secure=True)
    assert response.status_code == 303 and response["Location"] == f"/v/{issued.token}"
    assert accept(Client(), issued).status_code == 303
    assert not NdaAcceptance.objects.exists()


@pytest.mark.usefixtures("nda")
def test_when_no_nda_is_needed_its_page_goes_to_the_document(document: Document) -> None:
    issued = share(document=document)
    response = opened(issued).get(f"/v/{issued.token}/nda", secure=True)
    assert response.status_code == 303 and response["Location"] == f"/v/{issued.token}"


def test_acceptances_cannot_be_changed(room: Room, nda: NdaVersion) -> None:
    issued = share(room=room)
    record = terms.accept(
        issued.link, nda, name="Priya Shah", email=INVESTOR, ip=None, user_agent=""
    )
    record.name = "Someone else"
    with pytest.raises(ValueError):
        record.save()
    with pytest.raises(ValueError):
        record.delete()


# ---------------------------------------------------------------- the admin


def admin_client() -> Client:
    admin = Admin.objects.create(email="ruth@fernlea.example", is_active=True)
    browser = Client()
    browser.force_login(admin)
    session = browser.session
    session[sessions.LOGIN_AT] = session[sessions.LAST_ACTIVITY] = time.time()
    session.save()
    return browser


def test_the_nda_is_set_in_settings() -> None:
    browser = admin_client()
    response = browser.post("/admin/settings/nda", {"text": TEXT}, secure=True)
    assert response.status_code == 302 and terms.current() is not None
    page = browser.get("/admin/settings/", secure=True).content.decode()
    assert TEXT in page and "Version 1" in page
    browser.post("/admin/settings/nda", {"text": TEXT + " Amended."}, secure=True)
    assert NdaVersion.objects.count() == 2
    assert AuditEvent.objects.filter(action="settings.nda_published").count() == 2


def test_a_room_can_require_the_nda_only_once_there_is_one(document: Document) -> None:
    browser = admin_client()
    room = Room.objects.create(name="Seed round")
    response = browser.post(f"/admin/rooms/{room.token}/nda", {"require_nda": "on"}, secure=True)
    assert response.status_code == 400
    room.refresh_from_db()
    assert not room.require_nda
    terms.publish(TEXT, by=None)
    browser.post(f"/admin/rooms/{room.token}/nda", {"require_nda": "on"}, secure=True)
    assert Room.objects.get(pk=room.pk).require_nda
    assert AuditEvent.objects.filter(action="room.nda_required").exists()


def test_without_an_nda_the_option_is_disabled_and_says_where_to_write_one(
    document: Document,
) -> None:
    browser = admin_client()
    page = browser.get(f"/admin/links/for/{document.token}", secure=True).content.decode()
    assert 'name="require_nda" disabled' in page
    assert 'href="/admin/settings/#nda">Settings</a>' in page
