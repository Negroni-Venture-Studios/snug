"""The viewer's pages: landing, code, one-click open, and unavailable."""

import re
from typing import Any

import pytest
from django.core import mail as django_mail
from django.test import Client

from snug.audit.models import AuditEvent
from snug.documents.models import Document, DocumentVersion
from snug.links import service
from snug.links.models import Link
from snug.viewer import sessions
from snug.viewer.models import VerificationCode, ViewerSession

pytestmark = pytest.mark.django_db

INVESTOR = "priya@northfield.example"
STRANGER = "sam@eastgate.example"


@pytest.fixture
def document() -> Document:
    deck = Document.objects.create(title="Seed deck")
    DocumentVersion.objects.create(
        document=deck,
        number=1,
        size_bytes=1,
        sha256="0" * 64,
        page_count=18,
        page_sizes=[[960, 540]] * 18,
    )
    return deck


def issue(document: Document, **options: Any) -> service.Issued:
    fields: dict[str, Any] = {
        "email": INVESTOR,
        "note": "Partner, Northfield",
        "expires_at": None,
        "require_verification": True,
        "allow_download": True,
        "created_by": None,
    }
    fields.update(options)
    return service.create(document=document, **fields)


@pytest.fixture
def issued(document: Document) -> service.Issued:
    return issue(document)


@pytest.fixture
def browser() -> Client:
    return Client(REMOTE_ADDR="203.0.113.9")


def path(issued: service.Issued, suffix: str = "") -> str:
    return f"/v/{issued.token}{suffix}"


def get(browser: Client, url: str) -> Any:
    return browser.get(url, secure=True)


def post(browser: Client, url: str, data: dict[str, str] | None = None) -> Any:
    return browser.post(url, data or {}, secure=True)


def sent_code() -> str:
    match = re.search(r"\b(\d{6})\b", str(django_mail.outbox[-1].subject))
    assert match
    return match.group(1)


def verify(browser: Client, issued: service.Issued) -> Any:
    post(browser, path(issued, "/code/send"), {"email": INVESTOR})
    return post(browser, path(issued, "/code"), {"email": INVESTOR, "code": sent_code()})


# ---------------------------------------------------------------- landing


@pytest.mark.usefixtures("email_on")
def test_landing_asks_for_the_email_address(browser: Client, issued: service.Issued) -> None:
    page = get(browser, path(issued)).content.decode()
    assert "You have been sent <strong>Seed deck</strong>" in page
    assert 'name="email"' in page and "Send code" in page
    assert "18 pages" in page
    # The landing page never reveals who the link was sent to.
    assert INVESTOR not in page


def test_landing_starts_nothing(browser: Client, issued: service.Issued) -> None:
    get(browser, path(issued))
    assert not ViewerSession.objects.exists() and not VerificationCode.objects.exists()
    assert sessions.COOKIE not in browser.cookies


@pytest.mark.parametrize("suffix", ["", "/code", "/code/send", "/open"])
def test_unknown_links_are_unavailable(browser: Client, suffix: str) -> None:
    method = get if suffix == "" else post
    response = method(browser, f"/v/not-a-real-token{suffix}")
    assert response.status_code == 404
    assert "This link is not available" in response.content.decode()


def test_revoked_and_expired_links_look_the_same(browser: Client, document: Document) -> None:
    from datetime import timedelta

    from django.utils import timezone

    revoked = issue(document)
    service.revoke(revoked.link, by=None)
    expired = issue(document, expires_at=timezone.now() + timedelta(days=1))
    Link.objects.filter(pk=expired.link.pk).update(expires_at=timezone.now() - timedelta(days=1))
    unknown = get(browser, "/v/not-a-real-token").content
    assert get(browser, path(revoked)).content == unknown
    assert get(browser, path(expired)).content == unknown


# ---------------------------------------------------------------- sending a code


