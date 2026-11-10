"""Every password box can show what is typed, for phones and fiddly keyboards.

Asked for 6 November 2026: an eye at the right of the box toggles the
password between hidden and shown. The button stays hidden until the
script runs, since a button that does nothing is worse than none, and the
CSP forbids inline handlers. Autocorrect and capitalisation are off, so a
shown password is not changed as it is typed.
"""

import re
import time

import pytest
from django.test import Client

from snug.accounts import passwords, sessions, setup
from snug.accounts.models import Admin, AdminSetup
from snug.core.models import EmailSettings

pytestmark = pytest.mark.django_db


def boxes(page: str) -> list[str]:
    """Each password field's wrapper, as HTML."""
    return re.findall(r'<div class="password-field">.*?</button></div>', page, re.S)


def check(box: str) -> None:
    field = re.search(r'<input id="([a-z0-9-]+)"[^>]*type="password"[^>]*>', box)
    assert field, box
    for attribute in ('autocapitalize="none"', 'autocorrect="off"', 'spellcheck="false"'):
        assert attribute in field.group(0)
    button = box[box.index("<button") :]
    assert 'type="button"' in button  # never submits the form
    assert f'aria-controls="{field.group(1)}"' in button
    assert 'aria-pressed="false"' in button and 'aria-label="Show password"' in button
    assert " hidden>" in button  # until the script shows it
    assert 'class="icon when-hidden"' in button and 'class="icon when-shown"' in button


def test_the_sign_in_page(client: Client) -> None:
    page = client.get("/admin/login", secure=True).content.decode()
    [box] = boxes(page)
    check(box)
    assert 'autocomplete="current-password"' in box
    assert "/static/snug/js/reveal.js" in page


def test_choosing_a_new_password(client: Client) -> None:
    admin = Admin.objects.create(email="ruth@fernlea.example", is_active=True, totp_secret="X" * 32)
    Admin.objects.filter(pk=admin.pk).update(totp_confirmed_at="2026-11-06T09:00:00Z")
    token = passwords.issue(Admin.objects.get(pk=admin.pk))
    page = client.get(f"/admin/setup/{token}", secure=True).content.decode()
    found = boxes(page)
    assert len(found) == 2
    for box in found:
        check(box)
    assert "/static/snug/js/reveal.js" in page


def test_setting_up_an_invited_account(client: Client) -> None:
    invited = Admin.objects.create(email="tom@fernlea.example", is_active=False)
    token = setup.issue(invited, AdminSetup.Purpose.INVITE, by=None)
    page = client.get(f"/admin/setup/{token}", secure=True).content.decode()
    found = boxes(page)
    assert len(found) == 2
    for box in found:
        check(box)


def test_a_wrong_password_shows_its_error_against_the_box(client: Client) -> None:
    page = client.post(
        "/admin/login", {"email": "nobody@fernlea.example", "password": "x"}, secure=True
    ).content.decode()
    [box] = boxes(page)
    check(box)


def settings_page() -> str:
    admin = Admin.objects.create(email="ruth@fernlea.example", is_active=True)
    browser = Client()
    browser.force_login(admin)
    session = browser.session
    session[sessions.LOGIN_AT] = session[sessions.LAST_ACTIVITY] = time.time()
    session.save()
    return browser.get("/admin/settings/", secure=True).content.decode()


def test_every_password_box_in_settings_has_an_eye() -> None:
    """Found in a docs check: the SMTP password and Google client secret had none."""
    page = settings_page()
    found = boxes(page)
    assert len(found) == page.count('type="password"')
    for box in found:
        check(box)
    ids = [re.search(r'<input id="([a-z0-9-]+)"', box).group(1) for box in found]  # type: ignore[union-attr]
    assert "smtp-password" in ids and "google-client-secret" in ids
    assert "/static/snug/js/reveal.js" in page


def test_a_saved_email_secret_box_stays_optional_and_shows_dots() -> None:
    row = EmailSettings.load()
    row.provider = "smtp"
    row.smtp_password = "s3cret-pa$$word"
    row.google_client_secret = "GOCSPX-secret"
    row.google_client_id = "client.apps.googleusercontent.com"
    row.save()
    page = settings_page()
    for name in ("smtp_password", "google_client_secret"):
        box = re.search(rf'<input [^>]*name="{name}"[^>]*>', page)
        assert box, name
        assert " required" not in box.group(0)
        assert 'value=""' in box.group(0)
        assert 'placeholder="••••••••••••"' in box.group(0)
    assert "s3cret-pa$$word" not in page and "GOCSPX-secret" not in page
