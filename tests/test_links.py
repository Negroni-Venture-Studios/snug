"""Links: hashed tokens, shown once, emailed, revoked and reissued."""

import smtplib
import time
from datetime import timedelta
from typing import Any

import pytest
from django.core import mail as django_mail
from django.test import Client
from django.utils import timezone

from snug.accounts import sessions
from snug.accounts.models import Admin
from snug.audit.models import AuditEvent
from snug.documents.models import Document
from snug.links.models import Link, hash_token
from snug.links.views import ISSUED, ISSUED_COOKIE

pytestmark = pytest.mark.django_db

EMAIL = "ruth@fernlea.example"
INVESTOR = "priya@northfield.example"


@pytest.fixture
def admin() -> Admin:
    return Admin.objects.create(email=EMAIL, is_active=True)


@pytest.fixture
def client(admin: Admin) -> Client:
    browser = Client(REMOTE_ADDR="198.51.100.7")
    browser.force_login(admin)
    session = browser.session
    session[sessions.LOGIN_AT] = session[sessions.LAST_ACTIVITY] = time.time()
    session.save()
    return browser


@pytest.fixture
def document() -> Document:
    return Document.objects.create(title="Seed deck")


def create(client: Client, document: Document, **fields: str) -> Any:
    data = {"email": INVESTOR, "note": "Partner, Northfield", "require_verification": "on"}
    data.update(fields)
    return client.post(f"/admin/links/for/{document.token}", data, secure=True)


def issued_url(client: Client) -> str:
    page = client.get("/admin/links/issued", secure=True).content.decode()
    start = page.index('id="issued-link">') + len('id="issued-link">')
    return page[start : page.index("</span>", start)]


def token_of(url: str) -> str:
    return url.rsplit("/v/", 1)[1]


# ---------------------------------------------------------------- the model


def test_only_a_hash_of_the_token_is_stored(client: Client, document: Document) -> None:
    create(client, document)
    url = issued_url(client)
    token = token_of(url)
    link = Link.objects.get()
    assert len(token) >= 43  # 256 bits
    assert link.token_hash == hash_token(token)
    assert Link.find(token) == link
    for value in Link.objects.values().get().values():
        assert token not in str(value)
    for event in AuditEvent.objects.all():
        assert token not in str(event.details) and token not in event.target


def test_the_token_never_reaches_the_session_store(client: Client, document: Document) -> None:
    # Between creating a link and showing it, the session row is in the
    # database, and so in backups. It must hold nothing that opens the link.
    from django.contrib.sessions.models import Session

    create(client, document)
    stored = [s.get_decoded() for s in Session.objects.all()]
    url = issued_url(client)
    token = token_of(url)
    assert stored and all(token not in str(data) for data in stored)
    for row in Session.objects.values_list("session_data", flat=True):
        assert token not in row


def test_the_issued_page_needs_the_browsers_half(client: Client, document: Document) -> None:
    create(client, document)
    client.cookies.pop(ISSUED_COOKIE)
    response = client.get("/admin/links/issued", secure=True)
    assert response.status_code == 302 and response["Location"] == "/admin/links/"
    assert ISSUED not in client.session


@pytest.mark.parametrize("pad", ["not base64!", "AAAA"])
def test_an_altered_browsers_half_shows_nothing(
    client: Client, document: Document, pad: str
) -> None:
    create(client, document)
    client.cookies[ISSUED_COOKIE] = pad
    response = client.get("/admin/links/issued", secure=True)
    assert response.status_code == 302 and response["Location"] == "/admin/links/"
    assert ISSUED not in client.session


def test_the_browsers_half_is_a_strict_short_lived_cookie(
    client: Client, document: Document
) -> None:
    response = create(client, document)
    cookie = response.cookies[ISSUED_COOKIE]
    assert cookie["httponly"] and cookie["samesite"] == "Strict"
    assert 0 < int(cookie["max-age"]) <= 600
    issued_url(client)
    assert client.cookies[ISSUED_COOKIE].value == ""  # deleted once used


