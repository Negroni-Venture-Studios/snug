"""Changing your own password while signed in, from Settings.

Before version 1 (DESIGN.md section 4): until now the only way was the
forgotten-password link. It asks for the current password and a code from
the authenticator, as every other change to an admin account does. This
browser stays signed in; every other session ends.
"""

import time
from typing import Any

import pyotp
import pytest
from django.contrib.auth.hashers import check_password
from django.core import mail as django_mail
from django.test import Client
from django.utils import timezone

from snug.accounts import limits, sessions, totp
from snug.accounts.models import Admin
from snug.audit.models import AuditEvent

pytestmark = pytest.mark.django_db

EMAIL = "ruth@fernlea.example"
OLD = "correct horse battery staple"
NEW = "a brand new long passphrase"


@pytest.fixture
def admin() -> Admin:
    account = Admin(
        email=EMAIL, is_active=True, totp_secret=totp.new_secret(), totp_confirmed_at=timezone.now()
    )
    account.set_password(OLD)
    account.save()
    return account


def signed_in(admin: Admin) -> Client:
    browser = Client()
    browser.force_login(admin)
    session = browser.session
    session[sessions.LOGIN_AT] = session[sessions.LAST_ACTIVITY] = time.time()
    session.save()
    return browser


def code(admin: Admin) -> str:
    return pyotp.TOTP(Admin.objects.get(pk=admin.pk).totp_secret).now()


def change(browser: Client, admin: Admin, **fields: str) -> Any:
    data = {"current": OLD, "password": NEW, "password2": NEW, "code": code(admin), **fields}
    return browser.post("/admin/settings/password", data, secure=True)


def test_settings_offers_it(admin: Admin) -> None:
    page = signed_in(admin).get("/admin/settings/", secure=True).content.decode()
    section = page[
        page.index('id="account"') : page.index('class="section"', page.index('id="account"'))
    ]
    assert 'action="/admin/settings/password#account"' in section
    assert 'autocomplete="current-password"' in section
    assert section.count('autocomplete="new-password"') == 2
    assert 'name="code"' in section
    assert "/static/snug/js/reveal.js" in page


@pytest.mark.usefixtures("email_on")
def test_changing_it(admin: Admin) -> None:
    here, elsewhere = signed_in(admin), signed_in(admin)
    response = change(here, admin)
    assert response.status_code == 302
    assert response["Location"] == "/admin/settings/?saved=password#account"
    admin.refresh_from_db()
    assert check_password(NEW, admin.password)
    assert here.get("/admin/", secure=True).status_code == 200  # still signed in here
    assert elsewhere.get("/admin/", secure=True).status_code == 302  # signed out elsewhere
    page = here.get(response["Location"].split("#")[0], secure=True).content.decode()
    assert "Password changed. You are signed out everywhere else." in page
    [notice] = django_mail.outbox
    assert notice.to == [EMAIL] and notice.subject == "Your Snug password was changed"
    assert "from Settings" in notice.body
    event = AuditEvent.objects.get(action="admin.password_changed")
    assert (event.actor, event.target) == (EMAIL, EMAIL)


@pytest.mark.parametrize(
    ("fields", "field", "message"),
    [
        ({"current": "not my password"}, "current", "That is not your current password."),
        ({"code": "000000"}, "code", "That code is not right"),
        ({"password2": "something else entirely"}, "password", "The passwords do not match."),
        ({"password": "short", "password2": "short"}, "password", "too short"),
    ],
)
def test_a_wrong_field_changes_nothing(
    admin: Admin, fields: dict[str, str], field: str, message: str
) -> None:
    browser = signed_in(admin)
    response = change(browser, admin, **fields)
    assert response.status_code == 400
    page = response.content.decode()
    assert message in page
    assert f'id="{field}-error"' in page or f'id="account-{field}-error"' in page
    admin.refresh_from_db()
    assert check_password(OLD, admin.password)


def test_a_weak_new_password_spends_no_code(admin: Admin) -> None:
    browser = signed_in(admin)
    used = code(admin)
    change(browser, admin, password="short", password2="short", code=used)
    assert change(browser, admin, code=used).status_code == 302  # the code still works


def test_attempts_are_limited(admin: Admin) -> None:
    browser = signed_in(admin)
    for _ in range(limits.CONFIRM_PER_ADMIN.limit):
        change(browser, admin, current="not my password")
    response = change(browser, admin)
    assert response.status_code == 429
    admin.refresh_from_db()
    assert check_password(OLD, admin.password)
    assert AuditEvent.objects.filter(action="admin.password_change_failed").count() >= 10


def test_it_needs_a_signed_in_admin() -> None:
    response = Client().post("/admin/settings/password", {}, secure=True)
    assert response.status_code == 302 and response["Location"].startswith("/admin/login")
