"""The NDA: authorise() check 3, the acceptance page, and the admin's text.

Decided 5 November 2026: one NDA for the instance, set in Settings and
versioned; a room, or a link to a single document, requires it or not;
acceptance is per link and per version.
"""

import hashlib
import time
from collections.abc import Callable
from typing import Any

import pytest
from django.db import IntegrityError, transaction
from django.db.models.query import QuerySet
from django.test import Client, RequestFactory

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


def stale_current(monkeypatch: pytest.MonkeyPatch, seen: NdaVersion | None, times: int) -> None:
    """terms.current() answers `seen` for its next `times` calls: another admin's
    publish lands between this request reading the NDA and writing it."""
    real = terms.current
    calls = {"n": 0}

    def current() -> NdaVersion | None:
        calls["n"] += 1
        return seen if calls["n"] <= times else real()

    monkeypatch.setattr(terms, "current", current)


def missed_once(monkeypatch: pytest.MonkeyPatch, model: type[Any]) -> None:
    """The first lookup of `model` finds nothing: a racing request has not committed yet."""
    real: Callable[..., Any] = QuerySet.get
    missed = {"done": False}

    def get(self: QuerySet[Any], *args: Any, **kwargs: Any) -> Any:
        if self.model is model and not missed["done"]:
            missed["done"] = True
            raise model.DoesNotExist
        return real(self, *args, **kwargs)

    monkeypatch.setattr(QuerySet, "get", get)


# ---------------------------------------------------------------- versions


def test_publishing_versions_the_text() -> None:
    first = terms.publish(TEXT, by=None)
    assert (first.number, first.sha256) == (1, hashlib.sha256(TEXT.encode()).hexdigest())
    assert terms.publish(f"  {TEXT}  ", by=None) == first  # unchanged text, no new version
    second = terms.publish(TEXT + " For two years.", by=None)
    assert second.number == 2 and terms.current() == second


def test_a_racing_publish_takes_the_next_number(monkeypatch: pytest.MonkeyPatch) -> None:
    # Found in the audit of 10 November 2026: two admins saving at once both
    # chose the same number, and the second save failed with a server error.
    first = terms.publish(TEXT, by=None)
    NdaVersion.objects.create(number=2, text="Theirs.", sha256="0" * 64)
    stale_current(monkeypatch, first, times=1)
    mine = terms.publish(TEXT + " Mine.", by=None)
    assert (mine.number, mine.text) == (3, TEXT + " Mine.")


def test_a_racing_publish_of_the_same_text_keeps_theirs(monkeypatch: pytest.MonkeyPatch) -> None:
    first = terms.publish(TEXT, by=None)
    theirs = NdaVersion.objects.create(number=2, text=TEXT + " Same.", sha256="0" * 64)
    stale_current(monkeypatch, first, times=1)
    assert terms.publish(TEXT + " Same.", by=None) == theirs
    assert NdaVersion.objects.count() == 2


def test_a_racing_first_publish_uses_theirs(monkeypatch: pytest.MonkeyPatch) -> None:
    theirs = terms.publish("Theirs, published first.", by=None)
    admin = Admin.objects.create(email="ruth@fernlea.example", is_active=True)
    stale_current(monkeypatch, None, times=1)
    request = RequestFactory().post("/admin/rooms/")
    version = terms.publish_first(request, TEXT, by=admin, written_on="room")
    assert version == theirs and NdaVersion.objects.count() == 1
    assert not AuditEvent.objects.filter(action="settings.nda_published").exists()


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


def test_a_link_holds_one_acceptance_of_each_version(room: Room, nda: NdaVersion) -> None:
    issued = share(room=room)
    fields = {"link": issued.link, "version": nda, "name": "Priya Shah", "email": INVESTOR}
    NdaAcceptance.objects.create(**fields)
    with pytest.raises(IntegrityError), transaction.atomic():
        NdaAcceptance.objects.create(**fields)


def test_accepting_again_keeps_the_first_record(room: Room, nda: NdaVersion) -> None:
    issued = share(room=room)
    first, _ = terms.accept(
        issued.link, nda, name="Priya Shah", email=INVESTOR, ip=None, user_agent=""
    )
    again = terms.accept(issued.link, nda, name="P Shah", email=INVESTOR, ip=None, user_agent="")
    assert again == (first, False) and NdaAcceptance.objects.get().name == "Priya Shah"


def test_a_racing_acceptance_keeps_the_first_record(
    room: Room, nda: NdaVersion, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Found in the audit of 10 November 2026: two acceptances posted at once
    # both passed the check and both wrote a record.
    issued = share(room=room)
    first, _ = terms.accept(
        issued.link, nda, name="Priya Shah", email=INVESTOR, ip=None, user_agent=""
    )
    missed_once(monkeypatch, NdaAcceptance)
    again = terms.accept(issued.link, nda, name="P Shah", email=INVESTOR, ip=None, user_agent="")
    assert again == (first, False) and NdaAcceptance.objects.count() == 1


@pytest.mark.usefixtures("nda")
def test_a_second_acceptance_posted_at_once_is_not_recorded(
    room: Room, monkeypatch: pytest.MonkeyPatch
) -> None:
    issued = share(room=room)
    browser = opened(issued)
    assert accept(browser, issued).status_code == 303
    # The second request read the NDA as not yet accepted, before the first wrote.
    monkeypatch.setattr(terms, "accepted", lambda link, version: False)
    response = accept(browser, issued, name="P Shah")
    assert response.status_code == 303 and response["Location"] == f"/v/{issued.token}"
    assert NdaAcceptance.objects.get().name == "Priya Shah"
    assert AuditEvent.objects.filter(action="viewer.nda_accepted").count() == 1


def test_acceptances_cannot_be_changed(room: Room, nda: NdaVersion) -> None:
    issued = share(room=room)
    record, _ = terms.accept(
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


def test_a_racing_save_in_settings_is_saved(monkeypatch: pytest.MonkeyPatch) -> None:
    browser = admin_client()
    first = terms.publish(TEXT, by=None)
    NdaVersion.objects.create(number=2, text="Theirs.", sha256="0" * 64)
    stale_current(monkeypatch, first, times=2)
    response = browser.post("/admin/settings/nda", {"text": TEXT + " Mine."}, secure=True)
    assert response.status_code == 302 and response["Location"].endswith("saved=nda#nda")
    assert NdaVersion.objects.get(number=3).text == TEXT + " Mine."


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


def test_without_an_nda_the_option_offers_a_box_to_write_one(document: Document) -> None:
    # Was disabled with a pointer to Settings until 6 November 2026; writing
    # version 1 in place is covered in tests/test_nda_inline.py.
    browser = admin_client()
    page = browser.get(f"/admin/links/for/{document.token}", secure=True).content.decode()
    assert 'name="require_nda" disabled' not in page
    assert 'name="nda_text"' in page


def test_saving_the_nda_says_so_in_its_own_section() -> None:
    browser = admin_client()
    response = browser.post("/admin/settings/nda", {"text": TEXT}, secure=True, follow=True)
    page = response.content.decode()
    nda_section = page[page.index('id="nda"') : page.index('id="privacy"')]
    assert "Saved as version 1" in nda_section
    privacy_section = page[page.index('id="privacy"') :]
    assert "Saved." not in privacy_section  # each section confirms only its own save
