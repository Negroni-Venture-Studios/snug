"""Links for domains and lists of addresses (issue #174), and the authorisation rules.

A link lists addresses and domains. Anyone it admits verifies their own
address and reads under it: their own session, code, NDA acceptance and
first-open email. Every request checks the session's address is still
admitted, so narrowing the link, or blocking one person, ends their access
at once while everyone else carries on.
"""

import re
from typing import Any

import pytest
from django.core import mail as django_mail
from django.http import HttpResponse
from django.test import Client
from django.utils import timezone

from snug.analytics import notify
from snug.audit.models import AuditEvent
from snug.core import mail
from snug.documents.models import Document, DocumentVersion
from snug.links import service
from snug.links.models import Link, LinkPerson
from snug.nda import terms
from snug.nda.models import NdaVersion
from snug.viewer import codes, limits, sessions
from snug.viewer.access import NotVerified, authorise
from snug.viewer.models import ViewerSession
from tests.test_viewer_access import browser

pytestmark = pytest.mark.django_db

PRIYA = "priya@northfield.example"
TOMAS = "tomas@northfield.example"
ADVISER = "jo.adviser@gmail.example"
STRANGER = "sam@eastgate.example"


@pytest.fixture
def document() -> Document:
    deck = Document.objects.create(title="Seed deck")
    DocumentVersion.objects.create(
        document=deck,
        number=1,
        size_bytes=1,
        sha256="0" * 64,
        page_count=4,
        page_sizes=[[960, 540]] * 4,
    )
    return deck


def for_firm(document: Document, **options: Any) -> service.Issued:
    fields: dict[str, Any] = {
        "entries": ["Northfield.example", ADVISER],
        "note": "Northfield Capital",
        "expires_at": None,
        "require_verification": True,
        "allow_download": True,
        "created_by": None,
    }
    fields.update(options)
    return service.create(document=document, **fields)


@pytest.fixture
def firm(document: Document) -> service.Issued:
    return for_firm(document)


def session_for(link: Link, email: str) -> Any:
    response = HttpResponse()
    sessions.begin(browser(), response, link, email=email, verified=True)
    return browser(response)


def post(client: Client, url: str, data: dict[str, str]) -> Any:
    return client.post(url, data, secure=True)


def code_sent_to(email: str) -> str:
    message = [m for m in django_mail.outbox if m.to == [email]][-1]
    match = re.search(r"\b(\d{6})\b", str(message.subject))
    assert match
    return match.group(1)


def sign_in(client: Client, issued: service.Issued, email: str) -> Any:
    post(client, f"/v/{issued.token}/code/send", {"email": email})
    return post(client, f"/v/{issued.token}/code", {"email": email, "code": code_sent_to(email)})


# ---------------------------------------------------------------- the model


def test_entries_are_kept_in_lower_case_once_each(document: Document) -> None:
    link = for_firm(document, entries=["Northfield.example", " northfield.example", ADVISER]).link
    assert link.entry_list == ["northfield.example", ADVISER]
    assert link.recipient == f"northfield.example, {ADVISER}"
    assert link.email == ""  # not a link for one person
    assert link.has_domain


def test_a_link_for_one_address_keeps_its_email(document: Document) -> None:
    link = for_firm(document, entries=["Priya@Northfield.example"]).link
    assert link.email == PRIYA and not link.has_domain


@pytest.mark.parametrize(
    ("email", "admitted"),
    [
        (PRIYA, True),
        ("PRIYA@NORTHFIELD.EXAMPLE", True),
        (ADVISER, True),
        ("someone.else@gmail.example", False),  # the adviser's address, not their domain
        ("priya@uk.northfield.example", False),  # subdomains are entries of their own
        ("priya@northfield.example.evil", False),
        ("northfield.example", False),
        (STRANGER, False),
        ("", False),
    ],
)
def test_who_a_link_admits(firm: service.Issued, email: str, admitted: bool) -> None:
    assert firm.link.admits(email) is admitted


