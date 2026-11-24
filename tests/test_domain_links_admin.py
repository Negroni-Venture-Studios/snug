"""Creating links for firms, and blocking one person on a link, from the admin (#174)."""

from typing import Any

import pytest
from django.core import mail as django_mail
from django.http import HttpResponse
from django.test import Client
from django.utils import timezone

from snug.audit.models import AuditEvent
from snug.documents.models import Document
from snug.links import service
from snug.links.models import Link, LinkPerson
from snug.viewer import sessions
from snug.viewer.access import NotVerified, authorise
from tests.test_links import admin, client
from tests.test_viewer_access import browser

__all__ = ["admin", "client"]  # fixtures, used by name

pytestmark = pytest.mark.django_db

PRIYA = "priya@northfield.example"
ADVISER = "jo.adviser@outlook-mail.example"


@pytest.fixture
def document() -> Document:
    return Document.objects.create(title="Seed deck")


def create(client: Client, document: Document, who: str, **fields: str) -> Any:
    data = {"email": who, "note": "Northfield Capital", "require_verification": "on"}
    data.update(fields)
    return client.post(f"/admin/links/for/{document.token}", data, secure=True)


def firm_link(document: Document, entries: list[str] | None = None) -> service.Issued:
    return service.create(
        document=document,
        entries=entries or ["northfield.example", ADVISER],
        note="",
        expires_at=None,
        require_verification=True,
        allow_download=True,
        created_by=None,
    )


# ---------------------------------------------------------------- creating


@pytest.mark.parametrize(
    "who",
    [
        f"northfield.example, {ADVISER}",
        f"Northfield.example\n{ADVISER}",
        f"northfield.example  {ADVISER}",
        f"northfield.example;{ADVISER}",
    ],
)
def test_a_link_is_created_for_a_domain_and_an_address(
    client: Client, document: Document, who: str
) -> None:
    response = create(client, document, who)
    assert response.status_code == 302
    link = Link.objects.get()
    assert link.entry_list == ["northfield.example", ADVISER]
    event = AuditEvent.objects.get(action="link.created")
    assert event.target == f"northfield.example, {ADVISER}"


def test_the_form_explains_domains(client: Client, document: Document) -> None:
    page = client.get(f"/admin/links/for/{document.token}", secure=True).content.decode()
    assert "Who it is for" in page
    assert "A domain such as northfield.com lets anyone there open it" in page


@pytest.mark.parametrize("free", ["gmail.com", "Outlook.com", "icloud.com", "yahoo.co.uk"])
def test_free_mail_domains_are_refused(client: Client, document: Document, free: str) -> None:
    response = create(client, document, f"northfield.example, {free}")
    assert response.status_code == 400
    page = response.content.decode()
    assert f"{free.lower()} is a free email service" in page
    assert 'aria-invalid="true"' in page
    assert not Link.objects.exists()


def test_a_free_mail_address_is_fine(client: Client, document: Document) -> None:
    assert create(client, document, "jo@gmail.com").status_code == 302
    assert Link.objects.get().entry_list == ["jo@gmail.com"]


@pytest.mark.parametrize("wrong", ["not an address", "northfield", "@northfield.example", "a@b"])
def test_entries_that_are_neither_are_refused(
    client: Client, document: Document, wrong: str
) -> None:
    response = create(client, document, wrong)
    assert response.status_code == 400
    assert "Enter email addresses or domains" in response.content.decode()
    assert not Link.objects.exists()


def test_a_link_for_more_than_one_person_needs_verification(
    client: Client, document: Document
) -> None:
    data = {"email": "northfield.example", "note": "", "require_verification": ""}
    response = client.post(f"/admin/links/for/{document.token}", data, secure=True)
    assert response.status_code == 400
    assert "needs email verification" in response.content.decode()
    assert not Link.objects.exists()


def test_a_link_for_one_address_can_still_skip_verification(
    client: Client, document: Document
) -> None:
    data = {"email": PRIYA, "note": "", "require_verification": ""}
    assert client.post(f"/admin/links/for/{document.token}", data, secure=True).status_code == 302
    assert not Link.objects.get().require_verification


@pytest.mark.usefixtures("email_on")
def test_emailing_goes_to_each_address_not_the_domain(client: Client, document: Document) -> None:
    create(client, document, f"northfield.example, {ADVISER}", action="email")
    assert [message.to for message in django_mail.outbox] == [[ADVISER]]
    page = client.get("/admin/links/issued", secure=True).content.decode()
    assert f"Emailed to {ADVISER}." in page
    assert "Copy the link to send it to people at northfield.example." in page


@pytest.mark.usefixtures("email_on")
def test_a_link_for_a_domain_alone_is_not_emailed(client: Client, document: Document) -> None:
    create(client, document, "northfield.example", action="email")
    assert not django_mail.outbox
    page = client.get("/admin/links/issued", secure=True).content.decode()
    assert "Copy the link to send it to people at northfield.example." in page
    assert "could not be sent" not in page


# ---------------------------------------------------------------- the link's page


