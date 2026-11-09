"""People: everyone who has opened anything, with what they opened, in the same strips."""

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
    assert "Time on each page or part" in page and 'class="legend"' in page


def test_the_people_page_before_anyone_reads() -> None:
    page = admin_client().get("/admin/people/", secure=True).content.decode()
    assert "Nobody has opened anything yet" in page


def test_people_need_a_signed_in_admin() -> None:
    assert Client().get("/admin/people/", secure=True).status_code == 302


# ---------------------------------------------------------------- downloads
#
# Asked for 6 November 2026: the People page flags documents a person has
# downloaded, and how many times. Downloads are recorded in the audit log.


def downloaded(email: str, document: Document, times: int = 1) -> None:
    from snug.audit.models import AuditEvent, record

    for _ in range(times):
        record(
            "viewer.downloaded",
            actor_type=AuditEvent.ActorType.VIEWER,
            actor=email,
            target=email,
            details={"link": "L-ABCD", "document": document.token, "version": 1},
        )


@pytest.mark.usefixtures("library")
def test_downloads_are_counted_per_person_and_document() -> None:
    seed = Document.objects.get(title="Seed deck")
    model = Document.objects.get(title="Financial model")
    downloaded("priya@northfield.example", seed, times=2)
    downloaded("priya@northfield.example", model)
    downloaded("sam@eastgate.example", model)  # never read it: not on Sam's rows
    sam, priya = summary.for_people()
    assert [(d.title, d.downloads) for d in priya.documents] == [
        ("Financial model", 1),
        ("Seed deck", 2),
    ]
    assert priya.downloads == 3
    assert [(d.title, d.downloads) for d in sam.documents] == [("Seed deck", 0)]
    assert sam.downloads == 0


@pytest.mark.usefixtures("library")
def test_the_people_page_shows_downloads() -> None:
    downloaded("priya@northfield.example", Document.objects.get(title="Seed deck"), times=2)
    page = admin_client().get("/admin/people/", secure=True).content.decode()
    assert '<div class="matrix with-downloads">' in page
    assert '<div class="cell r">Downloads</div>' in page
    priya = page[page.index("<h2>priya@northfield.example</h2>") :]
    assert "2 min 50 s in all, 2 downloads, last seen" in priya
    seed_row = priya[priya.index("Seed deck") :]
    seed_row = seed_row[: seed_row.index('class="cell r last"')]
    count = '<div class="cell r count downloaded">2<span class="visually-hidden"> downloads</span>'
    assert count in seed_row
    sam = page[page.index("<h2>sam@eastgate.example</h2>") : page.index("<h2>priya")]
    assert "downloads, last seen" not in sam  # no downloads, no mention
    assert '<div class="cell r count">0<span class="visually-hidden"> downloads</span>' in sam
