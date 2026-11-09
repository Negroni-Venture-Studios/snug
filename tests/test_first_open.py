"""First-open emails: the first time a link shows anyone a document, every admin hears.

Each admin can turn their own off under Your account in Settings. The email
says who, what, when and where (the country), with a link to that person on
the People page. A link is claimed once, so two pages loading together still
send one email.
"""

import time
from collections.abc import Callable
from typing import Any

import pytest
from django.core import mail as django_mail
from django.test import Client

from snug.accounts import sessions as admin_sessions
from snug.accounts.models import Admin
from snug.analytics import notify, summary
from snug.audit.models import AuditEvent
from snug.core import geoip, mail
from snug.documents.models import Document, DocumentVersion
from snug.links import service
from snug.links.models import Link
from snug.rooms.models import Room, RoomDocument

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("email_on")]

INVESTOR = "priya@northfield.example"


@pytest.fixture(autouse=True)
def at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Send in the request, so the test can read the outbox."""
    monkeypatch.setattr(mail, "in_background", lambda task: task())


@pytest.fixture(autouse=True)
def from_the_uk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(geoip, "country", lambda address: "GB")


@pytest.fixture
def admins() -> list[Admin]:
    return [
        Admin.objects.create(email="ruth@fernlea.example", is_active=True),
        Admin.objects.create(email="omar@fernlea.example", is_active=True),
        Admin.objects.create(email="gone@fernlea.example", is_active=False),
    ]


def deck() -> Document:
    document = Document.objects.create(title="Seed deck")
    DocumentVersion.objects.create(
        document=document,
        number=1,
        stored_name="x.pdf",
        size_bytes=1,
        sha256="0" * 64,
        page_count=3,
        page_sizes=[[16, 9]] * 3,
    )
    return document


def open_link(*, room: Room | None = None, document: Document | None = None, **extra: Any) -> str:
    issued = service.create(
        document=document,
        room=room,
        email=INVESTOR,
        note="Partner at Northfield Capital",
        expires_at=None,
        require_verification=False,
        allow_download=True,
        created_by=None,
        **extra,
    )
    return issued.token


def read(token: str, path: str = "", visitor: Client | None = None) -> Client:
    """Open the link as the investor and show a page of the document."""
    visitor = visitor or Client()
    visitor.post(f"/v/{token}/open", secure=True)
    response = visitor.get(f"/v/{token}{path}", secure=True)
    assert response.status_code == 200
    return visitor


def first_opens() -> list[Any]:
    return [m for m in django_mail.outbox if "opened" in m.subject]


@pytest.mark.usefixtures("admins")
def test_every_working_admin_hears_once() -> None:
    token = open_link(document=deck())
    visitor = read(token)
    read(token, visitor=visitor)  # the same visit
    read(token)  # another browser, the same link
    sent = first_opens()
    assert sorted(m.to[0] for m in sent) == ["omar@fernlea.example", "ruth@fernlea.example"]
    assert Link.objects.get().first_opened_at is not None


@pytest.mark.usefixtures("admins")
def test_each_link_has_its_own_first_open() -> None:
    document = deck()
    read(open_link(document=document))
    read(open_link(document=document))
    assert len(first_opens()) == 4


@pytest.mark.usefixtures("admins")
def test_what_the_email_says() -> None:
    from snug.core.models import SiteSettings

    row = SiteSettings.load()
    row.company_name = "Fernlea Robotics"
    row.save()
    room = Room.objects.create(name="Series A")
    document = deck()
    RoomDocument.objects.create(room=room, document=document)
    token = open_link(room=room)
    read(token, f"/d/{document.token}")
    message = first_opens()[0]
    assert message.subject == f"Fernlea Robotics: {INVESTOR} opened Seed deck"
    body = message.body
    assert f"Who: {INVESTOR} (Partner at Northfield Capital)" in body
    assert "What: Seed deck, in Series A" in body
    assert "Where: United Kingdom" in body
    assert "When: " in body
    anchor = summary.anchor(INVESTOR)
    assert f"/admin/people/#{anchor}" in body
    assert "/admin/settings/#notifications" in body
    assert "first time anyone has opened" in body
    html = message.alternatives[0][0]
    assert f'/admin/people/#{anchor}">' in html


@pytest.mark.usefixtures("admins")
def test_an_unknown_country_is_said_plainly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(geoip, "country", lambda address: None)
    read(open_link(document=deck()))
    assert "Where: Not known" in first_opens()[0].body


def test_an_admin_who_turned_it_off_hears_nothing(admins: list[Admin]) -> None:
    Admin.objects.filter(email="omar@fernlea.example").update(notify_first_open=False)
    read(open_link(document=deck()))
    assert [m.to[0] for m in first_opens()] == ["ruth@fernlea.example"]


def test_the_link_is_claimed_once() -> None:
    link = Link.objects.get(pk=Link.find(open_link(document=deck())).pk)  # type: ignore[union-attr]
    claims: list[Callable[[], bool]] = [lambda: notify.claim(link) for _ in range(3)]
    assert [claim() for claim in claims] == [True, False, False]


@pytest.mark.usefixtures("admins")
def test_a_visit_that_only_lists_a_room_is_not_an_open() -> None:
    room = Room.objects.create(name="Series A")
    RoomDocument.objects.create(room=room, document=deck())
    read(open_link(room=room))  # the room's index, no document shown
    assert not first_opens()


# ---------------------------------------------------------------- the switch


@pytest.fixture
def admin_client(admins: list[Admin]) -> Client:
    client = Client()
    client.force_login(admins[0])
    session = client.session
    session[admin_sessions.LOGIN_AT] = session[admin_sessions.LAST_ACTIVITY] = time.time()
    session.save()
    return client


def test_settings_offers_the_switch_on_by_default(admin_client: Client) -> None:
    page = admin_client.get("/admin/settings/", secure=True).content.decode()
    section = page[page.index('id="notifications"') :]
    assert 'action="/admin/settings/notifications#notifications"' in section
    assert '<input type="checkbox" name="first_open" checked>' in section


def test_turning_it_off_and_on(admin_client: Client) -> None:
    response = admin_client.post("/admin/settings/notifications", {}, secure=True)
    assert response["Location"] == "/admin/settings/?saved=notifications#notifications"
    assert not Admin.objects.get(email="ruth@fernlea.example").notify_first_open
    event = AuditEvent.objects.get(action="admin.notifications_changed")
    assert event.details == {"first_open": False}
    page = admin_client.get("/admin/settings/?saved=notifications", secure=True).content.decode()
    assert "Saved. You will not be emailed when someone first opens a link." in page

    admin_client.post("/admin/settings/notifications", {"first_open": "on"}, secure=True)
    assert Admin.objects.get(email="ruth@fernlea.example").notify_first_open


def test_the_people_page_has_the_anchor(admin_client: Client) -> None:
    read(open_link(document=deck()))
    page = admin_client.get("/admin/people/", secure=True).content.decode()
    assert f'id="{summary.anchor(INVESTOR)}"' in page
