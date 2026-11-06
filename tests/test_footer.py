"""The admin and sign-in footers link to Negroni Venture Studios and to the source.

Asked for 6 November 2026, matching the mini apps: the GitHub mark beside
owner/repo, in the footer's muted text. GitHub's logo guidelines allow the mark
for linking to a project on GitHub (https://github.com/logos).
"""

import time

import pytest
from django.test import Client

from snug.accounts import sessions
from snug.accounts.models import Admin

pytestmark = pytest.mark.django_db

NEGRONI = '<a href="https://negroniventurestudios.com/">Negroni Venture Studios</a>'
SOURCE = 'href="https://github.com/Negroni-Venture-Studios/snug"'


def footer(page: str) -> str:
    return page[page.index('<footer class="admin-foot">') : page.index("</footer>")]


@pytest.fixture
def admin_page() -> str:
    admin = Admin.objects.create(email="ruth@fernlea.example", is_active=True)
    browser = Client()
    browser.force_login(admin)
    session = browser.session
    session[sessions.LOGIN_AT] = session[sessions.LAST_ACTIVITY] = time.time()
    session.save()
    return browser.get("/admin/", secure=True).content.decode()


@pytest.fixture
def sign_in_page() -> str:
    return Client().get("/admin/login", secure=True).content.decode()


@pytest.mark.parametrize("page", ["admin_page", "sign_in_page"])
def test_the_footer_links_to_negroni_and_the_source(
    page: str, request: pytest.FixtureRequest
) -> None:
    html = footer(request.getfixturevalue(page))
    assert f"Snug, from {NEGRONI}" in html
    source = html[html.index('<a class="source-link"') :]
    assert source.index(SOURCE) < source.index("</a>")
    assert 'class="icon github-mark"' in source  # the mark, inline, as the mini apps show it
    assert "Negroni-Venture-Studios/snug</a>" in source
    assert "Source on GitHub" not in html
