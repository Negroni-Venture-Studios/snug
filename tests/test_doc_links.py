"""Each Settings section with a guide links to it, under its heading."""

import re
import time
from pathlib import Path

import pytest
from django.test import Client

from snug.accounts import sessions
from snug.accounts.admin_views import DOCS_URL
from snug.accounts.models import Admin

pytestmark = pytest.mark.django_db

DOCS = Path(__file__).parent.parent / "docs"

# Section id, and the guides linked under its heading.
GUIDES = {
    "admins": ["admin-accounts.md"],
    "account": ["admin-accounts.md"],
    "email": ["email-google.md", "email.md"],
    "nda": ["nda.md"],
}


def settings_page() -> str:
    admin = Admin.objects.create(email="ruth@fernlea.example", is_active=True)
    browser = Client()
    browser.force_login(admin)
    session = browser.session
    session[sessions.LOGIN_AT] = session[sessions.LAST_ACTIVITY] = time.time()
    session.save()
    return browser.get("/admin/settings/", secure=True).content.decode()


def head(page: str, section: str) -> str:
    start = page.index(f'id="{section}"')
    start = page.index('class="section-head', start)
    return page[start : page.index("</div>", page.index("</h2>", start))]


def test_the_guides_are_on_github_and_exist() -> None:
    assert DOCS_URL == "https://github.com/Negroni-Venture-Studios/snug/blob/main/docs/"
    for guides in GUIDES.values():
        for name in guides:
            assert (DOCS / name).is_file(), name


def test_each_section_links_to_its_guide_under_its_heading() -> None:
    page = settings_page()
    for section, guides in GUIDES.items():
        links = re.findall(r'<a class="doc-link[^"]*" href="([^"]+)"', head(page, section))
        assert links == [DOCS_URL + name for name in guides], section


def test_the_guides_open_beside_the_form() -> None:
    """A guide opens in a new tab, so a half-filled form is not lost."""
    page = settings_page()
    for link in re.findall(r'<a class="doc-link[^>]*>', page):
        assert 'target="_blank"' in link and 'rel="noopener noreferrer"' in link, link


def test_email_shows_the_guide_for_the_chosen_provider() -> None:
    email = head(settings_page(), "email")
    assert f'class="doc-link provider-google" href="{DOCS_URL}email-google.md"' in email
    assert f'class="doc-link provider-smtp" href="{DOCS_URL}email.md"' in email


def test_the_book_icon_is_vendored() -> None:
    icons = Path(__file__).parent.parent / "snug" / "static" / "snug" / "icons"
    assert (icons / "book-open-text.svg").read_text().startswith('<svg xmlns="http://www.w3.org')
