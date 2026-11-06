"""Inviting admins: the Settings page, setup links, the setup page and alerts.

Decided 5 November 2026: invites are emailed setup links that work for 72
hours; inviting asks for a fresh code from the inviter's authenticator; and
every admin is emailed when an admin is invited.
"""

import re
from datetime import timedelta
from typing import Any

import pyotp
import pytest
from django.core import mail as django_mail
from django.test import Client
from django.utils import timezone

from snug.accounts import limits, setup, totp
from snug.accounts.models import Admin, AdminSetup, RecoveryCode
from snug.audit.models import AuditEvent
from snug.core import ratelimit

pytestmark = pytest.mark.django_db

EMAIL = "ruth@fernlea.example"
OTHER = "omar@fernlea.example"
NEW = "jo@fernlea.example"
PASSWORD = "correct horse battery staple"
NEW_PASSWORD = "a different long passphrase"


def make_admin(email: str) -> Admin:
    account = Admin(
        email=email, is_active=True, totp_secret=totp.new_secret(), totp_confirmed_at=timezone.now()
    )
    account.set_password(PASSWORD)
    account.save()
    return account


@pytest.fixture
def admin() -> Admin:
    return make_admin(EMAIL)


@pytest.fixture
def other() -> Admin:
    return make_admin(OTHER)


@pytest.fixture
def client(admin: Admin) -> Client:
    from snug.accounts import sessions

    browser = Client(REMOTE_ADDR="198.51.100.7")
    browser.force_login(admin)
    session = browser.session
    session[sessions.LOGIN_AT] = session[sessions.LAST_ACTIVITY] = __import__("time").time()
    session.save()
    return browser


def fresh_code(account: Admin) -> str:
    """The authenticator's current code, made usable even just after sign-in."""
    Admin.objects.filter(pk=account.pk).update(totp_last_step=0)
    return pyotp.TOTP(account.totp_secret).now()


def invite(client: Client, admin: Admin, email: str = NEW, code: str | None = None) -> Any:
    data = {"email": email, "code": fresh_code(admin) if code is None else code}
    return client.post("/admin/settings/admins/invite", data, secure=True)


def setup_url() -> str:
    message = next(m for m in django_mail.outbox if m.to == [NEW])
    match = re.search(r"https://snug\.example\.com(/admin/setup/[A-Za-z0-9_-]+)", str(message.body))
    assert match
    return match.group(1)


def events(action: str) -> list[AuditEvent]:
    return list(AuditEvent.objects.filter(action=action).order_by("id"))


# ---------------------------------------------------------------- the settings page


def test_settings_lists_the_admins(client: Client, other: Admin) -> None:
    page = client.get("/admin/settings/", secure=True).content.decode()
    assert 'aria-current="page">Settings</a>' in page
    assert EMAIL in page and OTHER in page


@pytest.mark.usefixtures("email_on")
def test_a_pending_invite_is_listed(client: Client, admin: Admin) -> None:
    invite(client, admin)
    page = client.get("/admin/settings/", secure=True).content.decode()
    assert NEW in page and "Invited" in page


def test_without_email_there_is_no_invite_form(client: Client, admin: Admin) -> None:
    page = client.get("/admin/settings/", secure=True).content.decode()
    assert "Inviting needs email" in page
    assert 'action="/admin/settings/admins/invite' not in page
    assert invite(client, admin).status_code == 503
    assert not Admin.objects.filter(email=NEW).exists()


def test_settings_need_a_signed_in_admin_and_csrf(admin: Admin) -> None:
    assert Client().get("/admin/settings/", secure=True).status_code == 302
    strict = Client(enforce_csrf_checks=True)
    strict.force_login(admin)
    response = strict.post(
        "/admin/settings/admins/invite", {"email": NEW, "code": "000000"}, secure=True
    )
    assert response.status_code == 403


# ---------------------------------------------------------------- inviting


@pytest.mark.usefixtures("email_on")
def test_inviting_needs_a_fresh_code(client: Client, admin: Admin) -> None:
    response = invite(client, admin, code="000000")
    assert response.status_code == 400
    assert "That code is not right" in response.content.decode()
    assert not Admin.objects.filter(email=NEW).exists() and django_mail.outbox == []
    assert events("admin.confirm_failed")[-1].details["change"] == "invite"


