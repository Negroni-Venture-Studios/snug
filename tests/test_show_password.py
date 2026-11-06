"""Every password box can show what is typed, for phones and fiddly keyboards.

Asked for 6 November 2026: an eye at the right of the box toggles the
password between hidden and shown. The button stays hidden until the
script runs, since a button that does nothing is worse than none, and the
CSP forbids inline handlers. Autocorrect and capitalisation are off, so a
shown password is not changed as it is typed.
"""

import re

import pytest
from django.test import Client

from snug.accounts import passwords, setup
from snug.accounts.models import Admin, AdminSetup

pytestmark = pytest.mark.django_db


def boxes(page: str) -> list[str]:
    """Each password field's wrapper, as HTML."""
    return re.findall(r'<div class="password-field">.*?</button></div>', page, re.S)


def check(box: str) -> None:
    field = re.search(r'<input id="([a-z0-9]+)"[^>]*type="password"[^>]*>', box)
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
