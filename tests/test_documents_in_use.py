"""The Documents list shows how many links open each document, and its last view (#153)."""

import re
from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from snug.documents.models import Document
from snug.links import service
from snug.rooms.models import Room, RoomDocument
from tests.test_links import admin, client
from tests.test_overview import make_link, visit

__all__ = ["admin", "client"]  # fixtures, used by name

pytestmark = pytest.mark.django_db


def row(client: Client, title: str) -> str:
    page = client.get("/admin/documents/", secure=True).content.decode()
    start = page.index(f">{title}</a>")
    end = page.find('<div class="item">', start)
    return page[start : end if end != -1 else len(page)]


def text(html: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def test_links_that_open_a_document_are_counted(client: Client) -> None:
    deck = Document.objects.create(title="Seed deck")
    make_link("a@one.example", document=deck)
    make_link("b@two.example", document=deck)
    gone = make_link("c@three.example", document=deck)
    service.revoke(gone, by=None)
    late = make_link("d@four.example", document=deck)
    late.expires_at = timezone.now() - timedelta(hours=1)
    late.save()
    # Through a room as well: three working links to a room that holds the deck.
    room = Room.objects.create(name="Series A")
    RoomDocument.objects.create(room=room, document=deck)
    for name in ("e", "f", "g"):
        make_link(f"{name}@room.example", room=room)
    # A room that does not hold the deck adds nothing.
    other = Room.objects.create(name="Board pack")
    make_link("h@board.example", room=other)

    assert "5 active links" in text(row(client, "Seed deck"))


def test_one_link_and_none(client: Client) -> None:
    one = Document.objects.create(title="Financial model")
    make_link("a@one.example", document=one)
    Document.objects.create(title="Cap table")
    assert "1 active link" in text(row(client, "Financial model"))
    assert "1 active links" not in text(row(client, "Financial model"))
    assert "No active links" in text(row(client, "Cap table"))


def test_the_last_view_is_shown(client: Client) -> None:
    deck = Document.objects.create(title="Seed deck")
    link = make_link("a@one.example", document=deck)
    visit(link, deck, ago=timedelta(days=11), seconds=30)
    latest = visit(link, deck, ago=timedelta(days=4), seconds=30)
    latest.last_seen_at = latest.started_at + timedelta(minutes=20)
    latest.save()
    Document.objects.create(title="Cap table")
    day = timezone.localtime(latest.last_seen_at)
    assert f"Viewed {day.day} {day:%b}" in text(row(client, "Seed deck"))
    assert "Not viewed" in text(row(client, "Cap table"))


def test_the_list_does_not_grow_its_queries_with_documents(
    client: Client, django_assert_max_num_queries: object
) -> None:
    for number in range(12):
        deck = Document.objects.create(title=f"Deck {number}")
        link = make_link(f"p{number}@one.example", document=deck)
        visit(link, deck, ago=timedelta(days=1), seconds=5)
    with django_assert_max_num_queries(14):  # type: ignore[operator]
        client.get("/admin/documents/", secure=True)
