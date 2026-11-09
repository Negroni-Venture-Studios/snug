"""UK-only mode: a link, or every link to a room, opens only from the UK.

The UK here is GB, Jersey, Guernsey and the Isle of Man, from DB-IP's data
(snug.core.geoip). It is authorise()'s check 4, and the gate pages check it
too, so nobody abroad is sent a code. An address whose country is unknown,
or a server without the data, is refused: the restriction fails closed.
"""

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from django.core import mail as django_mail
from django.test import Client

from snug.accounts import sessions as admin_sessions
from snug.accounts.models import Admin
from snug.audit.models import AuditEvent
from snug.core import doctor, geoip
from snug.documents.models import Document
from snug.links import service
from snug.links.models import Link
from snug.rooms.models import Room, RoomDocument
from snug.viewer.access import OutsideUK, authorise
from tests.test_viewer_access import verified_browser

pytestmark = pytest.mark.django_db

INVESTOR = "priya@northfield.example"


@pytest.fixture
def place(monkeypatch: pytest.MonkeyPatch) -> dict[str, str | None]:
    """Where the visitor appears to be. Every test address is 127.0.0.1."""
    where: dict[str, str | None] = {"country": "GB"}
    monkeypatch.setattr(geoip, "country", lambda address: where["country"])
    return where


def deck_link(**options: Any) -> service.Issued:
    deck = Document.objects.create(title="Seed deck")
    fields: dict[str, Any] = {
        "note": "",
        "expires_at": None,
        "require_verification": True,
        "allow_download": True,
        "created_by": None,
    }
    fields.update(options)
    return service.create(document=deck, email=INVESTOR, **fields)


def room_link(*, room_uk_only: bool, link_uk_only: bool = False) -> service.Issued:
    room = Room.objects.create(name="Series A", uk_only=room_uk_only)
    RoomDocument.objects.create(room=room, document=Document.objects.create(title="Model"))
    return service.create(
        room=room,
        email=INVESTOR,
        note="",
        expires_at=None,
        require_verification=True,
        allow_download=True,
        uk_only=link_uk_only,
        created_by=None,
    )


# ---------------------------------------------------------------- authorise(), check 4


@pytest.mark.parametrize("country", ["GB", "JE", "GG", "IM"])
def test_the_uk_and_crown_dependencies_are_let_in(
    country: str, place: dict[str, str | None]
) -> None:
    issued = deck_link(uk_only=True)
    place["country"] = country
    assert authorise(verified_browser(issued.link), issued.token).link == issued.link


@pytest.mark.parametrize("country", ["US", "IE", "FR", "GI", None])
def test_anywhere_else_or_unknown_is_refused(
    country: str | None, place: dict[str, str | None]
) -> None:
    issued = deck_link(uk_only=True)
    request = verified_browser(issued.link)
    place["country"] = country
    with pytest.raises(OutsideUK):
        authorise(request, issued.token)


def test_a_link_without_it_opens_anywhere(place: dict[str, str | None]) -> None:
    issued = deck_link()
    place["country"] = "US"
    assert authorise(verified_browser(issued.link), issued.token).link == issued.link


def test_a_room_link_follows_its_room(place: dict[str, str | None]) -> None:
    place["country"] = "US"
    restricted = room_link(room_uk_only=True)
    with pytest.raises(OutsideUK):
        authorise(verified_browser(restricted.link), restricted.token)
    # A room link never carries its own setting: the room decides.
    open_room = room_link(room_uk_only=False, link_uk_only=True)
    assert open_room.link.uk_only is False
    assert authorise(verified_browser(open_room.link), open_room.token)


def test_turning_it_on_ends_access_abroad_at_once(place: dict[str, str | None]) -> None:
    issued = room_link(room_uk_only=False)
    request = verified_browser(issued.link)
    place["country"] = "US"
    assert authorise(request, issued.token)
    assert issued.link.room is not None
    Room.objects.filter(pk=issued.link.room.pk).update(uk_only=True)
    with pytest.raises(OutsideUK):
        authorise(request, issued.token)


