"""A new setup link for an admin whose reset link expired or was used up (#223).

After five wrong passwords, or 72 hours, a reset link stops working. The
admin has no authenticator, so cannot sign in, and is already an admin, so
cannot be invited. Another admin can now send them a new link from Settings,
confirmed with their own authenticator code, as a reset is.
"""

import re

import pytest
from django.core import mail as django_mail
from django.test import Client

from snug.accounts import setup
from snug.accounts.models import Admin, AdminSetup
from snug.audit.models import AuditEvent
from tests.test_admin_changes import admin, change, client, other
from tests.test_admin_invites import EMAIL, OTHER

__all__ = ["admin", "client", "other"]  # fixtures, used by name

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("email_on")]


@pytest.fixture
def stuck(email_on: None, client: Client, admin: Admin, other: Admin) -> Admin:
    """OTHER's second factor was reset, and the link then used up."""
    change(client, admin, "reset", OTHER)
    AdminSetup.objects.filter(admin__email=OTHER).delete()
    django_mail.outbox.clear()
    return Admin.objects.get(email=OTHER)


def setup_links_to(email: str) -> list[str]:
    found = []
    for message in django_mail.outbox:
        if message.to == [email]:
            found += re.findall(r"https://snug\.example\.com(/admin/setup/\S+)", str(message.body))
    return found


@pytest.mark.usefixtures("stuck")
def test_settings_offers_a_new_link(client: Client) -> None:
    page = client.get("/admin/settings/", secure=True).content.decode()
    row = page[page.index(OTHER) :]
    row = row[: row.index('<div class="item">')] if '<div class="item">' in row else row
    assert "Resetting" in row
    assert f'href="/admin/settings/admins/reset?email={OTHER}">Send a new link</a>' in row


@pytest.mark.usefixtures("stuck")
def test_it_is_confirmed_on_its_own_page(client: Client) -> None:
    page = client.get(f"/admin/settings/admins/reset?email={OTHER}", secure=True).content.decode()
    assert f"Send {OTHER} a new setup link?" in page
    assert "asks for their current password" in page
    assert not AdminSetup.objects.exists()


def test_a_new_link_is_sent(client: Client, admin: Admin, stuck: Admin) -> None:
    response = change(client, admin, "reset", OTHER)
    assert response.status_code == 302
    assert response["Location"] == "/admin/settings/?resent=1#admins"
    row = AdminSetup.objects.get()
    assert row.admin == stuck and row.purpose == AdminSetup.Purpose.RESET
    links = setup_links_to(OTHER)
    assert len(links) == 1
    # It is a reset link: it asks for their current password, and sets no new one.
    page = Client().get(links[0], secure=True).content.decode()
    assert 'name="current"' in page and 'name="password"' not in page
    event = AuditEvent.objects.get(action="admin.setup_link_resent")
    assert event.actor == EMAIL and event.target == OTHER
    alerted = sorted(m.to[0] for m in django_mail.outbox if "/admin/setup/" not in str(m.body))
    assert alerted == [OTHER, EMAIL]
    notice = client.get("/admin/settings/?resent=1", secure=True).content.decode()
    assert "Sent. They have been emailed a new link to set up their authenticator." in notice


def test_a_live_link_is_replaced(client: Client, admin: Admin, other: Admin) -> None:
    change(client, admin, "reset", OTHER)
    first = setup_links_to(OTHER)[0]
    django_mail.outbox.clear()
    change(client, admin, "reset", OTHER)
    second = setup_links_to(OTHER)[0]
    assert first != second
    assert Client().get(first, secure=True).status_code == 404
    assert Client().get(second, secure=True).status_code == 200


@pytest.mark.usefixtures("stuck")
def test_a_wrong_code_sends_nothing(client: Client, admin: Admin) -> None:
    response = change(client, admin, "reset", OTHER, code="000000")
    assert response.status_code == 400
    assert not AdminSetup.objects.exists()
    assert not django_mail.outbox


def test_a_failed_email_changes_nothing(
    client: Client, admin: Admin, stuck: Admin, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(setup, "send_reset", lambda *args: False)
    response = change(client, admin, "reset", OTHER)
    assert response.status_code == 503
    assert not AdminSetup.objects.exists()


def test_the_used_up_link_says_who_can_send_another(client: Client) -> None:
    page = Client().get("/admin/setup/not-a-real-token", secure=True).content.decode()
    assert "Ask another admin to send you a new one, from Settings, under Admins." in page
    assert "sudo snug reset-2fa" in page
