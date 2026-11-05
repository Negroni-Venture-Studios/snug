"""Removing an admin, and resetting another admin's second factor.

Both ask for a fresh code from the acting admin's authenticator and email
every admin (decided 5 November 2026). An admin cannot remove themselves,
and the last admin can never be removed.
"""

import re
from typing import Any

import pyotp
import pytest
from django.core import mail as django_mail
from django.test import Client

from snug.accounts.models import Admin, AdminSetup, RecoveryCode
from snug.audit.models import AuditEvent
from tests.test_admin_invites import (
    EMAIL,
    OTHER,
    PASSWORD,
    fresh_code,
    make_admin,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin() -> Admin:
    return make_admin(EMAIL)


@pytest.fixture
def other() -> Admin:
    account = make_admin(OTHER)
    from snug.accounts import recovery

    recovery.issue(account)
    return account


def signed_in(account: Admin) -> Client:
    from snug.accounts import sessions

    browser = Client(REMOTE_ADDR="198.51.100.7")
    browser.force_login(account)
    session = browser.session
    session[sessions.LOGIN_AT] = session[sessions.LAST_ACTIVITY] = __import__("time").time()
    session.save()
    return browser


@pytest.fixture
def client(admin: Admin) -> Client:
    return signed_in(admin)


def change(client: Client, admin: Admin, what: str, email: str, code: str | None = None) -> Any:
    data = {"email": email, "code": fresh_code(admin) if code is None else code}
    return client.post(f"/admin/settings/admins/{what}", data, secure=True)


def is_signed_in(browser: Client) -> bool:
    return browser.get("/admin/", secure=True).status_code == 200


# ---------------------------------------------------------------- the settings page


def test_other_admins_have_remove_and_reset_but_you_do_not(client: Client, other: Admin) -> None:
    page = client.get("/admin/settings/", secure=True).content.decode()
    assert f"/admin/settings/admins/remove?email={OTHER}" in page
    assert f"/admin/settings/admins/reset?email={OTHER}" in page
    assert f"remove?email={EMAIL}" not in page and f"reset?email={EMAIL}" not in page


@pytest.mark.parametrize("what", ["remove", "reset"])
def test_changes_are_confirmed_on_their_own_page(client: Client, other: Admin, what: str) -> None:
    page = client.get(f"/admin/settings/admins/{what}?email={OTHER}", secure=True)
    assert page.status_code == 200
    text = page.content.decode()
    assert OTHER in text and 'name="code"' in text and 'name="email"' in text


@pytest.mark.parametrize("what", ["remove", "reset"])
def test_unknown_admins_are_not_found(client: Client, what: str) -> None:
    assert (
        client.get(f"/admin/settings/admins/{what}?email=x@y.example", secure=True).status_code
        == 404
    )
    assert change(client, Admin.objects.get(email=EMAIL), what, "x@y.example").status_code == 404


@pytest.mark.usefixtures("email_on")
@pytest.mark.parametrize("what", ["remove", "reset"])
def test_changes_need_a_fresh_code(client: Client, admin: Admin, other: Admin, what: str) -> None:
    response = change(client, admin, what, OTHER, code="000000")
    assert response.status_code == 400
    assert "That code is not right" in response.content.decode()
    account = Admin.objects.get(email=OTHER)
    assert account.totp_confirmed_at is not None
    assert AuditEvent.objects.filter(action="admin.confirm_failed").exists()


@pytest.mark.parametrize("what", ["remove", "reset"])
def test_changes_need_csrf(admin: Admin, other: Admin, what: str) -> None:
    strict = Client(enforce_csrf_checks=True)
    strict.force_login(admin)
    response = strict.post(
        f"/admin/settings/admins/{what}", {"email": OTHER, "code": "000000"}, secure=True
    )
    assert response.status_code == 403


# ---------------------------------------------------------------- removing


@pytest.mark.usefixtures("email_on")
def test_removing_an_admin(client: Client, admin: Admin, other: Admin) -> None:
    theirs = signed_in(other)
    response = change(client, admin, "remove", OTHER)
    assert response.status_code == 302 and response["Location"] == "/admin/settings/?removed=1"
    assert not Admin.objects.filter(email=OTHER).exists()
    assert not is_signed_in(theirs)
    event = AuditEvent.objects.get(action="admin.removed")
    assert (event.actor, event.target) == (EMAIL, OTHER)
    told = sorted(m.to[0] for m in django_mail.outbox)
    assert told == [OTHER, EMAIL]
    assert all("removed" in str(m.subject) for m in django_mail.outbox)


def test_you_cannot_remove_yourself(client: Client, admin: Admin, other: Admin) -> None:
    response = change(client, admin, "remove", EMAIL)
    assert response.status_code == 400
    assert "cannot remove yourself" in response.content.decode()
    assert Admin.objects.filter(email=EMAIL).exists()


def test_the_last_admin_is_never_removed(
    client: Client, admin: Admin, other: Admin, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Two admins removing each other at once must not leave none. Model the
    # race: the acting admin is gone by the time the removal is checked.
    from snug.accounts import setup

    real = setup.remove

    def racing(target: Admin) -> bool:
        Admin.objects.filter(email=EMAIL).update(is_active=False)
        return real(target)

    monkeypatch.setattr(setup, "remove", racing)
    response = change(client, admin, "remove", OTHER)
    assert response.status_code == 400
    assert "last admin" in response.content.decode()
    assert Admin.objects.filter(email=OTHER, is_active=True).exists()


@pytest.mark.usefixtures("email_on")
def test_removing_a_pending_invite_cancels_it(client: Client, admin: Admin) -> None:
    from snug.accounts import setup

    pending = setup.invitable("jo@fernlea.example")
    assert pending is not None
    pending.save()
    setup.issue(pending, AdminSetup.Purpose.INVITE, admin)
    assert change(client, admin, "remove", "jo@fernlea.example").status_code == 302
    assert not Admin.objects.filter(email="jo@fernlea.example").exists()
    assert not AdminSetup.objects.exists()


# ---------------------------------------------------------------- resetting a second factor


@pytest.mark.usefixtures("email_on")
def test_resetting_another_admins_second_factor(client: Client, admin: Admin, other: Admin) -> None:
    theirs = signed_in(other)
    response = change(client, admin, "reset", OTHER)
    assert response.status_code == 302 and response["Location"] == "/admin/settings/?reset=1"

    account = Admin.objects.get(email=OTHER)
    assert account.totp_secret == "" and account.totp_confirmed_at is None
    assert not RecoveryCode.objects.filter(admin=account).exists()
    assert account.check_password(PASSWORD)  # the password is kept
    assert not is_signed_in(theirs)
    row = AdminSetup.objects.get()
    assert row.admin == account and row.purpose == AdminSetup.Purpose.RESET

    to_them = [m for m in django_mail.outbox if m.to == [OTHER] and "/admin/setup/" in str(m.body)]
    assert len(to_them) == 1
    alerted = sorted(m.to[0] for m in django_mail.outbox if "/admin/setup/" not in str(m.body))
    assert alerted == [OTHER, EMAIL]
    assert AuditEvent.objects.get(action="admin.second_factor_reset").target == OTHER
    listing = client.get("/admin/settings/", secure=True).content.decode()
    assert "Resetting" in listing and "Link works until" in listing


@pytest.mark.usefixtures("email_on")
def test_after_a_reset_the_admin_sets_up_a_new_authenticator(
    client: Client, admin: Admin, other: Admin
) -> None:
    change(client, admin, "reset", OTHER)
    message = next(m for m in django_mail.outbox if "/admin/setup/" in str(m.body))
    url = re.search(r"https://snug\.example\.com(/admin/setup/\S+)", str(message.body))
    assert url
    browser = Client()
    page = browser.get(url.group(1), secure=True).content.decode()
    assert 'name="password"' not in page
    secret = re.search(r'data-key="([A-Z2-7]+)"', page)
    assert secret
    response = browser.post(url.group(1), {"code": pyotp.TOTP(secret.group(1)).now()}, secure=True)
    assert response.status_code == 200
    account = Admin.objects.get(email=OTHER)
    assert account.totp_secret == secret.group(1) and account.check_password(PASSWORD)


def test_you_cannot_reset_your_own_second_factor_here(client: Client, admin: Admin) -> None:
    response = change(client, admin, "reset", EMAIL)
    assert response.status_code == 400
    assert "recovery code" in response.content.decode()


@pytest.mark.usefixtures("email_on")
def test_a_pending_invite_has_no_second_factor_to_reset(client: Client, admin: Admin) -> None:
    from snug.accounts import setup

    pending = setup.invitable("jo@fernlea.example")
    assert pending is not None
    pending.save()
    response = change(client, admin, "reset", "jo@fernlea.example")
    assert response.status_code == 400
    assert "has not finished setting up" in response.content.decode()


def test_reset_without_email_is_refused(client: Client, admin: Admin, other: Admin) -> None:
    response = change(client, admin, "reset", OTHER)
    assert response.status_code == 503
    assert Admin.objects.get(email=OTHER).totp_confirmed_at is not None
