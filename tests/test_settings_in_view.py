"""A refused save on Settings shows its error where it was made.

Settings is one long page, and a refused save answers with that page. Each
form's action ends in the anchor of the place its error shows, so the browser
keeps the fragment and scrolls there, and the red error is in view without
any script. Without it, the page opens at the top.
"""

import re
import time

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
