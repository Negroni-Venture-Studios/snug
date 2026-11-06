"""A refused save on Settings shows its error where it was made.

Settings is one long page, and a refused save answers with that page. Each
form's action ends in the anchor of the place its error shows, so the browser
keeps the fragment and scrolls there, and the red error is in view without
any script. Without it, the page opens at the top.
"""

import re
import time
from pathlib import Path

import pytest
from django.test import Client

from snug.accounts import sessions
from snug.accounts.models import Admin

pytestmark = pytest.mark.django_db

# Each Settings form, and the anchor its response opens at. A section with
# two forms anchors the second one on the form itself.
RETURNS_TO = {
    "/admin/settings/password": "account",
    "/admin/settings/authenticator": "authenticator",
    "/admin/settings/admins/invite": "invite",
    "/admin/settings/branding": "branding",
    "/admin/settings/logo": "branding-logo",
    "/admin/settings/nda": "nda",
    "/admin/settings/privacy": "privacy",
    "/admin/settings/retention": "retention",
}


def test_every_form_opens_where_it_was_made(email_on: None) -> None:
    admin = Admin.objects.create(email="ruth@fernlea.example", is_active=True)
    browser = Client()
    browser.force_login(admin)
    session = browser.session
    session[sessions.LOGIN_AT] = session[sessions.LAST_ACTIVITY] = time.time()
    session.save()
    page = browser.get("/admin/settings/", secure=True).content.decode()

    actions = re.findall(r'<form [^>]*action="(/admin/settings/[^"]*)"', page)
    found = dict(action.partition("#")[::2] for action in actions)
    assert found == RETURNS_TO
    for anchor in (*RETURNS_TO.values(), "admins"):  # removals and resets return there
        assert page.count(f'id="{anchor}"') == 1, anchor


def test_a_code_beside_other_fields_is_the_same_height() -> None:
    """The large code style is for pages where the code is the only field."""
    templates = Path(__file__).parent.parent / "snug" / "templates" / "accounts"
    for name in ("settings.html", "password_reset.html"):
        page = (templates / name).read_text()
        assert 'class="code"' not in page, name
        assert 'class="code-field"' in page, name
    assert 'class="code"' in (templates / "login_code.html").read_text()


def test_replacing_the_authenticator_is_a_main_button(email_on: None) -> None:
    admin = Admin.objects.create(email="ruth@fernlea.example", is_active=True)
    browser = Client()
    browser.force_login(admin)
    session = browser.session
    session[sessions.LOGIN_AT] = session[sessions.LAST_ACTIVITY] = time.time()
    session.save()
    page = browser.get("/admin/settings/", secure=True).content.decode()
    assert '<button class="btn btn-primary" type="submit">Replace authenticator</button>' in page


def test_settings_buttons_are_as_wide_as_their_labels() -> None:
    css = (Path(__file__).parent.parent / "snug/static/snug/css/snug.css").read_text()
    assert ".form > .btn { justify-self: start; }" in css


def test_the_admins_come_first(email_on: None) -> None:
    admin = Admin.objects.create(email="ruth@fernlea.example", is_active=True)
    browser = Client()
    browser.force_login(admin)
    session = browser.session
    session[sessions.LOGIN_AT] = session[sessions.LAST_ACTIVITY] = time.time()
    session.save()
    page = browser.get("/admin/settings/", secure=True).content.decode()
    order = ["admins", "account", "branding", "nda", "privacy"]
    found = sorted(order, key=lambda anchor: page.index(f'class="section" id="{anchor}"'))
    assert found == order


def test_inviting_is_a_row_at_the_foot_of_the_admins_list(email_on: None) -> None:
    admin = Admin.objects.create(email="ruth@fernlea.example", is_active=True)
    browser = Client()
    browser.force_login(admin)
    session = browser.session
    session[sessions.LOGIN_AT] = session[sessions.LAST_ACTIVITY] = time.time()
    session.save()
    page = browser.get("/admin/settings/", secure=True).content.decode()
    admins = page[
        page.index('class="section" id="admins"') : page.index('class="section" id="account"')
    ]
    assert admins.index('class="rows admin-rows"') < admins.index(
        '<form class="admin-invite" id="invite"'
    )
    assert 'class="section" id="invite"' not in page
