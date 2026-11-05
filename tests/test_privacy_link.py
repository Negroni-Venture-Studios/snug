"""The founder's privacy policy link: set in Settings, shown to viewers.

Decided 5 November 2026: viewers see a short statement of what is recorded,
plus an optional link to the founder's own privacy policy.
"""

import time
from typing import Any

import pytest
from django.test import Client

from snug.accounts import sessions
from snug.accounts.models import Admin
from snug.audit.models import AuditEvent
from snug.core.models import SiteSettings
from snug.documents.models import Document
from snug.links import service

pytestmark = pytest.mark.django_db

POLICY = "https://fernlea.example/privacy"


@pytest.fixture
def admin() -> Admin:
    return Admin.objects.create(email="ruth@fernlea.example", is_active=True)


@pytest.fixture
def client(admin: Admin) -> Client:
    browser = Client()
    browser.force_login(admin)
    session = browser.session
    session[sessions.LOGIN_AT] = session[sessions.LAST_ACTIVITY] = time.time()
    session.save()
    return browser


def save(client: Client, url: str) -> Any:
    return client.post("/admin/settings/privacy", {"privacy_url": url}, secure=True)


def viewer_page() -> str:
    document = Document.objects.create(title="Seed deck")
    issued = service.create(
        document=document,
        email="priya@northfield.example",
        note="",
        expires_at=None,
        require_verification=True,
        allow_download=True,
        created_by=None,
    )
    return Client().get(f"/v/{issued.token}", secure=True).content.decode()


def test_there_is_no_link_until_one_is_set() -> None:
    page = viewer_page()
    assert "will see when you open this document" in page
    assert "privacy policy" not in page


def test_setting_the_link_shows_it_to_viewers(client: Client) -> None:
    response = save(client, f"  {POLICY}  ")
    assert (
        response.status_code == 302
        and response["Location"] == "/admin/settings/?saved=privacy#privacy"
    )
    assert SiteSettings.load().privacy_url == POLICY
    page = viewer_page()
    assert f'href="{POLICY}"' in page and 'rel="noopener noreferrer"' in page
    assert AuditEvent.objects.get(action="settings.privacy_link_changed").details == {"url": POLICY}
    settings_page = client.get("/admin/settings/", secure=True).content.decode()
    assert f'value="{POLICY}"' in settings_page


def test_the_link_can_be_removed(client: Client) -> None:
    save(client, POLICY)
    save(client, "")
    assert SiteSettings.load().privacy_url == ""
    assert "privacy policy" not in viewer_page()


@pytest.mark.parametrize(
    "url",
    [
        "http://fernlea.example/privacy",
        "javascript:alert(1)",
        "https://user:secret@fernlea.example/privacy",
        "fernlea.example/privacy",
        "https://" + "x" * 500 + ".example",
        "https://fernlea.example/a b",
    ],
)
def test_only_plain_https_links_are_accepted(client: Client, url: str) -> None:
    response = save(client, url)
    assert response.status_code == 400
    assert "https://" in response.content.decode()
    assert SiteSettings.load().privacy_url == ""


def test_changing_settings_needs_a_signed_in_admin_and_csrf(admin: Admin) -> None:
    assert (
        Client().post("/admin/settings/privacy", {"privacy_url": POLICY}, secure=True).status_code
        == 302
    )
    strict = Client(enforce_csrf_checks=True)
    strict.force_login(admin)
    assert (
        strict.post("/admin/settings/privacy", {"privacy_url": POLICY}, secure=True).status_code
        == 403
    )
    assert SiteSettings.load().privacy_url == ""


def test_there_is_only_ever_one_settings_row() -> None:
    first = SiteSettings.load()
    second = SiteSettings.load()
    assert first.pk == second.pk == 1
    assert SiteSettings.objects.count() == 1