@pytest.mark.usefixtures("email_on")
def test_a_code_cannot_confirm_twice(client: Client, admin: Admin) -> None:
    code = fresh_code(admin)
    assert invite(client, admin, code=code).status_code == 302
    assert invite(client, admin, email="kim@fernlea.example", code=code).status_code == 400


@pytest.mark.usefixtures("email_on")
def test_inviting_sends_a_setup_link_and_alerts_every_admin(
    client: Client, admin: Admin, other: Admin
) -> None:
    response = invite(client, admin, email=" Jo@Fernlea.example ")
    assert response.status_code == 302 and response["Location"] == "/admin/settings/?invited=1#invite"

    invited = Admin.objects.get(email=NEW)
    assert not invited.is_active and not invited.has_usable_password()
    row = AdminSetup.objects.get()
    assert row.admin == invited and row.purpose == AdminSetup.Purpose.INVITE
    token = setup_url().rsplit("/", 1)[1]
    assert len(token) >= 43 and token not in row.token_hash
    assert timedelta(hours=72) - (row.expires_at - timezone.now()) < timedelta(minutes=1)

    to_new = next(m for m in django_mail.outbox if m.to == [NEW])
    assert to_new.reply_to == [EMAIL] and "72 hours" in str(to_new.body)
    alerted = sorted(m.to[0] for m in django_mail.outbox if m.to != [NEW])
    assert alerted == [OTHER, EMAIL]
    alert = next(m for m in django_mail.outbox if m.to == [OTHER])
    assert EMAIL in str(alert.body) and NEW in str(alert.body) and "remove" in str(alert.body)

    event = events("admin.invited")[-1]
    assert (event.actor, event.target) == (EMAIL, NEW)
    for event in AuditEvent.objects.all():
        assert token not in str(event.details)


@pytest.mark.usefixtures("email_on")
def test_an_existing_admin_cannot_be_invited(client: Client, admin: Admin, other: Admin) -> None:
    response = invite(client, admin, email=OTHER)
    assert response.status_code == 400
    assert "already an admin" in response.content.decode()
    assert AdminSetup.objects.count() == 0


@pytest.mark.usefixtures("email_on")
def test_a_bad_address_is_refused(client: Client, admin: Admin) -> None:
    response = invite(client, admin, email="not-an-address")
    assert response.status_code == 400
    assert not AdminSetup.objects.exists()


@pytest.mark.usefixtures("email_on")
def test_inviting_again_replaces_the_link(client: Client, admin: Admin) -> None:
    invite(client, admin)
    first = setup_url()
    django_mail.outbox.clear()
    invite(client, admin)
    assert setup_url() != first
    assert Client().get(first, secure=True).status_code == 404
    assert AdminSetup.objects.count() == 1 and Admin.objects.filter(email=NEW).count() == 1


@pytest.mark.usefixtures("email_on")
def test_confirming_is_rate_limited(client: Client, admin: Admin) -> None:
    for _ in range(limits.CONFIRM_PER_ADMIN.limit):
        ratelimit.hit(limits.CONFIRM_PER_ADMIN, str(admin.pk))
    response = invite(client, admin)
    assert response.status_code == 429
    assert not Admin.objects.filter(email=NEW).exists()


