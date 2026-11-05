"""People: everyone who has read anything, with what they read, in the same strips."""

import time
from datetime import datetime, timedelta

import pytest
from django.test import Client
from django.utils import timezone

from snug.accounts import sessions
from snug.accounts.models import Admin
from snug.analytics import summary
from snug.documents.models import Document, DocumentVersion
from tests.test_reading_summary import link_for, read

pytestmark = pytest.mark.django_db

WHEN = timezone.make_aware(datetime(2026, 11, 3, 14, 5))


def deck(title: str, pages: int) -> Document:
    document = Document.objects.create(title=title)
    DocumentVersion.objects.create(
        document=document,
        number=1,
        size_bytes=1,
        sha256="0" * 64,
        page_count=pages,
        page_sizes=[[960, 540]] * pages,
    )
    return document


@pytest.fixture
def library() -> None:
    seed = deck("Seed deck", 3)
    model = deck("Financial model", 2)
    priya_seed = link_for(seed, "priya@northfield.example", "Partner, Northfield")
    priya_model = link_for(model, "priya@northfield.example")
    sam_seed = link_for(seed, "sam@eastgate.example")
    read(priya_seed, {1: 30, 2: 40}, at=WHEN)
    read(priya_model, {1: 90, 2: 10}, at=WHEN + timedelta(hours=2))
    read(sam_seed, {1: 5}, at=WHEN + timedelta(days=1))
    link_for(seed, "kim@westfield.example")  # sent, never opened


@pytest.mark.usefixtures("library")
def test_people_are_grouped_by_email() -> None:
    people = summary.for_people()
    assert [p.email for p in people] == ["sam@eastgate.example", "priya@northfield.example"]
    sam, priya = people
    assert priya.note == "Partner, Northfield"
    assert priya.last_seen == WHEN + timedelta(hours=2)
    assert priya.total_seconds == 170
    assert [(d.title, d.visits, d.seconds) for d in priya.documents] == [
        ("Financial model", 1, [90, 10]),
        ("Seed deck", 1, [30, 40, 0]),
    ]
    assert sam.documents[0].seen == [True, False, False]


def test_nobody_yet() -> None:
    assert summary.for_people() == []


def admin_client() -> Client:
    admin = Admin.objects.create(email="ruth@fernlea.example", is_active=True)
    browser = Client()
    browser.force_login(admin)
    session = browser.session
    session[sessions.LOGIN_AT] = session[sessions.LAST_ACTIVITY] = time.time()
    session.save()
    return browser


@pytest.mark.usefixtures("library")
def test_the_people_page() -> None:
    page = admin_client().get("/admin/people/", secure=True).content.decode()
    assert 'aria-current="page">People</a>' in page
    assert page.index("sam@eastgate.example") < page.index("priya@northfield.example")
    assert "Financial model" in page and page.count('class="strip"') == 3
    assert "kim@westfield.example" not in page  # never opened anything
    assert "2 min 50 s" in page  # Priya's total across both documents
    assert "Time on each page" in page and 'class="legend"' in page


def test_the_people_page_before_anyone_reads() -> None:
    page = admin_client().get("/admin/people/", secure=True).content.decode()
    assert "Nobody has opened anything yet" in page


def test_people_need_a_signed_in_admin() -> None:
    assert Client().get("/admin/people/", secure=True).status_code == 302