@pytest.mark.parametrize("entries", [["northfield.example"], [PRIYA, ADVISER]])
def test_only_a_link_for_one_address_can_skip_verification(
    document: Document, entries: list[str]
) -> None:
    with pytest.raises(ValueError, match="one address"):
        for_firm(document, entries=entries, require_verification=False)


# ---------------------------------------------------------------- reading


@pytest.mark.usefixtures("email_on")
def test_anyone_at_the_domain_reads_under_their_own_address(firm: service.Issued) -> None:
    client = Client(REMOTE_ADDR="203.0.113.9")
    response = sign_in(client, firm, TOMAS)
    assert response.status_code == 303
    session = ViewerSession.objects.get()
    assert session.email == TOMAS and session.verified
    assert AuditEvent.objects.get(action="viewer.verified").actor == TOMAS


@pytest.mark.usefixtures("email_on")
def test_an_address_outside_the_link_is_told_plainly(firm: service.Issued) -> None:
    client = Client(REMOTE_ADDR="203.0.113.9")
    response = post(client, f"/v/{firm.token}/code/send", {"email": STRANGER})
    assert response.status_code == 400
    assert "This link was not sent to that address." in response.content.decode()
    assert not django_mail.outbox


@pytest.mark.usefixtures("email_on")
def test_the_code_page_names_the_address_it_was_sent_to(firm: service.Issued) -> None:
    client = Client(REMOTE_ADDR="203.0.113.9")
    page = post(client, f"/v/{firm.token}/code/send", {"email": "Tomas@Northfield.example"})
    assert TOMAS in page.content.decode()
    assert django_mail.outbox[0].to == [TOMAS]


@pytest.mark.usefixtures("email_on")
def test_one_persons_code_does_not_sign_in_another(firm: service.Issued) -> None:
    client = Client(REMOTE_ADDR="203.0.113.9")
    post(client, f"/v/{firm.token}/code/send", {"email": PRIYA})
    code = code_sent_to(PRIYA)
    response = post(client, f"/v/{firm.token}/code", {"email": TOMAS, "code": code})
    assert response.status_code == 400
    assert not ViewerSession.objects.exists()
    # And the code still works for Priya.
    response = post(client, f"/v/{firm.token}/code", {"email": PRIYA, "code": code})
    assert response.status_code == 303


def test_a_code_is_bound_to_its_address(firm: service.Issued) -> None:
    code = codes.issue(firm.link, PRIYA)
    assert not codes.check(firm.link, TOMAS, code).ok
    assert codes.check(firm.link, PRIYA, code).ok


def test_each_address_has_its_own_code(firm: service.Issued) -> None:
    priya = codes.issue(firm.link, PRIYA)
    tomas = codes.issue(firm.link, TOMAS)
    assert codes.check(firm.link, PRIYA, priya).ok
    assert codes.check(firm.link, TOMAS, tomas).ok


def test_two_people_on_one_link_each_have_a_session(firm: service.Issued) -> None:
    priya = session_for(firm.link, PRIYA)
    tomas = session_for(firm.link, TOMAS)
    assert authorise(priya, firm.token).session.email == PRIYA
    assert authorise(tomas, firm.token).session.email == TOMAS


# ---------------------------------------------------------------- authorisation


def test_blocking_one_person_ends_their_session_at_once(firm: service.Issued) -> None:
    priya = session_for(firm.link, PRIYA)
    tomas = session_for(firm.link, TOMAS)
    LinkPerson.objects.create(link=firm.link, email=PRIYA, blocked_at=timezone.now())
    with pytest.raises(NotVerified):
        authorise(priya, firm.token)
    assert authorise(tomas, firm.token).session.email == TOMAS


@pytest.mark.usefixtures("email_on")
def test_a_blocked_person_cannot_get_a_code(firm: service.Issued) -> None:
    LinkPerson.objects.create(link=firm.link, email=PRIYA, blocked_at=timezone.now())
    client = Client(REMOTE_ADDR="203.0.113.9")
    response = post(client, f"/v/{firm.token}/code/send", {"email": PRIYA})
    assert response.status_code == 400
    assert not django_mail.outbox