@pytest.mark.usefixtures("email_on")
def test_a_failed_invite_email_is_reported(
    client: Client, admin: Admin, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("snug.core.mail.send", lambda **kwargs: False)
    response = invite(client, admin)
    assert response.status_code == 503
    assert "could not be sent" in response.content.decode()
    assert not AdminSetup.objects.exists()


# ---------------------------------------------------------------- the setup page


@pytest.fixture
def invited(client: Client, admin: Admin, email_on: None) -> str:
    invite(client, admin)
    return setup_url()


def setup_page(browser: Client, url: str) -> tuple[Any, str]:
    response = browser.get(url, secure=True)
    page = response.content.decode()
    match = re.search(r'data-key="([A-Z2-7]+)"', page)
    return response, match.group(1) if match else ""


def complete(browser: Client, url: str, secret: str, **fields: str) -> Any:
    data = {"password": NEW_PASSWORD, "password2": NEW_PASSWORD, "code": pyotp.TOTP(secret).now()}
    data.update(fields)
    return browser.post(url, data, secure=True)


def test_the_setup_page_shows_a_qr_code_and_key(invited: str) -> None:
    response, secret = setup_page(Client(), invited)
    page = response.content.decode()
    assert response.status_code == 200 and secret
    assert 'class="qr"' in page and "viewBox=" in page and " style=" not in page
    assert 'name="password"' in page and 'name="password2"' in page
    assert "no-store" in response["Cache-Control"]


def test_setting_up_activates_the_admin(invited: str) -> None:
    browser = Client()
    _, secret = setup_page(browser, invited)
    response = complete(browser, invited, secret)
    assert response.status_code == 200
    page = response.content.decode()
    codes = re.findall(r'<li class="code">([^<]+)</li>', page)
    assert len(codes) == 10 and "will not be shown again" in page

    account = Admin.objects.get(email=NEW)
    assert account.is_active and account.check_password(NEW_PASSWORD)
    assert account.totp_secret == secret and account.totp_confirmed_at is not None
    assert RecoveryCode.objects.filter(admin=account).count() == 10
    assert not AdminSetup.objects.exists()
    assert events("admin.setup_completed")[-1].actor == NEW
    # The link is spent.
    assert Client().get(invited, secure=True).status_code == 404


def test_a_setup_link_completes_only_once(invited: str) -> None:
    # Two submissions racing on one link: both found it live, but only the
    # first to claim it may change the account.
    row = AdminSetup.objects.get()
    first, second = totp.new_secret(), totp.new_secret()
    assert setup.complete(row, password=NEW_PASSWORD, secret=first, step=1)
    assert (
        setup.complete(row, password="another passphrase entirely", secret=second, step=1) is None
    )
    account = Admin.objects.get(email=NEW)
    assert account.totp_secret == first and account.check_password(NEW_PASSWORD)
    assert RecoveryCode.objects.filter(admin=account).count() == 10


def test_the_new_admin_can_sign_in(invited: str) -> None:
    browser = Client()
    _, secret = setup_page(browser, invited)
    complete(browser, invited, secret)
    login = Client()
    login.post("/admin/login", {"email": NEW, "password": NEW_PASSWORD}, secure=True)
    # The code that confirmed setup cannot be used again; the next one can.
    account = Admin.objects.get(email=NEW)
    Admin.objects.filter(pk=account.pk).update(totp_last_step=0)
    response = login.post("/admin/login/code", {"code": pyotp.TOTP(secret).now()}, secure=True)
    assert response.status_code == 302 and response["Location"] == "/admin/"


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"password2": "something else entirely"}, "The passwords do not match"),
        ({"password": "short", "password2": "short"}, "too short"),
        ({"code": "000000"}, "That code is not right"),
    ],
)
def test_setup_problems_are_explained(invited: str, fields: dict[str, str], message: str) -> None:
    browser = Client()
    _, secret = setup_page(browser, invited)
    response = complete(browser, invited, secret, **fields)
    assert response.status_code == 400 and message in response.content.decode()
    assert not Admin.objects.get(email=NEW).is_active
    # The same key is offered again, so there is nothing to rescan.
    _, again = setup_page(browser, invited)
    assert again == secret


def test_a_posted_setup_without_the_page_starts_again(invited: str) -> None:
    response = complete(Client(), invited, totp.new_secret())
    assert response.status_code == 400
    assert "Scan the QR code" in response.content.decode()
    assert not Admin.objects.get(email=NEW).is_active


@pytest.mark.parametrize("url", ["/admin/setup/not-a-real-token", "/admin/setup/" + "x" * 300])
def test_unknown_setup_links_are_not_found(url: str) -> None:
    response = Client().get(url, secure=True)
    assert response.status_code == 404
    assert "This setup link has expired or been used" in response.content.decode()


def test_setup_links_expire(invited: str) -> None:
    AdminSetup.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    assert Client().get(invited, secure=True).status_code == 404


def test_setup_posts_are_rate_limited(invited: str) -> None:
    for _ in range(limits.SETUP_PER_IP.limit):
        ratelimit.hit(limits.SETUP_PER_IP, "203.0.113.5")
    browser = Client(REMOTE_ADDR="203.0.113.5")
    _, secret = setup_page(browser, invited)
    assert complete(browser, invited, secret).status_code == 429


def test_setup_tokens_are_found_only_by_hash() -> None:
    assert setup.find("") is None
    assert setup.find("x" * 500) is None