def test_without_geoip_data_it_fails_closed(data_folder: Path) -> None:
    issued = deck_link(uk_only=True)
    with pytest.raises(OutsideUK):
        authorise(verified_browser(issued.link), issued.token)


# ---------------------------------------------------------------- what the visitor sees


def test_the_entry_page_refuses_before_any_code_is_sent(
    place: dict[str, str | None], email_on: None
) -> None:
    issued = deck_link(uk_only=True)
    place["country"] = "US"
    visitor = Client()
    page = visitor.get(f"/v/{issued.token}", secure=True)
    assert page.status_code == 403
    text = page.content.decode()
    assert "This link opens only in the UK" in text
    assert "Jersey, Guernsey and the Isle of Man" in text
    sent = visitor.post(f"/v/{issued.token}/code/send", {"email": INVESTOR}, secure=True)
    assert sent.status_code == 403
    assert not django_mail.outbox
    event = AuditEvent.objects.filter(action="viewer.outside_uk").latest("id")
    assert event.details["country"] == "US"
    assert event.details["link"] == issued.link.label


def test_an_unknown_country_is_named_as_unknown(place: dict[str, str | None]) -> None:
    issued = deck_link(uk_only=True)
    place["country"] = None
    assert Client().get(f"/v/{issued.token}", secure=True).status_code == 403
    event = AuditEvent.objects.get(action="viewer.outside_uk")
    assert event.details["country"] == "unknown"


def test_a_session_started_in_the_uk_does_not_work_abroad(place: dict[str, str | None]) -> None:
    issued = deck_link(uk_only=True, require_verification=False)
    visitor = Client()
    visitor.post(f"/v/{issued.token}/open", secure=True)
    assert visitor.get(f"/v/{issued.token}", secure=True).status_code == 200
    place["country"] = "FR"
    assert visitor.get(f"/v/{issued.token}", secure=True).status_code == 403


# ---------------------------------------------------------------- the country of every view


def test_every_viewer_event_records_its_country_without_uk_only(
    place: dict[str, str | None],
) -> None:
    """The brief: record the country of every view, whether or not the restriction is on."""
    issued = deck_link(uk_only=False, require_verification=False)
    place["country"] = "FR"
    visitor = Client()
    visitor.post(f"/v/{issued.token}/open", secure=True)
    visitor.get(f"/v/{issued.token}", secure=True)
    events = AuditEvent.objects.filter(actor_type=AuditEvent.ActorType.VIEWER)
    assert "viewer.opened" in {e.action for e in events}
    assert {e.country for e in events} == {"FR"}


def test_a_view_from_nowhere_known_leaves_the_country_empty(
    place: dict[str, str | None],
) -> None:
    issued = deck_link(uk_only=False, require_verification=False)
    place["country"] = None
    Client().post(f"/v/{issued.token}/open", secure=True)
    assert AuditEvent.objects.get(action="viewer.opened").country == ""


def test_a_refusal_records_its_country_too(place: dict[str, str | None]) -> None:
    issued = deck_link(uk_only=True)
    place["country"] = "US"
    Client().get(f"/v/{issued.token}", secure=True)
    assert AuditEvent.objects.get(action="viewer.outside_uk").country == "US"


# ---------------------------------------------------------------- the admin interface


@pytest.fixture
def admin_client() -> Client:
    admin = Admin.objects.create(email="ruth@fernlea.example", is_active=True)
    client = Client()
    client.force_login(admin)
    session = client.session
    session[admin_sessions.LOGIN_AT] = session[admin_sessions.LAST_ACTIVITY] = time.time()
    session.save()
    return client


