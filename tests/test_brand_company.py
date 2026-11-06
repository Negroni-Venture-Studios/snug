"""The company's name, set in Settings under Branding, and the colour where investors see it.

The name used to come only from SNUG_COMPANY_NAME in the server's settings,
which a founder cannot change from the admin. Settings now holds it; the
environment variable is used only until a name is saved there. The accent
colour shows as a band across the top of every investor page, as the admin
has in orange, and on the reader's Download button.
"""

import re
import time
from dataclasses import replace
from typing import Any

import pytest
from django.conf import settings
from django.core import mail as django_mail
from django.test import Client

from snug.accounts import sessions
from snug.accounts.models import Admin
from snug.audit.models import AuditEvent
from snug.core.models import SiteSettings, company_name
from snug.documents.models import Document
from snug.links import service

pytestmark = pytest.mark.django_db

INVESTOR = "priya@northfield.example"


@pytest.fixture
def client() -> Client:
    admin = Admin.objects.create(email="ruth@fernlea.example", is_active=True)
    browser = Client()
    browser.force_login(admin)
    session = browser.session
    session[sessions.LOGIN_AT] = session[sessions.LAST_ACTIVITY] = time.time()
    session.save()
    return browser


def save(client: Client, company: str, accent: str = "") -> Any:
    return client.post(
        "/admin/settings/branding", {"company": company, "accent": accent}, secure=True
    )


def issue(require_verification: bool = True) -> service.Issued:
    deck = Document.objects.create(title="Seed deck")
    return service.create(
        document=deck,
        email=INVESTOR,
        note="",
        expires_at=None,
        require_verification=require_verification,
        allow_download=True,
        created_by=None,
    )


# ---------------------------------------------------------------- the name


def test_settings_offers_it(client: Client) -> None:
    page = client.get("/admin/settings/", secure=True).content.decode()
    form = page[page.index('action="/admin/settings/branding') :]
    form = form[: form.index("</form>")]
    assert 'name="company"' in form and 'maxlength="100"' in form


def test_saving_a_name(client: Client) -> None:
    response = save(client, "  Fernlea   Robotics ")
    assert response.status_code == 302
    assert SiteSettings.load().company_name == "Fernlea Robotics"
    assert company_name() == "Fernlea Robotics"
    event = AuditEvent.objects.get(action="settings.branding_changed")
    assert event.details == {"company": "Fernlea Robotics"}  # what changed


def test_a_long_name_is_refused(client: Client) -> None:
    response = save(client, "x" * 101)
    assert response.status_code == 400
    assert "At most 100 characters." in response.content.decode()
    assert SiteSettings.load().company_name == ""


def test_the_server_setting_counts_until_a_name_is_saved(client: Client, monkeypatch: Any) -> None:
    monkeypatch.setattr(settings, "CONFIG", replace(settings.CONFIG, company_name="Old Name Ltd"))
    assert company_name() == "Old Name Ltd"
    save(client, "Fernlea Robotics")
    assert company_name() == "Fernlea Robotics"


def test_investors_and_their_email_see_it(client: Client, email_on: None) -> None:
    save(client, "Fernlea Robotics")
    issued = issue()
    viewer = Client()
    page = viewer.get(f"/v/{issued.token}", secure=True).content.decode()
    assert "<strong>Fernlea Robotics</strong> has shared <strong>Seed deck</strong>" in page
    viewer.post(f"/v/{issued.token}/code/send", {"email": INVESTOR}, secure=True)
    assert re.fullmatch(
        r"Your code for Fernlea Robotics: \d{6}", str(django_mail.outbox[-1].subject)
    )


# ---------------------------------------------------------------- the colour


def test_every_investor_page_has_the_band() -> None:
    issued = issue()
    page = Client().get(f"/v/{issued.token}", secure=True).content.decode()
    assert '<div class="chrome v-chrome" aria-hidden="true"></div>' in page
    css = (settings.BASE_DIR / "static/snug/css/snug.css").read_text()
    assert ".v-chrome { height: 4px; background: var(--brand);" in css


def test_the_download_button_uses_it() -> None:
    reader = (settings.BASE_DIR / "templates/viewer/reader.html").read_text()
    assert '<a class="btn btn-brand" href="{{ download_url }}">' in reader
