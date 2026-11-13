"""Writing the NDA where it is needed, when there is none yet.

Found while testing, 5 November 2026: with no NDA, the link form's "Ask for
the NDA first" and the room's NDA switch were disabled and pointed to
Settings, so a founder lost what they had typed. Now a text box there takes
version 1, published as Settings would publish it.
"""

import time
from typing import Any

import pytest
from django.test import Client

from snug.accounts import sessions
from snug.accounts.models import Admin
from snug.audit.models import AuditEvent
from snug.documents.models import Document
from snug.links.models import Link
from snug.nda import terms
from snug.nda.models import NdaVersion
from snug.rooms.models import Room

pytestmark = pytest.mark.django_db

TEXT = "The recipient will keep everything in this data room confidential."
INVESTOR = "priya@northfield.example"


@pytest.fixture
def client() -> Client:
    admin = Admin.objects.create(email="ruth@fernlea.example", is_active=True)
    browser = Client()
    browser.force_login(admin)
    session = browser.session
    session[sessions.LOGIN_AT] = session[sessions.LAST_ACTIVITY] = time.time()
    session.save()
    return browser


@pytest.fixture
def deck() -> Document:
    return Document.objects.create(title="Seed deck")


@pytest.fixture
def room() -> Room:
    return Room.objects.create(name="Seed round")


def new_link(client: Client, deck: Document, **fields: str) -> Any:
    data = {"email": INVESTOR, "require_verification": "on", "allow_download": "on", **fields}
    return client.post(f"/admin/links/for/{deck.token}", data, secure=True)


def published() -> list[AuditEvent]:
    return list(AuditEvent.objects.filter(action="settings.nda_published"))


# ---------------------------------------------------------------- the link form


def test_with_no_nda_the_link_form_offers_a_box_for_it(client: Client, deck: Document) -> None:
    page = client.get(f"/admin/links/for/{deck.token}", secure=True).content.decode()
    tick = page[page.index('name="require_nda"') - 40 : page.index('name="require_nda"') + 80]
    assert "disabled" not in tick
    assert '<textarea id="nda-text" name="nda_text"' in page
    assert "There is no NDA yet. Write it below" in page


def test_with_an_nda_the_link_form_has_no_box(client: Client, deck: Document) -> None:
    terms.publish(TEXT, by=None)
    page = client.get(f"/admin/links/for/{deck.token}", secure=True).content.decode()
    assert 'name="nda_text"' not in page


def test_writing_the_nda_on_the_link_form_publishes_version_1(
    client: Client, deck: Document
) -> None:
    response = new_link(client, deck, require_nda="on", nda_text=f"  {TEXT}  ")
    assert response.status_code == 302
    [version] = NdaVersion.objects.all()
    assert (version.number, version.text) == (1, TEXT)
    assert version.created_by is not None
    link = Link.objects.get()
    assert link.require_nda is True
    [event] = published()
    assert event.details["version"] == 1 and event.details["written_on"] == "link form"


def test_ticking_without_writing_explains_and_keeps_what_was_typed(
    client: Client, deck: Document
) -> None:
    response = new_link(client, deck, require_nda="on", nda_text="  ", note="Partner")
    assert response.status_code == 400
    page = response.content.decode()
    assert "Write the NDA, or untick Ask for the NDA first." in page
    assert f'value="{INVESTOR}"' in page and 'value="Partner"' in page
    assert not Link.objects.exists() and not NdaVersion.objects.exists()


def test_text_without_the_tick_publishes_nothing(client: Client, deck: Document) -> None:
    assert new_link(client, deck, nda_text=TEXT).status_code == 302
    assert not NdaVersion.objects.exists()
    assert Link.objects.get().require_nda is False


def test_an_nda_written_meanwhile_is_used_not_replaced(client: Client, deck: Document) -> None:
    # Another admin published one after this form was opened: no version 2.
    terms.publish("Theirs, published first.", by=None)
    assert new_link(client, deck, require_nda="on", nda_text=TEXT).status_code == 302
    assert list(NdaVersion.objects.values_list("number", "text")) == [
        (1, "Theirs, published first.")
    ]
    assert Link.objects.get().require_nda is True
    assert not published()


# ---------------------------------------------------------------- the room's switch


def test_with_no_nda_the_room_offers_a_box_for_it(client: Client, room: Room) -> None:
    page = client.get(f"/admin/rooms/{room.token}", secure=True).content.decode()
    panel = page[page.index('id="nda"') : page.index("</section>", page.index('id="nda"'))]
    assert "disabled" not in panel
    assert '<textarea id="room-nda-text" name="nda_text"' in panel


def test_writing_the_nda_on_the_room_publishes_version_1(client: Client, room: Room) -> None:
    response = client.post(
        f"/admin/rooms/{room.token}/nda", {"require_nda": "on", "nda_text": TEXT}, secure=True
    )
    assert response.status_code == 302
    assert NdaVersion.objects.get().text == TEXT
    room.refresh_from_db()
    assert room.require_nda is True
    [event] = published()
    assert event.details["written_on"] == "room"
    page = client.get(response["Location"].split("#")[0], secure=True).content.decode()
    assert "Every link to this room asks for the NDA, version 1" in page


def test_ticking_the_room_switch_without_writing_explains(client: Client, room: Room) -> None:
    response = client.post(
        f"/admin/rooms/{room.token}/nda", {"require_nda": "on", "nda_text": ""}, secure=True
    )
    assert response.status_code == 400
    assert "Write the NDA, or untick Ask for the NDA." in response.content.decode()
    assert not NdaVersion.objects.exists()
    room.refresh_from_db()
    assert room.require_nda is False


def test_an_overlong_nda_is_refused(client: Client, room: Room) -> None:
    text = "x" * (terms.MAX_TEXT + 1)
    response = client.post(
        f"/admin/rooms/{room.token}/nda", {"require_nda": "on", "nda_text": text}, secure=True
    )
    assert response.status_code == 400
    assert "up to 50,000 characters" in response.content.decode()
    assert not NdaVersion.objects.exists()