def test_the_link_form_offers_it_with_db_ip_credited(admin_client: Client) -> None:
    deck = Document.objects.create(title="Seed deck")
    page = admin_client.get(f"/admin/links/for/{deck.token}", secure=True).content.decode()
    assert 'name="uk_only"' in page
    assert "Only from the UK" in page
    assert '<a href="https://db-ip.com">IP geolocation by DB-IP</a>' in page


def test_a_link_can_be_made_uk_only(admin_client: Client) -> None:
    deck = Document.objects.create(title="Seed deck")
    admin_client.post(
        f"/admin/links/for/{deck.token}",
        {"email": INVESTOR, "require_verification": "on", "uk_only": "on", "action": "copy"},
        secure=True,
    )
    link = Link.objects.get()
    assert link.uk_only
    assert AuditEvent.objects.get(action="link.created").details["uk_only"] is True
    listing = admin_client.get("/admin/links/", secure=True).content.decode()
    assert "UK only" in listing


def test_a_room_link_form_says_the_room_decides(admin_client: Client) -> None:
    room = Room.objects.create(name="Series A", uk_only=True)
    page = admin_client.get(f"/admin/links/for-room/{room.token}", secure=True).content.decode()
    assert 'name="uk_only"' not in page


def test_a_room_can_be_made_uk_only(admin_client: Client) -> None:
    room = Room.objects.create(name="Series A")
    page = admin_client.get(f"/admin/rooms/{room.token}", secure=True).content.decode()
    assert f'action="/admin/rooms/{room.token}/uk-only"' in page
    assert '<a href="https://db-ip.com">IP geolocation by DB-IP</a>' in page

    response = admin_client.post(
        f"/admin/rooms/{room.token}/uk-only", {"uk_only": "on"}, secure=True
    )
    assert response["Location"] == f"/admin/rooms/{room.token}#uk-only"
    room.refresh_from_db()
    assert room.uk_only
    assert AuditEvent.objects.filter(action="room.uk_only").count() == 1
    page = admin_client.get(f"/admin/rooms/{room.token}", secure=True).content.decode()
    assert '<span class="meta-uk">UK only</span>' in page
    notice = page[page.index('id="uk-only"') :]
    assert "Saved. Links to this room open only in the UK" in notice

    admin_client.post(f"/admin/rooms/{room.token}/uk-only", {}, secure=True)
    assert Room.objects.get(pk=room.pk).uk_only is False
    assert AuditEvent.objects.filter(action="room.not_uk_only").count() == 1


# ---------------------------------------------------------------- snug doctor


NOW = datetime(2026, 11, 6, 15, 0, tzinfo=UTC)


def test_doctor_skips_geoip_when_nothing_is_uk_only(data_folder: Path) -> None:
    assert doctor.geoip_data(NOW).status == "skipped"


def test_doctor_warns_when_uk_only_links_have_no_data(data_folder: Path) -> None:
    deck_link(uk_only=True)
    found = doctor.geoip_data(NOW)
    assert found.status == "problem"
    assert found.message == (
        "UK-only links refuse everyone: there is no country data yet. Housekeeping "
        "downloads it from DB-IP; check that the server can reach download.db-ip.com."
    )
    assert found in doctor.problems(NOW)


def test_doctor_warns_when_the_data_is_old(
    data_folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    Room.objects.create(name="Series A", uk_only=True)
    monkeypatch.setattr(geoip, "edition", lambda: "2026-08")
    found = doctor.geoip_data(NOW)
    assert found.status == "problem"
    assert found.message == (
        "DB-IP's country data is from 2026-08 and should be renewed monthly. Check that "
        "the server can reach download.db-ip.com."
    )


@pytest.mark.parametrize("held", ["2026-11", "2026-10"])
def test_doctor_is_content_with_this_or_last_months_data(
    held: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    deck_link(uk_only=True)
    monkeypatch.setattr(geoip, "edition", lambda: held)
    found = doctor.geoip_data(NOW + timedelta(days=0))
    assert found.status == "ok"
    assert found.message == f"DB-IP's country data from {held}, for UK-only links."
