"""Filters on the Links page: by status, and by a search (#152)."""

from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from snug.documents.models import Document
from snug.links import service
from snug.links.models import Link
from snug.rooms.models import Room
from tests.test_links import admin, client
from tests.test_overview import make_link

__all__ = ["admin", "client"]  # fixtures, used by name

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def links() -> dict[str, Link]:
    deck = Document.objects.create(title="Seed deck")
    model = Document.objects.create(title="Financial model")
    room = Room.objects.create(name="Series A room")
    made = {
        "active": make_link("priya@northfield.example", document=deck),
        "room": make_link("sam@eastgate.example", room=room),
        "expired": make_link("lena@foxglove.example", document=model),
        "revoked": make_link("tom@harbourcap.example", document=deck),
    }
    made["room"].note = "Partner at Eastgate"
    made["room"].save()
    made["expired"].expires_at = timezone.now() - timedelta(days=1)
    made["expired"].save()
    service.revoke(made["revoked"], by=None)
    return made


def shown(client: Client, query: str = "") -> set[str]:
    page = client.get(f"/admin/links/{query}", secure=True).content.decode()
    emails = (
        "priya@northfield.example",
        "sam@eastgate.example",
        "lena@foxglove.example",
        "tom@harbourcap.example",
    )
    return {email for email in emails if email in page}


ALL = {
    "priya@northfield.example",
    "sam@eastgate.example",
    "lena@foxglove.example",
    "tom@harbourcap.example",
}


def test_every_link_is_listed_without_a_filter(client: Client) -> None:
    assert shown(client) == ALL
    assert shown(client, "?status=&q=") == ALL


@pytest.mark.parametrize(
    ("status", "emails"),
    [
        ("active", {"priya@northfield.example", "sam@eastgate.example"}),
        ("expired", {"lena@foxglove.example"}),
        ("revoked", {"tom@harbourcap.example"}),
        ("nonsense", ALL),
    ],
)
def test_links_are_filtered_by_status(client: Client, status: str, emails: set[str]) -> None:
    assert shown(client, f"?status={status}") == emails


@pytest.mark.parametrize(
    ("query", "emails"),
    [
        ("northfield", {"priya@northfield.example"}),  # part of an address
        ("EASTGATE", {"sam@eastgate.example"}),  # any case
        ("partner at", {"sam@eastgate.example"}),  # the note
        ("seed deck", {"priya@northfield.example", "tom@harbourcap.example"}),  # a document
        ("series a", {"sam@eastgate.example"}),  # a room
        ("nobody", set()),
    ],
)
def test_links_are_found_by_a_search(client: Client, query: str, emails: set[str]) -> None:
    assert shown(client, f"?q={query}") == emails


def test_a_link_is_found_by_its_reference(client: Client, links: dict[str, Link]) -> None:
    ref = links["expired"].ref
    assert shown(client, f"?q={ref.lower()}") == {"lena@foxglove.example"}
    assert shown(client, f"?q=L-{ref}") == {"lena@foxglove.example"}


def test_status_and_search_combine(client: Client) -> None:
    assert shown(client, "?status=active&q=seed") == {"priya@northfield.example"}


def test_the_form_keeps_what_was_chosen(client: Client) -> None:
    page = client.get("/admin/links/?status=revoked&q=harbour", secure=True).content.decode()
    assert '<option value="revoked" selected>' in page
    assert 'value="harbour"' in page
    assert 'href="/admin/links/">Clear</a>' in page


def test_no_match_says_so(client: Client) -> None:
    page = client.get("/admin/links/?q=nobody", secure=True).content.decode()
    assert "No links match that filter" in page
    assert "No links yet" not in page
