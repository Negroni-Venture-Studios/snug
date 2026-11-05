"""The Overview: a stat strip and recent activity, first opens marked New.

DESIGN.md section 2. The status line arrives with `snug doctor` (step 7).
"""

import time
from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from snug.accounts import sessions
from snug.accounts.models import Admin
from snug.analytics.models import PageView, Visit
from snug.documents.models import Document
from snug.links import service
from snug.links.models import Link
from snug.rooms.models import Room, RoomDocument

pytestmark = pytest.mark.django_db


@pytest.fixture
def client() -> Client:
    admin = Admin.objects.create(email="ruth@fernlea.example", is_active=True)
    browser = Client()
    browser.force_login(admin)
    session = browser.session
    session[sessions.LOGIN_AT] = session[sessions.LAST_ACTIVITY] = time.time()
    session.save()
    return browser


def overview(client: Client) -> str:
    response = client.get("/admin/", secure=True)
    assert response.status_code == 200
    return response.content.decode()


def make_link(email: str, *, document: Document | None = None, room: Room | None = None) -> Link:
    return service.create(
        document=document,
        room=room,
        email=email,
        note="",
        expires_at=None,
        require_verification=False,
        allow_download=True,
        created_by=None,
    ).link


def visit(link: Link, document: Document, *, ago: timedelta, seconds: float) -> Visit:
    started = timezone.now() - ago
    record = Visit.objects.create(
        token=f"t{Visit.objects.count()}",
        link=link,
        email=link.email,
        document=document,
        version_number=1,
        started_at=started,
        last_seen_at=started,
        current_page=1,
        clock_at=started,
    )
    PageView.objects.create(visit=record, page=1, seconds=seconds, first_seen_at=started)
    return record


def test_an_empty_instance_says_how_to_start(client: Client) -> None:
    page = overview(client)
    assert "Nothing to show yet" in page
    empty = page[page.index('<div class="empty">') :]
    assert 'href="/admin/documents/new"' in empty


def test_the_stat_strip_counts_what_is_there(client: Client) -> None:
    deck = Document.objects.create(title="Seed deck")
    Document.objects.create(title="Cap table")
    Document.objects.create(title="Financial model")
    priya = make_link("priya@northfield.example", document=deck)
    make_link("tom@ashgrove.example", document=deck)
    gone = make_link("old@ashgrove.example", document=deck)
    Link.objects.filter(pk=gone.pk).update(revoked_at=timezone.now())
    lapsed = make_link("late@ashgrove.example", document=deck)
    Link.objects.filter(pk=lapsed.pk).update(expires_at=timezone.now() - timedelta(days=1))
    visit(priya, deck, ago=timedelta(days=1), seconds=40)
    visit(priya, deck, ago=timedelta(days=2), seconds=40)
    visit(lapsed, deck, ago=timedelta(days=20), seconds=40)  # outside the week

    page = overview(client)
    assert "Nothing to show yet" not in page
    stats = page[page.index('<div class="stats">') :]
    assert '<div class="value">3</div><div class="label">Documents</div>' in stats
    assert '<div class="value">2</div><div class="label">Active links</div>' in stats
    assert '<div class="value">2</div><div class="label">People who have read</div>' in stats
    assert '<div class="value">2</div><div class="label">Visits in the last 7 days</div>' in stats


def test_recent_activity_lists_visits_newest_first_and_marks_first_opens(client: Client) -> None:
    deck = Document.objects.create(title="Seed deck")
    model = Document.objects.create(title="Financial model")
    room = Room.objects.create(name="Series A room")
    RoomDocument.objects.create(room=room, document=model)
    priya = make_link("priya@northfield.example", document=deck)
    tom = make_link("tom@ashgrove.example", room=room)
    visit(priya, deck, ago=timedelta(days=3), seconds=30)  # Priya's first
    visit(tom, model, ago=timedelta(hours=5), seconds=125)  # Tom's first
    visit(priya, deck, ago=timedelta(hours=1), seconds=90)  # Priya again

    page = overview(client)
    activity = page[page.index("Recent activity") :]
    rows = activity.split('<div class="item">')[1:]
    assert len(rows) == 3
    assert "priya@northfield.example" in rows[0] and "Seed deck" in rows[0]
    assert 'class="new"' not in rows[0]  # she had opened it before
    assert "tom@ashgrove.example" in rows[1] and "Financial model" in rows[1]
    assert "in Series A room" in rows[1]
    assert '<span class="new">New</span>' in rows[1]
    assert "2 min 5 s" in rows[1]
    assert '<span class="new">New</span>' in rows[2]
    assert f'href="/admin/documents/{deck.token}"' in rows[0]
    assert 'href="/admin/people/"' in activity


def test_recent_activity_stops_at_ten(client: Client) -> None:
    deck = Document.objects.create(title="Seed deck")
    link = make_link("priya@northfield.example", document=deck)
    for hours in range(12):
        visit(link, deck, ago=timedelta(hours=hours), seconds=10)
    activity = overview(client).split("Recent activity")[1]
    assert activity.count('<div class="item">') == 10


def test_documents_without_visits_say_nobody_has_read_yet(client: Client) -> None:
    deck = Document.objects.create(title="Seed deck")
    make_link("priya@northfield.example", document=deck)
    page = overview(client)
    assert "Nobody has opened a link yet." in page


def test_the_overview_needs_a_login() -> None:
    response = Client().get("/admin/", secure=True)
    assert response.status_code == 302
