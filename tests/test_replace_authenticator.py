"""Replacing your own authenticator while signed in, for a new phone.

It asks for the password and a
code from the current authenticator, or a recovery code when the old phone
is gone. The old authenticator and recovery codes stop working at once, and
the admin goes straight to the setup page for the new one; the link is also
emailed, so closing the tab halfway does not lock them out.
"""

import re
import time
from typing import Any

import pyotp
import pytest
from django.core import mail as django_mail
from django.test import Client
from django.utils import timezone

from snug.accounts import limits, recovery, sessions, totp
from snug.accounts.models import Admin, AdminSetup, RecoveryCode
from snug.audit.models import AuditEvent

pytestmark = pytest.mark.django_db

EMAIL = "ruth@fernlea.example"
OTHER = "tom@fernlea.example"
PASSWORD = "correct horse battery staple"


def make(email: str) -> Admin:
    account = Admin(
        email=email, is_active=True, totp_secret=totp.new_secret(), totp_confirmed_at=timezone.now()
    )
    account.set_password(PASSWORD)
    account.save()
    return account


@pytest.fixture
def admin() -> Admin:
    make(OTHER)
    return make(EMAIL)


def signed_in(admin: Admin) -> Client:
    browser = Client()
    browser.force_login(admin)
    session = browser.session
    session[sessions.LOGIN_AT] = session[sessions.LAST_ACTIVITY] = time.time()
    session.save()
    return browser


def code(admin: Admin) -> str:
    return pyotp.TOTP(Admin.objects.get(pk=admin.pk).totp_secret).now()


def replace(browser: Client, **fields: str) -> Any:
    data = {"current": PASSWORD, **fields}
    return browser.post("/admin/settings/authenticator", data, secure=True)


def test_settings_offers_it(admin: Admin) -> None:
    page = signed_in(admin).get("/admin/settings/", secure=True).content.decode()
    section = page[
        page.index('id="account"') : page.index('class="section"', page.index('id="account"'))
    ]
    assert 'action="/admin/settings/authenticator#authenticator"' in section
    assert "Replace your authenticator" in section


@pytest.mark.usefixtures("email_on")
def test_replacing_it_with_a_code_from_the_old_app(admin: Admin) -> None:
    old_secret = admin.totp_secret
    recovery.issue(admin)
    browser = signed_in(admin)
    response = replace(browser, code=code(admin))
    assert response.status_code == 302
    location = response["Location"]
    assert re.fullmatch(r"/admin/setup/[A-Za-z0-9_-]+", location)
    admin.refresh_from_db()
    assert admin.totp_secret == "" and admin.totp_confirmed_at is None
    assert not RecoveryCode.objects.filter(admin=admin).exists()
    assert AdminSetup.objects.get(admin=admin).purpose == AdminSetup.Purpose.RESET
    assert browser.get("/admin/", secure=True).status_code == 302  # the old factor's session ends
    page = browser.get(location, secure=True).content.decode()
    assert "Set up your authenticator" in page and "qr-tile" in page
    assert old_secret not in page
    # The link is emailed too, and every admin is told.
    to_me = [m for m in django_mail.outbox if m.to == [EMAIL] and location in m.body]
    assert (
        len(to_me) == 1 and to_me[0].subject == "Finish setting up your new authenticator for Snug"
    )
    alerts = sorted(m.to[0] for m in django_mail.outbox if "replaced" in str(m.subject))
    assert alerts == [EMAIL, OTHER]
    event = AuditEvent.objects.get(action="admin.second_factor_replaced")
    assert (event.actor, event.target, event.details) == (EMAIL, EMAIL, {"method": "totp"})


def test_a_recovery_code_works_when_the_old_phone_is_gone(admin: Admin) -> None:
    codes = recovery.issue(admin)
    response = replace(signed_in(admin), code=codes[0])
    assert response.status_code == 302
    event = AuditEvent.objects.get(action="admin.second_factor_replaced")
    assert event.details == {"method": "recovery"}


def test_without_email_it_still_works(admin: Admin) -> None:
    response = replace(signed_in(admin), code=code(admin))
    assert response.status_code == 302 and response["Location"].startswith("/admin/setup/")
    assert django_mail.outbox == []


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"current": "not my password", "code": "LIVE"}, "That is not your current password."),
        ({"code": "000000"}, "That code is not right"),
        ({"code": "aaaa-bbbb-cccc-dddd"}, "That code is not right"),
    ],
)
def test_a_wrong_field_changes_nothing(admin: Admin, fields: dict[str, str], message: str) -> None:
    secret = admin.totp_secret
    if fields.get("code") == "LIVE":
        fields = {**fields, "code": code(admin)}
    response = replace(signed_in(admin), **fields)
    assert response.status_code == 400
    assert message in response.content.decode()
    admin.refresh_from_db()
    assert admin.totp_secret == secret and admin.totp_confirmed_at is not None
    assert not AdminSetup.objects.exists()


def test_attempts_are_limited(admin: Admin) -> None:
    browser = signed_in(admin)
    for _ in range(limits.CONFIRM_PER_ADMIN.limit):
        replace(browser, code="000000")
    assert replace(browser, code=code(admin)).status_code == 429
    admin.refresh_from_db()
    assert admin.totp_confirmed_at is not None


def test_resetting_yourself_from_the_admins_list_points_here(admin: Admin) -> None:
    browser = signed_in(admin)
    response = browser.post(
        "/admin/settings/admins/reset", {"email": EMAIL, "code": code(admin)}, secure=True
    )
    assert "Replace your authenticator, under Your account" in response.content.decode()
