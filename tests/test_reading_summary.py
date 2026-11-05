"""The founder's view of how a document was read: the stat strip and the reading grid."""

import time
from datetime import datetime, timedelta

import pytest
from django.test import Client
from django.utils import timezone

from snug.accounts import sessions
from snug.accounts.models import Admin
from snug.analytics import charts, summary
from snug.analytics.models import PageView, Visit
from snug.documents.models import Document, DocumentVersion
from snug.links import service
from snug.links.models import Link

pytestmark = pytest.mark.django_db

WHEN = timezone.make_aware(datetime(2026, 11, 3, 14, 5))


@pytest.fixture
def document() -> Document:
    deck = Document.objects.create(title="Seed deck")
    DocumentVersion.objects.create(
        document=deck,
        number=1,
        size_bytes=1,
        sha256="0" * 64,
        page_count=4,
        page_sizes=[[960, 540]] * 4,
    )
    return deck


def link_for(document: Document, email: str, note: str = "") -> Link:
    return service.create(
        document=document,
        email=email,
        note=note,
        expires_at=None,
        require_verification=True,
        allow_download=True,
        created_by=None,
    ).link


def read(link: Link, seconds: dict[int, float], at: datetime = WHEN) -> Visit:
    """One visit to the link's document, with these seconds per page."""
    assert link.document is not None
    visit = Visit.objects.create(
        token=f"t{Visit.objects.count()}",
        link=link,
        email=link.email,
        document=link.document,
        version_number=1,
        started_at=at,
        last_seen_at=at,
        current_page=1,
        clock_at=at,
    )
    for page, secs in seconds.items():
        PageView.objects.create(visit=visit, page=page, seconds=secs, first_seen_at=at)
    return visit


@pytest.fixture
def readers(document: Document) -> None:
    priya = link_for(document, "priya@northfield.example", "Partner, Northfield")
    sam = link_for(document, "sam@eastgate.example")
    read(priya, {1: 30, 2: 50, 3: 100, 4: 20})
    read(priya, {1: 10, 4: 5}, at=WHEN + timedelta(days=1))
    read(sam, {1: 20, 2: 10})


# ---------------------------------------------------------------- the numbers


@pytest.mark.usefixtures("readers")
def test_the_summary_adds_up(document: Document) -> None:
    reading = summary.for_document(document)
    assert reading is not None
    assert reading.pages == 4
    assert (reading.viewer_count, reading.visits, reading.read_all) == (2, 3, 1)
    # Per viewer totals are 215 s and 30 s; the median of two is their mean.
    assert reading.median_seconds == 122
    priya, sam = reading.viewers  # most time first
    assert priya.email == "priya@northfield.example" and priya.note == "Partner, Northfield"
    assert priya.visits == 2 and priya.total_seconds == 215
    assert priya.seconds == [40, 50, 100, 25]
    assert priya.last_seen == WHEN + timedelta(days=1)
    assert sam.seconds == [20, 10, 0, 0] and sam.seen == [True, True, False, False]
    # Averages count only the viewers who reached each page.
    assert reading.averages == [30, 30, 100, 25]


def test_no_reading_yet(document: Document) -> None:
    assert summary.for_document(document) is None


@pytest.mark.usefixtures("readers")
def test_another_documents_reading_is_not_counted(document: Document) -> None:
    other = Document.objects.create(title="Board pack")
    read(link_for(other, "kim@westfield.example"), {1: 500})
    reading = summary.for_document(document)
    assert reading is not None and reading.viewer_count == 2


# ---------------------------------------------------------------- the charts


def test_durations_read_plainly() -> None:
    assert [charts.duration(s) for s in (0, 7, 60, 125, 3600)] == [
        "0 s",
        "7 s",
        "1 min",
        "2 min 5 s",
        "60 min",
    ]


def test_heat_levels_keep_seen_apart_from_unseen() -> None:
    assert charts.heat(0, seen=False) == 0
    assert charts.heat(0, seen=True) == 1  # seen, however briefly
    assert [charts.heat(s, seen=True) for s in (10, 30, 60, 200)] == [1, 2, 3, 4]


def test_a_strip_has_one_cell_per_page_with_its_value() -> None:
    svg = charts.strip([40, 0, 100], [True, False, True], "a<b>@example.com")
    assert svg.count("<rect") == 3
    # Room for at least 18 pages, so a short document's cells keep their size
    # instead of stretching across the column.
    assert 'viewBox="0 0 360 16"' in svg
    assert "<title>Page 2: not seen</title>" in svg and "<title>Page 3: 1 min 40 s</title>" in svg
    assert "a&lt;b&gt;@example.com: saw 2 of 3 pages" in svg
    assert " style=" not in svg


def test_bars_share_the_strips_coordinates() -> None:
    svg = charts.bars([30, 0, 100])
    assert 'viewBox="0 0 360 64"' in svg and svg.count("<rect") == 3
    assert "Page 3: 1 min 40 s on average" in svg


def test_a_one_page_document_is_one_small_cell() -> None:
    import re

    svg = charts.strip([90], [True], "a@b.example")
    assert 'viewBox="0 0 360 16"' in svg and svg.count("<rect") == 1
    assert 'width="18"' in svg  # one slot of eighteen, not the whole column
    spans = re.findall(r"<span>(\d*)</span>", charts.axis(1))
    assert len(spans) == 18 and spans[0] == "1" and not any(spans[1:])


def test_long_documents_label_every_few_pages() -> None:
    import re

    axis = charts.axis(40)
    spans = re.findall(r"<span>(\d*)</span>", axis)
    labels = [int(n) for n in spans if n]
    assert len(spans) == 40  # one column per page, so labels sit over their strips
    assert labels[0] == 1 and labels[-1] == 40 and len(labels) <= 12


# ---------------------------------------------------------------- the page


def admin_client() -> Client:
    admin = Admin.objects.create(email="ruth@fernlea.example", is_active=True)
    browser = Client()
    browser.force_login(admin)
    session = browser.session
    session[sessions.LOGIN_AT] = session[sessions.LAST_ACTIVITY] = time.time()
    session.save()
    return browser


@pytest.mark.usefixtures("readers")
def test_the_document_page_shows_how_it_was_read(document: Document) -> None:
    page = admin_client().get(f"/admin/documents/{document.token}", secure=True).content.decode()
    assert "How it was read" in page
    for figure in ('<div class="value">2</div>', '<div class="value">3</div>'):
        assert figure in page
    assert "2 min <small>" not in page  # durations in the strip use the stat format
    assert "priya@northfield.example" in page and "Partner, Northfield" in page
    assert 'class="strip"' in page and 'class="bars"' in page
    assert "Longest average time: page 3 (1 min 40 s)" in page


def test_an_unread_document_says_so(document: Document) -> None:
    page = admin_client().get(f"/admin/documents/{document.token}", secure=True).content.decode()
    assert "Nobody has opened this document yet" in page
    assert 'class="strip"' not in page