@pytest.mark.usefixtures("email_on")
def test_the_right_address_gets_a_code(browser: Client, issued: service.Issued) -> None:
    response = post(browser, path(issued, "/code/send"), {"email": " Priya@Northfield.example "})
    assert response.status_code == 200
    page = response.content.decode()
    assert "Check your email" in page and INVESTOR in page
    message = django_mail.outbox[0]
    assert message.to == [INVESTOR]
    assert re.fullmatch(r"Your code for Seed deck: \d{6}", str(message.subject))
    assert "Seed deck" in message.body and "ignore this email" in message.body
    assert AuditEvent.objects.filter(action="viewer.code_sent", target=INVESTOR).exists()


@pytest.mark.usefixtures("email_on")
def test_the_subject_names_the_company(
    browser: Client, issued: service.Issued, settings: Any
) -> None:
    from dataclasses import replace

    settings.CONFIG = replace(settings.CONFIG, company_name="Fernlea Robotics")
    page = get(browser, path(issued)).content.decode()
    assert (
        "<strong>Fernlea Robotics</strong> has shared <strong>Seed deck</strong> with you" in page
    )
    post(browser, path(issued, "/code/send"), {"email": INVESTOR})
    assert re.fullmatch(
        r"Your code for Fernlea Robotics: \d{6}", str(django_mail.outbox[0].subject)
    )


@pytest.mark.usefixtures("email_on")
def test_a_wrong_address_is_told_plainly(browser: Client, issued: service.Issued) -> None:
    response = post(browser, path(issued, "/code/send"), {"email": STRANGER})
    assert response.status_code == 400
    page = response.content.decode()
    assert "That is not the address this link was sent to" in page
    assert INVESTOR not in page
    assert django_mail.outbox == [] and not VerificationCode.objects.exists()
    event = AuditEvent.objects.get(action="viewer.code_refused")
    assert event.actor == STRANGER and event.details["reason"] == "wrong_email"


@pytest.mark.usefixtures("email_on")
def test_code_requests_are_rate_limited_per_link(browser: Client, issued: service.Issued) -> None:
    for n in range(5):
        other = Client(REMOTE_ADDR=f"203.0.113.{20 + n}")
        assert post(other, path(issued, "/code/send"), {"email": INVESTOR}).status_code == 200
    response = post(browser, path(issued, "/code/send"), {"email": INVESTOR})
    assert response.status_code == 429
    assert "Too many attempts" in response.content.decode()
    assert len(django_mail.outbox) == 5


