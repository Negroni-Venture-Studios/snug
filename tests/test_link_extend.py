"""Extending a link: a later expiry, or none, for the same URL (issue #151)."""

from datetime import date, timedelta

import pytest
from django.test import Client
from django.utils import timezone

from snug.audit.models import AuditEvent
from snug.documents.models import Document
from snug.links.models import Link
from tests.test_links import INVESTOR, admin, client, create, document, issued_url, token_of

__all__ = ["admin", "client", "document"]  # fixtures, used by name

pytestmark = pytest.mark.django_db


def in_days(days: int) -> date:
    return timezone.localdate() + timedelta(days=days)


def link_until(client: Client, document: Document, day: date) -> tuple[Link, str]:
    create(client, document, expires=day.isoformat())
    return Link.objects.get(), token_of(issued_url(client))


def extend(client: Client, link: Link, until: str) -> object:
    return client.post(f"/admin/links/{link.ref}/extend", {"expires": until}, secure=True)


def test_extending_asks_first(client: Client, document: Document) -> None:
    day = in_days(5)
    link, _ = link_until(client, document, day)
    page = client.get(f"/admin/links/{link.ref}/extend", secure=True).content.decode()
    assert f"Extend the link for {INVESTOR}?" in page
    assert f"It works until {day.day} {day:%B %Y}." in page
    assert Link.objects.get().last_day == day


def test_extending_moves_the_expiry_and_keeps_the_url(client: Client, document: Document) -> None:
    link, token = link_until(client, document, in_days(5))
    later = in_days(19)
    response = extend(client, link, later.isoformat())
    assert response["Location"] == f"/admin/documents/{document.token}#links"  # type: ignore[index]
    link.refresh_from_db()
    assert link.last_day == later
    assert Link.find(token) == link
    event = AuditEvent.objects.get(action="link.extended")
    assert event.details["from"] == in_days(5).isoformat()
    assert event.details["to"] == later.isoformat()
    page = client.get(f"/admin/documents/{document.token}", secure=True).content.decode()
    assert f"it now works until {later.day} {later:%B %Y}" in page


def test_extending_with_no_date_removes_the_expiry(client: Client, document: Document) -> None:
    link, _ = link_until(client, document, in_days(5))
    extend(client, link, "")
    link.refresh_from_db()
    assert link.expires_at is None
    assert AuditEvent.objects.get(action="link.extended").details["to"] is None
    page = client.get(f"/admin/documents/{document.token}", secure=True).content.decode()
    assert "it now has no expiry" in page


@pytest.mark.parametrize(
    ("days", "error"),
    [
        (3, "Choose a day after 8 "),  # earlier than the link's last day
        (8, "Choose a day after 8 "),  # the same day
        (0, "The expiry date must be in the future."),
        (-2, "The expiry date must be in the future."),
    ],
)
def test_an_earlier_day_is_refused(
    client: Client, document: Document, days: int, error: str
) -> None:
    link, _ = link_until(client, document, in_days(8))
    response = extend(client, link, in_days(days).isoformat())
    assert response.status_code == 400  # type: ignore[attr-defined]
    page = response.content.decode()  # type: ignore[attr-defined]
    assert error.replace("8 ", f"{in_days(8).day} ") in page
    assert 'aria-invalid="true"' in page
    assert Link.objects.get().last_day == in_days(8)
    assert not AuditEvent.objects.filter(action="link.extended").exists()


def test_nonsense_is_refused(client: Client, document: Document) -> None:
    link, _ = link_until(client, document, in_days(8))
    response = extend(client, link, "next Tuesday")
    assert response.status_code == 400  # type: ignore[attr-defined]
    assert "Enter the expiry as a date." in response.content.decode()  # type: ignore[attr-defined]


def test_an_expired_link_works_again(client: Client, document: Document) -> None:
    link, token = link_until(client, document, in_days(4))
    Link.objects.filter(pk=link.pk).update(expires_at=timezone.now() - timedelta(days=2))
    link.refresh_from_db()
    assert link.status == "expired"
    extend(client, link, in_days(2).isoformat())
    link.refresh_from_db()
    assert link.is_active() and link.last_day == in_days(2)
    assert Link.find(token) == link


def test_a_revoked_link_cannot_be_extended(client: Client, document: Document) -> None:
    link, _ = link_until(client, document, in_days(6))
    client.post(f"/admin/links/{link.ref}/revoke", secure=True)
    response = extend(client, link, in_days(30).isoformat())
    assert response["Location"] == f"/admin/documents/{document.token}"  # type: ignore[index]
    link.refresh_from_db()
    assert link.is_revoked() and link.last_day == in_days(6)
    assert not AuditEvent.objects.filter(action="link.extended").exists()


def test_a_link_with_no_expiry_is_not_shortened(client: Client, document: Document) -> None:
    create(client, document)
    link = Link.objects.get()
    assert client.get(f"/admin/links/{link.ref}/extend", secure=True).status_code == 302
    response = extend(client, link, in_days(9).isoformat())
    assert response["Location"] == f"/admin/documents/{document.token}"  # type: ignore[index]
    assert Link.objects.get().expires_at is None


def test_extend_is_offered_beside_revoke(client: Client, document: Document) -> None:
    link, _ = link_until(client, document, in_days(6))
    create(client, document, email="tomas@northfield.example")  # no expiry
    forever = Link.objects.get(email="tomas@northfield.example")
    listing = client.get("/admin/links/", secure=True).content.decode()
    assert f'href="/admin/links/{link.ref}/extend"' in listing
    assert f'href="/admin/links/{forever.ref}/extend"' not in listing
    client.post(f"/admin/links/{link.ref}/revoke", secure=True)
    listing = client.get("/admin/links/", secure=True).content.decode()
    assert f'href="/admin/links/{link.ref}/extend"' not in listing


def test_unknown_references_are_not_found(client: Client) -> None:
    assert client.get("/admin/links/ZZZZ/extend", secure=True).status_code == 404
