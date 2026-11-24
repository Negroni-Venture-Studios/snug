"""The weekly digest: a summary of the week's reading, emailed on Monday mornings (#154).

Each admin turns it on under Your account in Settings; it is off until they
do. Housekeeping, which runs every hour, sends it once a week to each admin
who wants it, and an empty week sends nothing.
"""

import time
from datetime import UTC, datetime, timedelta
from io import StringIO
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from django.core import mail as django_mail
from django.core.mail import EmailMessage, EmailMultiAlternatives
from django.core.management import call_command
from django.test import Client

from snug.accounts import sessions as admin_sessions
from snug.accounts.models import Admin
from snug.analytics import digest
from snug.analytics.models import PageView, Visit
from snug.audit.models import AuditEvent, record
from snug.core import mail
from snug.documents.models import Document
from snug.links.models import Link
from tests.test_overview import make_link

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("email_on")]

LONDON = ZoneInfo("Europe/London")
# Wednesday 25 November 2026, 10:00 in London: the week began on Monday the 23rd at 08:00.
WEDNESDAY = datetime(2026, 11, 25, 10, 0, tzinfo=LONDON)


@pytest.fixture
def ruth() -> Admin:
    return Admin.objects.create(
        email="ruth@fernlea.example", is_active=True, notify_weekly_digest=True
    )


def read(link: Link, document: Document, at: datetime, seconds: float, page: int = 1) -> None:
    visit = Visit.objects.create(
        token=f"t{Visit.objects.count()}",
        link=link,
        email=link.email,
        document=document,
        version_number=1,
        started_at=at,
        last_seen_at=at + timedelta(seconds=seconds),
        current_page=page,
        clock_at=at,
    )
    PageView.objects.create(visit=visit, page=page, seconds=seconds, first_seen_at=at)


def a_week_of_reading(now: datetime) -> dict[str, Link]:
    deck = Document.objects.create(title="Seed deck")
    model = Document.objects.create(title="Financial model")
    priya = make_link("priya@northfield.example", document=deck)
    priya.note = "Partner, Northfield"
    priya.first_opened_at = now - timedelta(days=2)
    priya.save()
    sam = make_link("sam@eastgate.example", document=deck)
    sam.first_opened_at = now - timedelta(days=30)  # opened before this week
    sam.save()
    lena = make_link("lena@foxglove.example", document=model)
    lena.expires_at = now + timedelta(days=3)
    lena.save()
    read(priya, deck, now - timedelta(days=2), 200)
    read(priya, deck, now - timedelta(days=1), 45, page=2)
    read(sam, deck, now - timedelta(days=3), 70)
    read(sam, model, now - timedelta(days=12), 999)  # last week's: not in this digest
    record(
        "viewer.downloaded",
        actor_type=AuditEvent.ActorType.VIEWER,
        actor="priya@northfield.example",
        target="priya@northfield.example",
        details={"link": priya.label},
    )
    return {"priya": priya, "sam": sam, "lena": lena}


def html_of(message: EmailMessage) -> str:
    assert isinstance(message, EmailMultiAlternatives)
    return str(message.alternatives[0][0])


# ---------------------------------------------------------------- when it is due


@pytest.mark.parametrize(
    ("start", "end", "text"),
    [
        (datetime(2026, 11, 18, 10, tzinfo=LONDON), WEDNESDAY, "18 to 25 November 2026"),
        (
            datetime(2026, 10, 28, 9, tzinfo=LONDON),
            datetime(2026, 11, 4, 9, tzinfo=LONDON),
            "28 October to 4 November 2026",
        ),
        (
            datetime(2026, 12, 29, 9, tzinfo=LONDON),
            datetime(2027, 1, 5, 9, tzinfo=LONDON),
            "29 December 2026 to 5 January 2027",
        ),
    ],
)
def test_the_period_is_written_out(start: datetime, end: datetime, text: str) -> None:
    assert digest.period(start, end) == text


