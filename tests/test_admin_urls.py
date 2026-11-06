"""Every admin page needs a signed-in admin, except a short list that cannot.

Walks the URL configuration, like tests/test_viewer_urls.py, so a new admin
page that forgets `login_required` fails here. Added with the forgotten-password
page (6 November 2026), which is deliberately open.
"""

import re

import pytest
from django.test import Client

from tests.test_viewer_urls import walk

pytestmark = pytest.mark.django_db

# Pages for someone not yet signed in. Adding to this set needs a reason.
PUBLIC = {
    "accounts:login",
    "accounts:login_code",  # needs the password step's pending session instead
    "accounts:login_recovery",  # likewise
    "accounts:forgot",  # asks for a link; never says who is an admin
    "accounts:setup",  # the emailed link's token is the credential
    "accounts:logout",  # POST only; signing out needs no sign-in
}
ADMIN = [(route, name) for route, name, _ in walk() if route.startswith("admin/")]


def concrete(route: str) -> str:
    """A path for the route, with made-up values for each parameter."""
    path = re.sub(r"<int:[a-z_]+>", "1", route)
    return "/" + re.sub(r"<(?:str:)?[a-z_]+>", "x", path)


def test_there_are_admin_urls_to_check() -> None:
    assert len(ADMIN) > 20


def test_every_public_page_still_exists() -> None:
    assert PUBLIC.issubset({name for _, name in ADMIN})


@pytest.mark.parametrize(("route", "name"), [a for a in ADMIN if a[1] not in PUBLIC])
def test_every_other_admin_page_needs_a_sign_in(route: str, name: str) -> None:
    browser = Client()
    path = concrete(route)
    response = browser.get(path, secure=True)
    if response.status_code == 405:  # POST only: the sign-in check must still come first
        response = browser.post(path, {}, secure=True)
    assert response.status_code == 302, (name, response.status_code)
    assert response["Location"].startswith("/admin/login?next="), (name, response["Location"])
