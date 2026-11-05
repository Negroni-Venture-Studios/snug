"""Rooms in the admin: a named collection of documents, with one level of folders.

Decided 5 November 2026: a document can be in any number of rooms.
"""

import time
from typing import Any

import pytest
from django.test import Client

from snug.accounts import sessions
from snug.accounts.models import Admin
from snug.audit.models import AuditEvent
from snug.documents.models import Document
from snug.rooms.models import Folder, Room, RoomDocument

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin() -> Admin:
    return Admin.objects.create(email="ruth@fernlea.example", is_active=True)


@pytest.fixture
def client(admin: Admin) -> Client:
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


def post(client: Client, url: str, data: dict[str, str] | None = None) -> Any:
    return client.post(url, data or {}, secure=True)


def actions() -> list[str]:
    return list(AuditEvent.objects.order_by("id").values_list("action", flat=True))


# ---------------------------------------------------------------- creating


def test_creating_a_room(client: Client) -> None:
    response = post(client, "/admin/rooms/new", {"name": "  Seed   round "})
    room = Room.objects.get()
    assert room.name == "Seed round" and len(room.token) >= 20
    assert response.status_code == 302 and response["Location"] == f"/admin/rooms/{room.token}"
    assert actions() == ["room.created"]


@pytest.mark.parametrize("name", ["", "   ", "x" * 121])
def test_a_room_needs_a_sensible_name(client: Client, name: str) -> None:
    response = post(client, "/admin/rooms/new", {"name": name})
    assert response.status_code == 400 and not Room.objects.exists()


def test_rooms_are_listed(client: Client, room: Room, deck: Document) -> None:
    RoomDocument.objects.create(room=room, document=deck)
    page = client.get("/admin/rooms/", secure=True).content.decode()
    assert 'aria-current="page">Rooms</a>' in page
    assert "Seed round" in page and "1 document" in page


def test_no_rooms_yet(client: Client) -> None:
    assert "No rooms yet" in client.get("/admin/rooms/", secure=True).content.decode()


def test_renaming(client: Client, room: Room) -> None:
    post(client, f"/admin/rooms/{room.token}/rename", {"name": "Series A"})
    room.refresh_from_db()
    assert room.name == "Series A" and "room.renamed" in actions()


# ---------------------------------------------------------------- folders


def test_folders_one_level_unique_names(client: Client, room: Room) -> None:
    post(client, f"/admin/rooms/{room.token}/folders", {"name": "Financials"})
    response = post(client, f"/admin/rooms/{room.token}/folders", {"name": "financials"})
    assert response.status_code == 400
    assert list(room.folders.values_list("name", flat=True)) == ["Financials"]
    assert "room.folder_added" in actions()


def test_removing_a_folder_keeps_its_documents(client: Client, room: Room, deck: Document) -> None:
    folder = Folder.objects.create(room=room, name="Financials")
    RoomDocument.objects.create(room=room, document=deck, folder=folder)
    post(client, f"/admin/rooms/{room.token}/folders/{folder.pk}/remove")
    assert not Folder.objects.exists()
    assert RoomDocument.objects.get().folder is None


# ---------------------------------------------------------------- documents


def test_adding_a_document_to_a_folder(client: Client, room: Room, deck: Document) -> None:
    folder = Folder.objects.create(room=room, name="Pitch")
    data = {"document": deck.token, "folder": str(folder.pk)}
    assert post(client, f"/admin/rooms/{room.token}/documents", data).status_code == 302
    entry = RoomDocument.objects.get()
    assert (entry.document, entry.folder) == (deck, folder)
    assert "room.document_added" in actions()


def test_a_document_can_be_in_many_rooms_but_once_in_each(
    client: Client, room: Room, deck: Document
) -> None:
    other = Room.objects.create(name="Series A")
    post(client, f"/admin/rooms/{room.token}/documents", {"document": deck.token})
    post(client, f"/admin/rooms/{other.token}/documents", {"document": deck.token})
    again = post(client, f"/admin/rooms/{room.token}/documents", {"document": deck.token})
    assert again.status_code == 400
    assert RoomDocument.objects.count() == 2


def test_a_folder_from_another_room_is_refused(client: Client, room: Room, deck: Document) -> None:
    elsewhere = Folder.objects.create(room=Room.objects.create(name="Other"), name="X")
    data = {"document": deck.token, "folder": str(elsewhere.pk)}
    assert post(client, f"/admin/rooms/{room.token}/documents", data).status_code == 400
    assert not RoomDocument.objects.exists()


def test_moving_and_removing_a_document(client: Client, room: Room, deck: Document) -> None:
    folder = Folder.objects.create(room=room, name="Pitch")
    entry = RoomDocument.objects.create(room=room, document=deck)
    post(
        client, f"/admin/rooms/{room.token}/documents/{deck.token}/move", {"folder": str(folder.pk)}
    )
    entry.refresh_from_db()
    assert entry.folder == folder
    post(client, f"/admin/rooms/{room.token}/documents/{deck.token}/move", {"folder": ""})
    assert RoomDocument.objects.get(pk=entry.pk).folder is None
    post(client, f"/admin/rooms/{room.token}/documents/{deck.token}/remove")
    assert not RoomDocument.objects.exists() and Document.objects.filter(pk=deck.pk).exists()
    assert {"room.document_moved", "room.document_removed"} <= set(actions())


def test_the_room_page_groups_documents_by_folder(
    client: Client, room: Room, deck: Document
) -> None:
    model = Document.objects.create(title="Financial model")
    folder = Folder.objects.create(room=room, name="Financials")
    RoomDocument.objects.create(room=room, document=deck)
    RoomDocument.objects.create(room=room, document=model, folder=folder)
    page = client.get(f"/admin/rooms/{room.token}", secure=True).content.decode()
    assert page.index("Seed deck") < page.index("Financials") < page.index("Financial model")


# ---------------------------------------------------------------- safety


@pytest.mark.parametrize(
    "url",
    [
        "/admin/rooms/new",
        "/admin/rooms/{room}/rename",
        "/admin/rooms/{room}/folders",
        "/admin/rooms/{room}/documents",
    ],
)
def test_room_changes_need_csrf(admin: Admin, room: Room, url: str) -> None:
    strict = Client(enforce_csrf_checks=True)
    strict.force_login(admin)
    response = strict.post(url.format(room=room.token), {"name": "x"}, secure=True)
    assert response.status_code == 403


def test_rooms_need_a_signed_in_admin(room: Room) -> None:
    assert Client().get("/admin/rooms/", secure=True).status_code == 302
    assert Client().get(f"/admin/rooms/{room.token}", secure=True).status_code == 302


def test_unknown_rooms_are_not_found(client: Client) -> None:
    assert client.get("/admin/rooms/nope", secure=True).status_code == 404