def test_the_links_page_lists_who_and_the_people(client: Client, document: Document) -> None:
    issued = firm_link(document)
    LinkPerson.objects.create(link=issued.link, email=PRIYA, first_opened_at=timezone.now())
    LinkPerson.objects.create(link=issued.link, email="tomas@northfield.example")
    page = client.get(f"/admin/links/{issued.link.ref}", secure=True).content.decode()
    assert "northfield.example" in page and ADVISER in page
    assert PRIYA in page and "First opened" in page
    assert "tomas@northfield.example" in page and "Not opened yet" in page
    assert f'href="/admin/links/{issued.link.ref}/block?email={PRIYA}"' in page
    listing = client.get("/admin/links/", secure=True).content.decode()
    assert f'href="/admin/links/{issued.link.ref}"' in listing


def test_a_link_for_one_address_has_no_blocking(client: Client, document: Document) -> None:
    issued = firm_link(document, [PRIYA])
    page = client.get(f"/admin/links/{issued.link.ref}", secure=True).content.decode()
    assert "Block" not in page
    assert "This link is for one person. To stop it, revoke it." in page


def test_unknown_links_have_no_page(client: Client) -> None:
    assert client.get("/admin/links/ZZZZ", secure=True).status_code == 404


# ---------------------------------------------------------------- blocking one person


def session_for(link: Link, email: str) -> Any:
    response = HttpResponse()
    sessions.begin(browser(), response, link, email=email, verified=True)
    return browser(response)


def test_blocking_asks_first(client: Client, document: Document) -> None:
    issued = firm_link(document)
    url = f"/admin/links/{issued.link.ref}/block?email={PRIYA}"
    page = client.get(url, secure=True).content.decode()
    assert f"Block {PRIYA} on {issued.link.label}?" in page
    assert not LinkPerson.objects.exists()


def test_blocking_ends_one_persons_access(client: Client, document: Document) -> None:
    issued = firm_link(document)
    priya = session_for(issued.link, PRIYA)
    tomas = session_for(issued.link, "tomas@northfield.example")
    response = client.post(
        f"/admin/links/{issued.link.ref}/block", {"email": "Priya@Northfield.example"}, secure=True
    )
    assert response.status_code == 302
    assert response["Location"] == f"/admin/links/{issued.link.ref}#people"
    assert LinkPerson.objects.get(email=PRIYA).blocked_at is not None
    with pytest.raises(NotVerified):
        authorise(priya, issued.token)
    assert authorise(tomas, issued.token).session.email == "tomas@northfield.example"
    event = AuditEvent.objects.get(action="link.person_blocked")
    assert event.target == PRIYA and event.details["link"] == issued.link.label
    page = client.get(f"/admin/links/{issued.link.ref}", secure=True).content.decode()
    assert f"Blocked {PRIYA}." in page and "Unblock" in page


def test_someone_who_has_not_opened_it_can_be_blocked(client: Client, document: Document) -> None:
    issued = firm_link(document)
    data = {"email": "leaver@northfield.example"}
    client.post(f"/admin/links/{issued.link.ref}/block", data, secure=True)
    assert not issued.link.admits("leaver@northfield.example")


def test_only_someone_the_link_admits_can_be_blocked(client: Client, document: Document) -> None:
    issued = firm_link(document)
    data = {"email": "sam@eastgate.example"}
    response = client.post(f"/admin/links/{issued.link.ref}/block", data, secure=True)
    assert response.status_code == 400
    assert "cannot open this link anyway" in response.content.decode()
    assert not LinkPerson.objects.exists()


def test_a_link_for_one_address_cannot_block(client: Client, document: Document) -> None:
    issued = firm_link(document, [PRIYA])
    response = client.post(f"/admin/links/{issued.link.ref}/block", {"email": PRIYA}, secure=True)
    assert response.status_code == 400
    assert not LinkPerson.objects.exists()


def test_unblocking(client: Client, document: Document) -> None:
    issued = firm_link(document)
    LinkPerson.objects.create(link=issued.link, email=PRIYA, blocked_at=timezone.now())
    response = client.post(f"/admin/links/{issued.link.ref}/unblock", {"email": PRIYA}, secure=True)
    assert response.status_code == 302
    assert issued.link.admits(PRIYA)
    assert AuditEvent.objects.filter(action="link.person_unblocked", target=PRIYA).exists()


# ---------------------------------------------------------------- people are recorded


@pytest.mark.usefixtures("email_on")
def test_verifying_records_the_person(document: Document) -> None:
    issued = firm_link(document)
    visitor = Client(REMOTE_ADDR="203.0.113.9")
    visitor.post(f"/v/{issued.token}/code/send", {"email": PRIYA}, secure=True)
    code = str(django_mail.outbox[-1].subject).rsplit(" ", 1)[-1]
    visitor.post(f"/v/{issued.token}/code", {"email": PRIYA, "code": code}, secure=True)
    person = LinkPerson.objects.get()
    assert person.email == PRIYA and person.first_opened_at is None