@pytest.mark.parametrize(
    ("now", "start"),
    [
        (WEDNESDAY, datetime(2026, 11, 23, 8, 0, tzinfo=LONDON)),
        (datetime(2026, 11, 23, 8, 0, tzinfo=LONDON), datetime(2026, 11, 23, 8, 0, tzinfo=LONDON)),
        (datetime(2026, 11, 23, 7, 59, tzinfo=LONDON), datetime(2026, 11, 16, 8, 0, tzinfo=LONDON)),
        (datetime(2026, 11, 29, 23, 0, tzinfo=LONDON), datetime(2026, 11, 23, 8, 0, tzinfo=LONDON)),
        # In summer time too: 08:00 in London is 07:00 UTC.
        (datetime(2026, 7, 8, 12, 0, tzinfo=UTC), datetime(2026, 7, 6, 8, 0, tzinfo=LONDON)),
    ],
)
def test_the_week_starts_on_monday_at_eight(now: datetime, start: datetime) -> None:
    assert digest.week_start(now) == start


def test_it_is_off_until_an_admin_turns_it_on() -> None:
    assert not Admin.objects.create(email="omar@fernlea.example").notify_weekly_digest


def test_it_goes_once_a_week_to_admins_who_want_it(ruth: Admin) -> None:
    Admin.objects.create(email="omar@fernlea.example", is_active=True)  # left off
    Admin.objects.create(email="gone@fernlea.example", is_active=False, notify_weekly_digest=True)
    a_week_of_reading(WEDNESDAY)

    assert digest.send_due(WEDNESDAY) == 1
    assert [message.to for message in django_mail.outbox] == [["ruth@fernlea.example"]]
    assert digest.send_due(WEDNESDAY + timedelta(hours=1)) == 0
    assert digest.send_due(WEDNESDAY + timedelta(days=4)) == 0  # Sunday: still this week
    assert digest.send_due(WEDNESDAY + timedelta(days=5)) == 1  # the next Monday, 10:00
    assert len(django_mail.outbox) == 2


def test_an_empty_week_sends_nothing(ruth: Admin) -> None:
    assert digest.send_due(WEDNESDAY) == 0
    assert not django_mail.outbox
    ruth.refresh_from_db()
    assert ruth.digest_sent_at == WEDNESDAY  # and it is not tried again this week


def test_without_email_it_waits(ruth: Admin, monkeypatch: pytest.MonkeyPatch) -> None:
    a_week_of_reading(WEDNESDAY)
    monkeypatch.setattr(mail, "enabled", lambda: False)
    assert digest.send_due(WEDNESDAY) == 0
    ruth.refresh_from_db()
    assert ruth.digest_sent_at is None


def test_a_failed_send_is_tried_again(ruth: Admin, monkeypatch: pytest.MonkeyPatch) -> None:
    a_week_of_reading(WEDNESDAY)
    working = mail.send
    tries: list[str] = []

    def fails_once(**kwargs: Any) -> bool:
        tries.append(kwargs["to"])
        return len(tries) > 1 and working(**kwargs)

    monkeypatch.setattr(mail, "send", fails_once)
    assert digest.send_due(WEDNESDAY) == 0
    ruth.refresh_from_db()
    assert ruth.digest_sent_at is None
    assert digest.send_due(WEDNESDAY + timedelta(hours=1)) == 1
    assert len(django_mail.outbox) == 1


# ---------------------------------------------------------------- what it says