@pytest.mark.usefixtures("email_on")
def test_a_code_sent_before_a_block_does_not_sign_them_in(firm: service.Issued) -> None:
    client = Client(REMOTE_ADDR="203.0.113.9")
    post(client, f"/v/{firm.token}/code/send", {"email": PRIYA})
    LinkPerson.objects.create(link=firm.link, email=PRIYA, blocked_at=timezone.now())
    response = post(client, f"/v/{firm.token}/code", {"email": PRIYA, "code": code_sent_to(PRIYA)})
    assert response.status_code == 400
    assert not ViewerSession.objects.exists()


def test_narrowing_the_link_ends_sessions_outside_it(firm: service.Issued) -> None:
    priya = session_for(firm.link, PRIYA)
    adviser = session_for(firm.link, ADVISER)
    firm.link.entries = ADVISER
    firm.link.save(update_fields=["entries"])
    with pytest.raises(NotVerified):
        authorise(priya, firm.token)
    assert authorise(adviser, firm.token).session.email == ADVISER


def test_a_domain_links_session_does_not_open_another_link(document: Document) -> None:
    a = for_firm(document)
    b = for_firm(document)
    request = session_for(a.link, PRIYA)
    with pytest.raises(NotVerified):
        authorise(request, b.token)


def test_a_session_for_an_address_the_link_never_admitted_is_refused(
    firm: service.Issued,
) -> None:
    # A session can only be begun by a code, which only an admitted address
    # gets; even so, authorise checks the address itself.
    with pytest.raises(NotVerified):
        authorise(session_for(firm.link, STRANGER), firm.token)


# ---------------------------------------------------------------- each person on their own


def test_each_person_accepts_the_nda(firm: service.Issued) -> None:
    nda = NdaVersion.objects.create(number=1, text="Keep it confidential.", sha256="1" * 64)
    terms.accept(firm.link, nda, name="Priya", email=PRIYA, ip=None, user_agent="")
    assert terms.accepted(firm.link, nda, PRIYA)
    assert not terms.accepted(firm.link, nda, TOMAS)
    _record, created = terms.accept(
        firm.link, nda, name="Tomas", email=TOMAS, ip=None, user_agent=""
    )
    assert created


def test_each_persons_first_open_is_claimed_once(firm: service.Issued) -> None:
    assert notify.claim(firm.link, PRIYA)
    assert not notify.claim(firm.link, PRIYA)
    assert notify.claim(firm.link, TOMAS)
    firm.link.refresh_from_db()
    assert firm.link.first_opened_at is not None


# ---------------------------------------------------------------- how many people


@pytest.mark.usefixtures("email_on")
def test_a_link_sends_codes_to_at_most_ten_new_addresses_an_hour(
    firm: service.Issued, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mail, "in_background", lambda task: task())
    for number in range(limits.NEW_ADDRESSES_PER_LINK.limit):
        client = Client(REMOTE_ADDR=f"203.0.113.{number + 1}")
        email = f"partner{number}@northfield.example"
        assert post(client, f"/v/{firm.token}/code/send", {"email": email}).status_code == 200
    client = Client(REMOTE_ADDR="198.51.100.20")
    response = post(client, f"/v/{firm.token}/code/send", {"email": "eleventh@northfield.example"})
    assert response.status_code == 429
    assert "sent codes to many people" in response.content.decode()
    # Someone already sent a code this hour can still have another.
    again = post(client, f"/v/{firm.token}/code/send", {"email": "partner0@northfield.example"})
    assert again.status_code == 200


@pytest.mark.usefixtures("email_on")
def test_the_landing_page_asks_for_a_work_address_and_names_no_one(firm: service.Issued) -> None:
    page = Client().get(f"/v/{firm.token}", secure=True).content.decode()
    assert "Enter your work email address." in page
    assert "northfield.example" not in page and ADVISER not in page