@pytest.mark.usefixtures("email_on")
def test_past_the_cap_on_wrong_addresses_the_answer_turns_neutral(
    issued: service.Issued, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("snug.viewer.views._in_background", lambda task: task())

    # Guesses from many addresses meet a ceiling on each link. Past it, every
    # request gets the same neutral reply, so guessing learns nothing; and the
    # right address still gets a code, so nobody can lock the owner out.
    def ask(n: int, email: str) -> Any:
        browser = Client(REMOTE_ADDR=f"198.51.100.{n + 1}")
        return post(browser, path(issued, "/code/send"), {"email": email})

    plain = [ask(n, f"guess{n}@northfield.example").status_code for n in range(10)]
    assert plain == [400] * 10
    wrong = ask(10, "guess10@northfield.example")
    right = ask(11, INVESTOR)
    neutral = "If that is the address this link was sent to, we have sent it a six-digit code"
    for response in (wrong, right):
        page = response.content.decode()
        assert response.status_code == 200 and neutral in page
        assert INVESTOR not in page.replace('value="priya@northfield.example"', "")
        assert "not the address" not in page
    assert [message.to for message in django_mail.outbox] == [[INVESTOR]]


@pytest.fixture
def neutral(issued: service.Issued) -> None:
    """Put the link past its cap on wrong addresses."""
    from snug.core import ratelimit
    from snug.viewer import limits

    for _ in range(limits.WRONG_EMAIL_PER_LINK.limit):
        ratelimit.hit(limits.WRONG_EMAIL_PER_LINK, str(issued.link.pk))


@pytest.mark.usefixtures("email_on", "neutral")
def test_in_neutral_mode_a_wrong_code_never_says_whether_one_was_sent(
    issued: service.Issued,
) -> None:
    # With a live code, "4 tries left"; with none, "expired". Either would
    # tell a guesser whether their address was right, so both read the same.
    pages = []
    for email in ("guess@northfield.example", INVESTOR):
        browser = Client(REMOTE_ADDR="198.51.100.200")
        post(browser, path(issued, "/code/send"), {"email": email})
        response = post(browser, path(issued, "/code"), {"code": "000000"})
        pages.append((response.status_code, response.content.decode()))
    for status, page in pages:
        assert status == 400 and "That code is not right." in page
        assert "tries left" not in page and "expired" not in page


@pytest.mark.usefixtures("email_on", "neutral")
def test_in_neutral_mode_the_daily_cap_reads_like_a_wrong_code(
    issued: service.Issued, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The daily cap is only reached against a live code, so its own message
    # would also say that an address was right.
    from snug.viewer import codes

    monkeypatch.setattr(codes, "check", lambda link, typed: codes.Result(False, 0, limited=True))
    response = post(Client(), path(issued, "/code"), {"code": "000000"})
    assert response.status_code == 400
    page = response.content.decode()
    assert "That code is not right." in page and "today" not in page


@pytest.mark.usefixtures("email_on", "neutral")
def test_in_neutral_mode_the_code_is_sent_in_the_background(
    issued: service.Issued, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The reply must not wait for the email, or its timing would tell a
    # guesser that the address was right.
    started: list[Any] = []
    monkeypatch.setattr("snug.viewer.views._in_background", started.append)
    response = post(Client(), path(issued, "/code/send"), {"email": INVESTOR})
    assert response.status_code == 200 and django_mail.outbox == []
    assert len(started) == 1
    started[0]()
    assert [message.to for message in django_mail.outbox] == [[INVESTOR]]


@pytest.mark.usefixtures("email_on")
def test_background_sending_really_sends(issued: service.Issued) -> None:
    from snug.core import mail
    from snug.viewer import views

    via = mail.outgoing()  # read on the request thread, as the view does
    thread = views._in_background(
        lambda: views._send(issued.link, "123456", "Fernlea Robotics", via)
    )
    thread.join(timeout=5)
    assert [message.to for message in django_mail.outbox] == [[INVESTOR]]


@pytest.mark.usefixtures("email_on")
def test_code_requests_are_rate_limited_per_address(browser: Client, document: Document) -> None:
    links = [issue(document) for _ in range(21)]
    statuses = [
        post(browser, path(link, "/code/send"), {"email": STRANGER}).status_code for link in links
    ]
    assert statuses[:20] == [400] * 20 and statuses[20] == 429


@pytest.mark.usefixtures("email_on")
def test_codes_to_one_inbox_are_rate_limited(document: Document) -> None:
    links = [issue(document) for _ in range(11)]
    statuses = [
        post(
            Client(REMOTE_ADDR=f"203.0.113.{50 + n}"), path(link, "/code/send"), {"email": INVESTOR}
        ).status_code
        for n, link in enumerate(links)
    ]
    assert statuses[:10] == [200] * 10 and statuses[10] == 429


def test_without_email_the_page_says_so(browser: Client, issued: service.Issued) -> None:
    page = get(browser, path(issued)).content.decode()
    assert "email is not set up" in page
    response = post(browser, path(issued, "/code/send"), {"email": INVESTOR})
    assert response.status_code == 503
    assert not VerificationCode.objects.exists()


@pytest.mark.usefixtures("email_on")
def test_a_failed_send_is_reported(
    browser: Client, issued: service.Issued, monkeypatch: pytest.MonkeyPatch, caplog: Any
) -> None:
    monkeypatch.setattr("snug.core.mail.send", lambda **kwargs: False)
    response = post(browser, path(issued, "/code/send"), {"email": INVESTOR})
    assert response.status_code == 503
    assert "could not send" in response.content.decode()
    assert not VerificationCode.objects.exists()
    assert AuditEvent.objects.filter(action="viewer.code_failed_to_send").exists()


# ---------------------------------------------------------------- entering the code


@pytest.mark.usefixtures("email_on")
def test_the_right_code_opens_the_document(browser: Client, issued: service.Issued) -> None:
    response = verify(browser, issued)
    assert response.status_code == 303 and response["Location"] == path(issued)
    assert sessions.COOKIE in browser.cookies
    page = get(browser, path(issued)).content.decode()
    assert 'alt="Page 1 of 18"' in page and "Send code" not in page
    assert AuditEvent.objects.filter(action="viewer.verified", actor=INVESTOR).exists()


@pytest.mark.usefixtures("email_on")
def test_a_wrong_code_says_how_many_tries_are_left(browser: Client, issued: service.Issued) -> None:
    post(browser, path(issued, "/code/send"), {"email": INVESTOR})
    wrong = f"{(int(sent_code()) + 1) % 1_000_000:06d}"
    response = post(browser, path(issued, "/code"), {"email": INVESTOR, "code": wrong})
    assert response.status_code == 400
    assert "That code is not right. You have 4 tries left." in response.content.decode()
    assert sessions.COOKIE not in browser.cookies
    assert AuditEvent.objects.filter(action="viewer.code_wrong").exists()
    for _ in range(3):
        post(browser, path(issued, "/code"), {"email": INVESTOR, "code": wrong})
    last = post(browser, path(issued, "/code"), {"email": INVESTOR, "code": wrong}).content.decode()
    assert "Send a new code" in last and "tries left" not in last


@pytest.mark.usefixtures("email_on")
def test_code_checks_are_rate_limited_per_address(browser: Client, document: Document) -> None:
    links = [issue(document) for _ in range(31)]
    statuses = [
        post(browser, path(link, "/code"), {"email": INVESTOR, "code": "000000"}).status_code
        for link in links
    ]
    assert 429 not in statuses[:30] and statuses[30] == 429


@pytest.mark.usefixtures("email_on")
def test_the_code_page_does_not_echo_a_forged_address(
    browser: Client, issued: service.Issued
) -> None:
    page = post(
        browser, path(issued, "/code"), {"email": STRANGER, "code": "000000"}
    ).content.decode()
    assert STRANGER not in page and INVESTOR not in page


def test_getting_the_code_page_goes_back_to_the_start(
    browser: Client, issued: service.Issued
) -> None:
    response = get(browser, path(issued, "/code"))
    assert response.status_code == 303 and response["Location"] == path(issued)


@pytest.mark.usefixtures("email_on")
def test_a_verified_viewer_skips_the_gate_on_return(
    browser: Client, issued: service.Issued
) -> None:
    verify(browser, issued)
    page = get(browser, path(issued)).content.decode()
    assert "Send code" not in page


@pytest.mark.usefixtures("email_on")
def test_revoking_ends_an_open_session(browser: Client, issued: service.Issued) -> None:
    verify(browser, issued)
    service.revoke(issued.link, by=None)
    assert get(browser, path(issued)).status_code == 404


# ---------------------------------------------------------------- links without verification


def test_an_unverified_link_needs_one_click(browser: Client, document: Document) -> None:
    issued = issue(document, require_verification=False)
    page = get(browser, path(issued)).content.decode()
    assert "Open Seed deck" in page and 'name="email"' not in page
    assert not ViewerSession.objects.exists()  # a mail scanner's visit starts nothing

    response = post(browser, path(issued, "/open"))
    assert response.status_code == 303 and response["Location"] == path(issued)
    assert 'alt="Page 1 of 18"' in get(browser, path(issued)).content.decode()
    assert AuditEvent.objects.filter(action="viewer.opened", actor=INVESTOR).exists()


def test_open_is_refused_for_a_link_that_needs_verification(
    browser: Client, issued: service.Issued
) -> None:
    response = post(browser, path(issued, "/open"))
    assert response.status_code == 303 and response["Location"] == path(issued)
    assert not ViewerSession.objects.exists()


# ---------------------------------------------------------------- forms


def test_viewer_forms_need_csrf(issued: service.Issued) -> None:
    strict = Client(enforce_csrf_checks=True)
    for suffix in ("/code/send", "/code", "/open"):
        assert post(strict, path(issued, suffix), {"email": INVESTOR}).status_code == 403


def test_viewer_pages_are_not_cached_and_set_no_admin_session(
    browser: Client, issued: service.Issued
) -> None:
    response = get(browser, path(issued))
    assert "no-store" in response["Cache-Control"]
    assert not any("session" in name and "viewer" not in name for name in browser.cookies)