def test_find_refuses_nonsense() -> None:
    assert Link.find("") is None
    assert Link.find("x" * 500) is None
    assert Link.find("not-a-real-token") is None


def test_states(document: Document) -> None:
    link = Link(ref="AB23", email=INVESTOR, document=document)
    assert link.is_active() and link.status == "active" and link.label == "L-AB23"
    link.expires_at = timezone.now() - timedelta(seconds=1)
    assert link.is_expired() and not link.is_active() and link.status == "expired"
    link.revoked_at = timezone.now()
    assert link.status == "revoked"


# ---------------------------------------------------------------- creating


def test_creating_a_link_shows_it_once(client: Client, document: Document) -> None:
    response = create(client, document)
    assert response.status_code == 302
    assert response["Location"] == "/admin/links/issued"
    url = issued_url(client)
    assert url.startswith("https://snug.example.com/v/")
    assert ISSUED not in client.session
    again = client.get("/admin/links/issued", secure=True)
    assert again["Location"] == "/admin/links/"


def test_link_settings_are_saved(client: Client, document: Document) -> None:
    tomorrow = timezone.localdate() + timedelta(days=1)
    create(client, document, allow_download="on", expires=tomorrow.isoformat())
    link = Link.objects.get()
    assert link.email == INVESTOR
    assert link.note == "Partner, Northfield"
    assert link.require_verification and link.allow_download
    assert link.expires_at is not None
    # The link works to the end of the chosen day, in the instance's time zone.
    assert timezone.localtime(link.expires_at).date() == tomorrow + timedelta(days=1)
    assert timezone.localtime(link.expires_at).hour == 0


def test_downloads_are_allowed_by_default(client: Client, document: Document) -> None:
    page = client.get(f"/admin/links/for/{document.token}", secure=True).content.decode()
    assert '<input type="checkbox" name="allow_download" checked>' in page
    assert "The original PDF, stamped with their email address" in page
    assert Link(email=INVESTOR, document=document).allow_download


def test_downloads_can_be_turned_off(client: Client, document: Document) -> None:
    create(client, document)  # the form posts no allow_download when it is unticked
    assert not Link.objects.get().allow_download


def test_verification_can_be_turned_off(client: Client, document: Document) -> None:
    client.post(f"/admin/links/for/{document.token}", {"email": INVESTOR}, secure=True)
    assert not Link.objects.get().require_verification


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"email": "not-an-email"}, "Enter the email address"),
        ({"expires": "2020-01-01"}, "must be in the future"),
        ({"expires": "next tuesday"}, "Enter the expiry as a date"),
    ],
)
def test_bad_details_are_refused(
    client: Client, document: Document, fields: dict[str, str], message: str
) -> None:
    response = create(client, document, **fields)
    assert response.status_code == 400
    assert message in response.content.decode()
    assert not Link.objects.exists()


def test_creating_a_link_is_audited(client: Client, document: Document) -> None:
    create(client, document)
    event = AuditEvent.objects.get(action="link.created")
    link = Link.objects.get()
    assert (event.actor, event.target) == (EMAIL, INVESTOR)
    assert event.details["link"] == link.label
    assert event.details["verification"] is True


def test_links_need_a_signed_in_admin_and_csrf(admin: Admin, document: Document) -> None:
    assert Client().get(f"/admin/links/for/{document.token}", secure=True).status_code == 302
    strict = Client(enforce_csrf_checks=True)
    strict.force_login(admin)
    response = strict.post(f"/admin/links/for/{document.token}", {"email": INVESTOR}, secure=True)
    assert response.status_code == 403
    assert not Link.objects.exists()


def test_unknown_documents_are_not_found(client: Client) -> None:
    assert client.get("/admin/links/for/nope", secure=True).status_code == 404


# ---------------------------------------------------------------- email


