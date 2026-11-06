"""A cover message on a link: a short note from the founder, shown before the document.

Before version 1 (DESIGN.md section 4). Plain text, up to 1,000 characters,
with line breaks kept and nothing rendered as HTML. It appears in the viewer's
About this link panel and in the email that carries the link.
"""

import time
from typing import Any

import pytest
from django.core import mail as django_mail
from django.core.mail import EmailMultiAlternatives
from django.test import Client

from snug.accounts import sessions
from snug.accounts.models import Admin
from snug.documents.models import Document
from snug.links import service
from snug.links.models import Link

pytestmark = pytest.mark.django_db

INVESTOR = "priya@northfield.example"
NOTE = "Ahead of Thursday's call.\nPage 12 has the new numbers."


@pytest.fixture
def client() -> Client:
    admin = Admin.objects.create(email="ruth@fernlea.example", is_active=True)
    browser = Client()
    browser.force_login(admin)
    session = browser.session
    session[sessions.LOGIN_AT] = session[sessions.LAST_ACTIVITY] = time.time()
    session.save()
    return browser


@pytest.fixture
def deck() -> Document:
    return Document.objects.create(title="Seed deck")


def create(client: Client, deck: Document, **fields: str) -> Any:
    data = {"email": INVESTOR, "require_verification": "on", "allow_download": "on", **fields}
    return client.post(f"/admin/links/for/{deck.token}", data, secure=True)


def test_the_link_form_offers_it(client: Client, deck: Document) -> None:
    page = client.get(f"/admin/links/for/{deck.token}", secure=True).content.decode()
    assert '<textarea id="message" name="message" rows="4" maxlength="1000">' in page
    assert "Message for them" in page


def test_it_is_saved_trimmed(client: Client, deck: Document) -> None:
    assert create(client, deck, message=f"  {NOTE}  \n").status_code == 302
    assert Link.objects.get().message == NOTE


def test_it_is_optional(client: Client, deck: Document) -> None:
    assert create(client, deck).status_code == 302
    assert Link.objects.get().message == ""


def test_an_overlong_message_is_refused_and_kept(client: Client, deck: Document) -> None:
    response = create(client, deck, message="x" * 1001)
    assert response.status_code == 400
    page = response.content.decode()
    assert "Keep the message to 1,000 characters or fewer." in page
    assert "x" * 1001 in page  # what they typed is kept
    assert not Link.objects.exists()


@pytest.mark.usefixtures("email_on")
def test_the_email_carries_it(client: Client, deck: Document) -> None:
    create(client, deck, message=NOTE, action="email")
    [email] = django_mail.outbox
    assert NOTE in email.body
    assert isinstance(email, EmailMultiAlternatives)
    html = str(email.alternatives[0][0])
    assert "Ahead of Thursday&#x27;s call.<br>Page 12 has the new numbers." in html


def test_the_investor_sees_it_safely(deck: Document) -> None:
    issued = service.create(
        document=deck,
        email=INVESTOR,
        note="",
        message="<script>alert(1)</script>\nSecond line",
        expires_at=None,
        require_verification=True,
        allow_download=True,
        created_by=None,
    )
    page = Client().get(f"/v/{issued.token}", secure=True).content.decode()
    about = page[page.index('aria-label="About this link"') :]
    assert '<p class="cover">&lt;script&gt;alert(1)&lt;/script&gt;<br>Second line</p>' in about
    assert "<script>alert" not in page


def test_no_message_no_paragraph(deck: Document) -> None:
    issued = service.create(
        document=deck,
        email=INVESTOR,
        note="",
        expires_at=None,
        require_verification=True,
        allow_download=True,
        created_by=None,
    )
    page = Client().get(f"/v/{issued.token}", secure=True).content.decode()
    assert 'class="cover"' not in page
