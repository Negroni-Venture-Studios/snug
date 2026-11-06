"""Every admin save says it worked, in the section where it was made.

Asked for 6 November 2026: saving on the room page reloaded it at the top
with no sign anything had changed. A save now returns to its own section
(#anchor) with a green notice there, as Settings does, shown once. Errors
already show in red beside the form.
"""

import re
import time
from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from snug.accounts import sessions
from snug.accounts.models import Admin
from snug.documents.models import Document
from snug.links import service
from snug.nda import terms as nda_terms
from snug.rooms.models import Folder, Room, RoomDocument
from tests.pdfs import make_pdf

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def own_data_folder(data_folder: Path) -> None:
    """Uploads need a data folder of their own (see conftest.py)."""


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
def room() -> Room:
    return Room.objects.create(name="Seed round")


@pytest.fixture
def deck() -> Document:
    return Document.objects.create(title="Seed deck")


def saved(client: Client, url: str, data: dict[str, object] | None = None) -> tuple[str, str]:
    """POST, check it redirects to a section, and return (anchor, that section's HTML)."""
    response = client.post(url, data or {}, secure=True)
    assert response.status_code == 302, response.content.decode()[:500]
    location = response["Location"]
    match = re.fullmatch(r"([^?#]+)#([a-z-]+)", location)
    assert match, location
    page = client.get(location.split("#")[0], secure=True).content.decode()
    anchor = match.group(2)
    start = page.index(f'id="{anchor}"')
    section = page[start : page.index("</section>", start)]
    return anchor, section


def notice(section: str) -> str:
    found = re.search(r'<div class="notice notice-ok" role="status">.*?<span>(.*?)</span>', section)
    assert found, "no saved notice in the section"
    return found.group(1)


# ---------------------------------------------------------------- rooms


def test_creating_a_room(client: Client) -> None:
    anchor, section = saved(client, "/admin/rooms/new", {"name": "Series A"})
    assert anchor == "documents"
    assert notice(section) == "Room created. Add documents below, then share it with a link."


def test_renaming_a_room(client: Client, room: Room) -> None:
    anchor, section = saved(client, f"/admin/rooms/{room.token}/rename", {"name": "Series A"})
    assert anchor == "rename"
    assert notice(section) == "Renamed."


def test_turning_the_nda_on_and_off(client: Client, room: Room) -> None:
    nda_terms.publish("Keep it to yourself.", by=None)
    anchor, section = saved(client, f"/admin/rooms/{room.token}/nda", {"require_nda": "on"})
    assert anchor == "nda"
    assert notice(section) == (
        "Saved. Every link to this room asks for the NDA, version 1, before showing anything."
    )
    _, section = saved(client, f"/admin/rooms/{room.token}/nda")
    assert notice(section) == "Saved. Links to this room no longer ask for the NDA."


def test_adding_and_removing_a_folder(client: Client, room: Room) -> None:
    anchor, section = saved(client, f"/admin/rooms/{room.token}/folders", {"name": "Legal"})
    assert anchor == "add-folder"
    assert notice(section) == "Folder added. Move documents into it from the list above."
    folder = Folder.objects.get()
    anchor, section = saved(client, f"/admin/rooms/{room.token}/folders/{folder.pk}/remove")
    assert anchor == "documents"
    assert notice(section) == "Folder removed. Its documents are now at the top."


def test_adding_moving_and_removing_a_document(client: Client, room: Room, deck: Document) -> None:
    anchor, section = saved(
        client, f"/admin/rooms/{room.token}/documents", {"document": deck.token}
    )
    assert anchor == "add-document"
    assert notice(section) == "Seed deck added to the room."

    folder = Folder.objects.create(room=room, name="Legal")
    url = f"/admin/rooms/{room.token}/documents/{deck.token}"
    anchor, section = saved(client, f"{url}/move", {"folder": str(folder.pk)})
    assert anchor == "documents"
    assert notice(section) == "Seed deck moved to Legal."
    _, section = saved(client, f"{url}/move", {"folder": ""})
    assert notice(section) == "Seed deck moved to the top."

    anchor, section = saved(client, f"{url}/remove")
    assert anchor == "documents"
    assert notice(section) == "Seed deck removed. Links to this room no longer reach it."
    assert not RoomDocument.objects.exists()


def test_a_notice_shows_once(client: Client, room: Room) -> None:
    saved(client, f"/admin/rooms/{room.token}/rename", {"name": "Series A"})
    page = client.get(f"/admin/rooms/{room.token}", secure=True).content.decode()
    assert "notice-ok" not in page


def test_the_room_says_when_it_asks_for_the_nda(client: Client, room: Room) -> None:
    def meta() -> str:
        page = client.get(f"/admin/rooms/{room.token}", secure=True).content.decode()
        return page[page.index('class="page-title"') : page.index("</h1>") + 200]

    nda_terms.publish("Keep it to yourself.", by=None)
    assert "Asks for the NDA" not in meta()
    Room.objects.filter(pk=room.pk).update(require_nda=True)
    assert "Asks for the NDA, version 1" in meta()


# ---------------------------------------------------------------- documents and links

DECK = make_pdf((960, 540), (960, 540))


def test_uploading_a_document(client: Client) -> None:
    pdf = SimpleUploadedFile("deck.pdf", DECK, content_type="application/pdf")
    anchor, section = saved(client, "/admin/documents/new", {"title": "Seed deck", "pdf": pdf})
    assert anchor == "links"
    assert notice(section) == "Uploaded, 2 pages. Create a link to share it."


def test_uploading_a_new_version(client: Client, deck: Document) -> None:
    pdf = SimpleUploadedFile("deck.pdf", DECK, content_type="application/pdf")
    anchor, section = saved(client, f"/admin/documents/{deck.token}/versions", {"pdf": pdf})
    assert anchor == "versions"
    assert notice(section) == "Version 1 uploaded. Every link to this document shows it now."


def test_revoking_a_link(client: Client, deck: Document, room: Room) -> None:
    for target in ({"document": deck}, {"room": room}):
        link = service.create(
            **target,
            email="priya@northfield.example",
            note="",
            expires_at=None,
            require_verification=True,
            allow_download=True,
            created_by=None,
        ).link
        anchor, section = saved(client, f"/admin/links/{link.ref}/revoke")
        assert anchor == "links"
        assert notice(section) == (
            f"Revoked L-{link.ref}. It stopped working at once, for anyone already in too."
        )
