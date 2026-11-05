"""Reading time: visits, pages shown, and time per page from heartbeats.

The brief: the viewer sends view started, page shown, a heartbeat while the
tab is visible, and view ended. Snug measures elapsed time itself, so a
browser cannot claim more time than really passed.
"""

from datetime import datetime, timedelta
from typing import Any

import pytest
from django.test import Client
from django.utils import timezone

from snug.analytics import reading
from snug.analytics.models import PageView, Visit
from snug.audit.models import AuditEvent
from snug.documents.models import Document, DocumentVersion
from snug.links import service
from snug.viewer import sessions

pytestmark = pytest.mark.django_db

INVESTOR = "priya@northfield.example"
START = timezone.make_aware(datetime(2026, 11, 5, 10, 0, 0))


class Clock:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.now = START
        monkeypatch.setattr("django.utils.timezone.now", lambda: self.now)

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    return Clock(monkeypatch)


@pytest.fixture
def document() -> Document:
    deck = Document.objects.create(title="Seed deck")
    DocumentVersion.objects.create(
        document=deck,
        number=1,
        size_bytes=1,
        sha256="0" * 64,
        page_count=3,
        page_sizes=[[960, 540]] * 3,
    )
    return deck


@pytest.fixture
def issued(document: Document) -> service.Issued:
    return service.create(
        document=document,
        email=INVESTOR,
        note="",
        expires_at=None,
        require_verification=False,
        allow_download=True,
        created_by=None,
    )


@pytest.fixture
def browser(issued: service.Issued, clock: Clock) -> Client:
    client = Client(REMOTE_ADDR="203.0.113.9")
    client.post(f"/v/{issued.token}/open", secure=True)
    assert sessions.COOKIE in client.cookies
    return client


def show(browser: Client, issued: service.Issued, page: int = 1) -> str:
    """Load the reader at a page; return the visit token the page carries."""
    html = browser.get(f"/v/{issued.token}?page={page}", secure=True).content.decode()
    start = html.index('data-visit="') + len('data-visit="')
    return html[start : html.index('"', start)]


def beat(browser: Client, issued: service.Issued, visit: str, page: int, kind: str = "beat") -> Any:
    data = {"visit": visit, "page": str(page), "kind": kind}
    return browser.post(f"/v/{issued.token}/beat", data, secure=True)


def seconds(page: int) -> float:
    return sum(v.seconds for v in PageView.objects.filter(page=page))


# ---------------------------------------------------------------- visits


def test_opening_the_reader_starts_a_visit(browser: Client, issued: service.Issued) -> None:
    token = show(browser, issued)
    visit = Visit.objects.get()
    assert visit.token == token and len(token) >= 22
    assert (visit.email, visit.link, visit.version_number) == (INVESTOR, issued.link, 1)
    assert PageView.objects.get().page == 1
    event = AuditEvent.objects.get(action="viewer.document_viewed")
    assert issued.link.document is not None
    assert event.actor == INVESTOR and event.details["document"] == issued.link.document.token


def test_turning_pages_stays_in_one_visit(
    browser: Client, issued: service.Issued, clock: Clock
) -> None:
    first = show(browser, issued, 1)
    clock.advance(40)
    second = show(browser, issued, 2)
    assert first == second
    assert Visit.objects.count() == 1
    assert sorted(PageView.objects.values_list("page", flat=True)) == [1, 2]
    assert AuditEvent.objects.filter(action="viewer.document_viewed").count() == 1


def test_coming_back_after_half_an_hour_is_a_new_visit(
    browser: Client, issued: service.Issued, clock: Clock
) -> None:
    show(browser, issued)
    clock.advance(reading.VISIT_GAP.total_seconds() + 1)
    show(browser, issued)
    assert Visit.objects.count() == 2


# ---------------------------------------------------------------- time


def test_heartbeats_credit_the_time_that_really_passed(
    browser: Client, issued: service.Issued, clock: Clock
) -> None:
    visit = show(browser, issued, 1)
    clock.advance(15)
    assert beat(browser, issued, visit, 1).status_code == 204
    clock.advance(15)
    beat(browser, issued, visit, 1)
    clock.advance(6)
    beat(browser, issued, visit, 1, kind="end")
    assert seconds(1) == pytest.approx(36)