def test_it_summarises_the_week(ruth: Admin) -> None:
    # About now, since the audit log records the download at the real time.
    now = datetime.now(UTC) + timedelta(seconds=5)
    links = a_week_of_reading(now)
    digest.send_due(now)
    message = django_mail.outbox[0]
    body = message.body
    assert message.subject == f"Snug: your week, {digest.period(now - timedelta(days=7), now)}"
    assert "2 people read 1 document, for 5 min 15 s in all." in body
    assert "Seed deck: 2 people, 5 min 15 s" in body
    assert "priya@northfield.example (Partner, Northfield), 4 min 5 s" in body
    assert "sam@eastgate.example, 1 min 10 s" in body
    assert "Financial model" not in body.split("Links that expire")[0]  # last week's reading
    assert "Opened for the first time" in body
    assert f"priya@northfield.example, {links['priya'].label}" in body
    assert (
        f"sam@eastgate.example, {links['sam'].label}"
        not in body.split("Downloads")[0].split("Opened for the first time")[1]
    )
    assert "Downloads: 1" in body
    assert "Links that expire in the next 7 days" in body
    last = links["lena"].last_day
    assert last is not None
    expiring = f"lena@foxglove.example, {links['lena'].label}, Financial model"
    assert f"{expiring}, last day {last.day} {last:%B}" in body
    assert "/admin/settings/#notifications" in body
    html = html_of(message)
    assert "Seed deck" in html and "<script" not in html


def test_reading_that_names_html_is_escaped(ruth: Admin) -> None:
    deck = Document.objects.create(title="<b>Deck</b>")
    link = make_link("priya@northfield.example", document=deck)
    read(link, deck, WEDNESDAY - timedelta(days=1), 30)
    digest.send_due(WEDNESDAY)
    html = html_of(django_mail.outbox[0])
    assert "&lt;b&gt;Deck&lt;/b&gt;" in html and "<b>Deck</b>" not in html


# ---------------------------------------------------------------- housekeeping


def test_housekeeping_sends_it(ruth: Admin, monkeypatch: pytest.MonkeyPatch) -> None:
    a_week_of_reading(WEDNESDAY)
    monkeypatch.setattr("django.utils.timezone.now", lambda: WEDNESDAY)
    out = StringIO()
    call_command("housekeeping", stdout=out)
    assert "Weekly digest: 1 sent." in out.getvalue()
    assert "ruth" not in out.getvalue()
    assert len(django_mail.outbox) == 1


# ---------------------------------------------------------------- the switch


@pytest.fixture
def admin_client(ruth: Admin) -> Client:
    Admin.objects.filter(pk=ruth.pk).update(notify_weekly_digest=False)
    client = Client()
    client.force_login(ruth)
    session = client.session
    session[admin_sessions.LOGIN_AT] = session[admin_sessions.LAST_ACTIVITY] = time.time()
    session.save()
    return client


def test_settings_offers_the_switch_off_by_default(admin_client: Client) -> None:
    page = admin_client.get("/admin/settings/", secure=True).content.decode()
    section = page[page.index('id="notifications"') :]
    assert '<input type="checkbox" name="weekly_digest">' in section
    assert "Email me a summary of the week" in section


def test_turning_it_on_and_off(admin_client: Client) -> None:
    before = datetime.now(UTC)
    data = {"first_open": "on", "weekly_digest": "on"}
    response = admin_client.post("/admin/settings/notifications", data, secure=True)
    assert response["Location"] == "/admin/settings/?saved=notifications#notifications"
    ruth = Admin.objects.get(email="ruth@fernlea.example")
    assert ruth.notify_weekly_digest
    # The first one covers a whole week: it comes on the Monday after next 08:00.
    assert ruth.digest_sent_at is not None and ruth.digest_sent_at >= before
    event = AuditEvent.objects.get(action="admin.notifications_changed")
    assert event.details == {"weekly_digest": True}
    page = admin_client.get("/admin/settings/?saved=notifications", secure=True).content.decode()
    assert "You will get a summary of the week's reading every Monday morning." in page

    admin_client.post("/admin/settings/notifications", {"first_open": "on"}, secure=True)
    assert not Admin.objects.get(email="ruth@fernlea.example").notify_weekly_digest
    page = admin_client.get("/admin/settings/?saved=notifications", secure=True).content.decode()
    assert "You will not get the weekly summary." in page