@pytest.mark.usefixtures("email_on")
def test_create_and_email(client: Client, document: Document) -> None:
    create(client, document, action="email")
    url = issued_url(client)
    assert len(django_mail.outbox) == 1
    message = django_mail.outbox[0]
    assert message.to == [INVESTOR]
    assert message.reply_to == [EMAIL]
    assert "Seed deck" in message.subject
    assert url in message.body
    assert list(AuditEvent.objects.order_by("id").values_list("action", flat=True)) == [
        "link.created",
        "link.emailed",
    ]


def test_without_email_there_is_no_email_button(client: Client, document: Document) -> None:
    page = client.get(f"/admin/links/for/{document.token}", secure=True).content.decode()
    assert "Create and email" not in page
    assert "Email is not set up" in page
    create(client, document, action="email")
    assert django_mail.outbox == []
    assert "Emailed to" not in client.get("/admin/links/issued", secure=True).content.decode()


@pytest.mark.usefixtures("email_on")
def test_a_failed_email_is_reported_not_logged(
    client: Client, document: Document, monkeypatch: pytest.MonkeyPatch, caplog: Any
) -> None:
    def refuse(*args: object, **kwargs: object) -> int:
        raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    monkeypatch.setattr("django.core.mail.EmailMultiAlternatives.send", refuse)
    create(client, document, action="email")
    page = client.get("/admin/links/issued", secure=True).content.decode()
    assert "could not be sent" in page
    assert AuditEvent.objects.filter(action="link.email_failed").exists()
    assert INVESTOR not in caplog.text and "/v/" not in caplog.text


# ---------------------------------------------------------------- revoking and reissuing


def test_revoking_asks_first_then_revokes(client: Client, document: Document) -> None:
    create(client, document)
    link = Link.objects.get()
    page = client.get(f"/admin/links/{link.ref}/revoke", secure=True).content.decode()
    assert f"Revoke the link for {INVESTOR}?" in page
    assert Link.objects.get().revoked_at is None

    response = client.post(f"/admin/links/{link.ref}/revoke", secure=True)
    assert response["Location"] == f"/admin/documents/{document.token}"
    link.refresh_from_db()
    assert link.is_revoked() and link.revoked_by is not None
    client.post(f"/admin/links/{link.ref}/revoke", secure=True)
    assert AuditEvent.objects.filter(action="link.revoked").count() == 1


def test_a_new_link_stops_the_old_one(client: Client, document: Document) -> None:
    create(client, document)
    old = token_of(issued_url(client))
    link = Link.objects.get()
    assert (
        "stops working at once"
        in client.get(f"/admin/links/{link.ref}/reissue", secure=True).content.decode()
    )

    client.post(f"/admin/links/{link.ref}/reissue", {"action": "copy"}, secure=True)
    new = token_of(issued_url(client))
    assert new != old
    assert Link.find(old) is None
    assert Link.find(new) == link
    assert AuditEvent.objects.filter(action="link.reissued").exists()


def test_a_revoked_link_cannot_be_reissued(client: Client, document: Document) -> None:
    create(client, document)
    link = Link.objects.get()
    client.post(f"/admin/links/{link.ref}/revoke", secure=True)
    before = Link.objects.get().token_hash
    client.post(f"/admin/links/{link.ref}/reissue", {"action": "copy"}, secure=True)
    assert Link.objects.get().token_hash == before


@pytest.mark.parametrize("action", ["revoke", "reissue"])
def test_unknown_link_references(client: Client, action: str) -> None:
    assert client.get(f"/admin/links/ZZZZ/{action}", secure=True).status_code == 404


# ---------------------------------------------------------------- listing


def test_links_are_listed_everywhere(client: Client, document: Document) -> None:
    create(client, document)
    link = Link.objects.get()
    listing = client.get("/admin/links/", secure=True).content.decode()
    assert INVESTOR in listing and link.label in listing and "Seed deck" in listing
    assert 'aria-current="page">Links</a>' in listing
    detail = client.get(f"/admin/documents/{document.token}", secure=True).content.decode()
    assert INVESTOR in detail and "Active" in detail