def test_a_beat_never_credits_more_than_the_cap(
    browser: Client, issued: service.Issued, clock: Clock
) -> None:
    # A sleeping laptop, or a tab left open: the gap is not reading time.
    visit = show(browser, issued, 1)
    clock.advance(600)
    beat(browser, issued, visit, 1)
    assert seconds(1) == pytest.approx(reading.MAX_CREDIT.total_seconds())


def test_rapid_beats_cannot_inflate_time(
    browser: Client, issued: service.Issued, clock: Clock
) -> None:
    visit = show(browser, issued, 1)
    for _ in range(50):
        beat(browser, issued, visit, 1)
    assert seconds(1) == 0
    clock.advance(10)
    beat(browser, issued, visit, 1)
    assert seconds(1) == pytest.approx(10)


def test_time_hidden_is_not_counted(browser: Client, issued: service.Issued, clock: Clock) -> None:
    visit = show(browser, issued, 1)
    clock.advance(10)
    beat(browser, issued, visit, 1, kind="end")  # the tab was hidden
    clock.advance(300)
    beat(browser, issued, visit, 1, kind="resume")  # and shown again
    clock.advance(5)
    beat(browser, issued, visit, 1)
    assert seconds(1) == pytest.approx(15)


def test_time_goes_to_the_page_being_shown(
    browser: Client, issued: service.Issued, clock: Clock
) -> None:
    visit = show(browser, issued, 1)
    clock.advance(12)
    show(browser, issued, 2)  # turning the page credits page 1 first
    clock.advance(7)
    beat(browser, issued, visit, 2)
    assert seconds(1) == pytest.approx(12) and seconds(2) == pytest.approx(7)


def test_a_beat_for_another_page_credits_nothing(
    browser: Client, issued: service.Issued, clock: Clock
) -> None:
    visit = show(browser, issued, 1)
    clock.advance(10)
    beat(browser, issued, visit, 3)
    assert seconds(1) == 0 and seconds(3) == 0


# ---------------------------------------------------------------- refusals


@pytest.mark.parametrize(
    "data",
    [
        {"visit": "not-a-visit", "page": "1", "kind": "beat"},
        {"page": "1", "kind": "beat"},
        {"visit": "{visit}", "page": "x", "kind": "beat"},
        {"visit": "{visit}", "page": "1", "kind": "explode"},
    ],
)
def test_odd_beats_are_ignored(
    browser: Client, issued: service.Issued, clock: Clock, data: dict[str, str]
) -> None:
    visit = show(browser, issued, 1)
    clock.advance(10)
    data = {k: v.replace("{visit}", visit) for k, v in data.items()}
    response = browser.post(f"/v/{issued.token}/beat", data, secure=True)
    assert response.status_code == 400
    assert seconds(1) == 0


def test_one_links_visit_cannot_be_credited_through_another(
    browser: Client, issued: service.Issued, clock: Clock, document: Document
) -> None:
    visit = show(browser, issued, 1)
    other = service.create(
        document=document,
        email="sam@eastgate.example",
        note="",
        expires_at=None,
        require_verification=False,
        allow_download=True,
        created_by=None,
    )
    stranger = Client()
    stranger.post(f"/v/{other.token}/open", secure=True)
    clock.advance(10)
    assert beat(stranger, other, visit, 1).status_code == 400
    assert seconds(1) == 0


def test_beats_need_a_session(issued: service.Issued, browser: Client) -> None:
    visit = show(browser, issued, 1)
    response = Client().post(
        f"/v/{issued.token}/beat", {"visit": visit, "page": "1", "kind": "beat"}, secure=True
    )
    assert response.status_code in (303, 403)


def test_beats_after_revoking_are_refused(
    browser: Client, issued: service.Issued, clock: Clock
) -> None:
    visit = show(browser, issued, 1)
    service.revoke(issued.link, by=None)
    clock.advance(10)
    assert beat(browser, issued, visit, 1).status_code == 404
    assert seconds(1) == 0


def test_the_reader_carries_what_its_script_needs(browser: Client, issued: service.Issued) -> None:
    html = browser.get(f"/v/{issued.token}", secure=True).content.decode()
    assert f'data-beat-url="/v/{issued.token}/beat"' in html
    assert 'data-page="1"' in html and 'name="csrfmiddlewaretoken"' in html
    assert f'data-interval="{reading.BEAT_SECONDS}"' in html
