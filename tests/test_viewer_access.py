"""The viewer's access rules: codes, sessions and the single authorisation path.

Every viewer request for content goes through snug.viewer.access.authorise.
These tests walk its checks in order, including the cases the brief names:
revoked links with open sessions, expired links, link A's session used against
link B, and a document outside the link.
"""

from datetime import timedelta
from http.cookies import SimpleCookie

import pytest
from django.http import HttpRequest, HttpResponse
from django.test import RequestFactory
from django.utils import timezone

from snug.documents.models import Document
from snug.links import service
from snug.links.models import Link
from snug.viewer import codes, sessions
from snug.viewer.access import NotVerified, Unavailable, authorise, find_link
from snug.viewer.models import VerificationCode, ViewerSession

pytestmark = pytest.mark.django_db

INVESTOR = "priya@northfield.example"
OTHER = "sam@eastgate.example"


def issue(document: Document, email: str = INVESTOR, **options: object) -> service.Issued:
    fields: dict[str, object] = {
        "note": "",
        "expires_at": None,
        "require_verification": True,
        "allow_download": True,
        "created_by": None,
    }
    fields.update(options)
    return service.create(document=document, email=email, **fields)  # type: ignore[arg-type]


@pytest.fixture
def document() -> Document:
    return Document.objects.create(title="Seed deck")


@pytest.fixture
def issued(document: Document) -> service.Issued:
    return issue(document)


def browser(response: HttpResponse | None = None, cookie: str | None = None) -> HttpRequest:
    """A request from a browser holding whatever cookie the response set."""
    request = RequestFactory().get("/v/x", secure=True)
    if response is not None:
        cookie = response.cookies[sessions.COOKIE].value
    if cookie is not None:
        request.COOKIES[sessions.COOKIE] = cookie
    return request


def verified_browser(link: Link, request: HttpRequest | None = None) -> HttpRequest:
    response = HttpResponse()
    sessions.begin(request or browser(), response, link, verified=True)
    return browser(response)


# ---------------------------------------------------------------- the link


def test_an_unknown_token_is_unavailable() -> None:
    with pytest.raises(Unavailable):
        find_link("not-a-real-token")
    with pytest.raises(Unavailable):
        authorise(browser(), "not-a-real-token")


def test_a_revoked_link_with_an_open_session_is_unavailable(issued: service.Issued) -> None:
    request = verified_browser(issued.link)
    assert authorise(request, issued.token).link == issued.link
    service.revoke(issued.link, by=None)  # type: ignore[arg-type]
    with pytest.raises(Unavailable):
        authorise(request, issued.token)


def test_an_expired_link_with_an_open_session_is_unavailable(issued: service.Issued) -> None:
    request = verified_browser(issued.link)
    Link.objects.filter(pk=issued.link.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
    with pytest.raises(Unavailable):
        authorise(request, issued.token)


def test_a_reissued_links_old_address_is_unavailable(issued: service.Issued) -> None:
    request = verified_browser(issued.link)
    new = service.reissue(issued.link)
    with pytest.raises(Unavailable):
        authorise(request, issued.token)
    # The person it was sent to keeps their session at the new address.
    assert authorise(request, new.token).link == issued.link


# ---------------------------------------------------------------- the session


def test_without_a_session_the_viewer_must_verify(issued: service.Issued) -> None:
    with pytest.raises(NotVerified) as raised:
        authorise(browser(), issued.token)
    assert raised.value.link == issued.link


def test_an_unknown_cookie_is_not_a_session(issued: service.Issued) -> None:
    verified_browser(issued.link)
    with pytest.raises(NotVerified):
        authorise(browser(cookie="made-up"), issued.token)


def test_link_as_session_does_not_open_link_b(document: Document) -> None:
    a = issue(document)
    b = issue(document, email=OTHER)
    request = verified_browser(a.link)
    assert authorise(request, a.token).link == a.link
    with pytest.raises(NotVerified):
        authorise(request, b.token)


def test_link_as_session_does_not_open_link_b_for_the_same_person(document: Document) -> None:
    # Two links to the same address are still two links: each is verified,
    # revoked and audited on its own.
    a = issue(document)
    b = issue(document)
    request = verified_browser(a.link)
    with pytest.raises(NotVerified):
        authorise(request, b.token)


def test_an_expired_session_must_verify_again(issued: service.Issued) -> None:
    request = verified_browser(issued.link)
    ViewerSession.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    with pytest.raises(NotVerified):
        authorise(request, issued.token)


def test_a_session_for_another_address_is_refused(issued: service.Issued) -> None:
    request = verified_browser(issued.link)
    ViewerSession.objects.update(email=OTHER)
    with pytest.raises(NotVerified):
        authorise(request, issued.token)


def test_an_unverified_session_does_not_open_a_link_that_needs_verification(
    document: Document,
) -> None:
    open_link = issue(document, require_verification=False)
    response = HttpResponse()
    sessions.begin(browser(), response, open_link.link, verified=False)
    request = browser(response)
    assert authorise(request, open_link.token).session.verified is False
    Link.objects.filter(pk=open_link.link.pk).update(require_verification=True)
    with pytest.raises(NotVerified):
        authorise(request, open_link.token)


# ---------------------------------------------------------------- the document


def test_the_links_own_document_is_allowed(issued: service.Issued, document: Document) -> None:
    request = verified_browser(issued.link)
    assert authorise(request, issued.token).document == document
    assert authorise(request, issued.token, document_token=document.token).document == document


def test_a_document_outside_the_link_is_unavailable(issued: service.Issued) -> None:
    request = verified_browser(issued.link)
    elsewhere = Document.objects.create(title="Board minutes")
    with pytest.raises(Unavailable):
        authorise(request, issued.token, document_token=elsewhere.token)
    with pytest.raises(Unavailable):
        authorise(request, issued.token, document_token="no-such-document")


def test_the_checks_run_in_order(issued: service.Issued) -> None:
    # A revoked link is unavailable before the session is even looked at, so a
    # stranger learns nothing about whether anyone has verified.
    service.revoke(issued.link, by=None)  # type: ignore[arg-type]
    with pytest.raises(Unavailable):
        authorise(browser(), issued.token)


# ---------------------------------------------------------------- sessions


def test_the_session_cookie(issued: service.Issued) -> None:
    response = HttpResponse()
    sessions.begin(browser(), response, issued.link, verified=True)
    cookie = response.cookies[sessions.COOKIE]
    assert sessions.COOKIE.startswith("__Host-")
    assert cookie["httponly"] and cookie["secure"] and cookie["path"] == "/"
    # Lax, so the session survives arriving from a link in an email.
    assert cookie["samesite"] == "Lax"
    assert int(cookie["max-age"]) == 7 * 24 * 60 * 60
    row = ViewerSession.objects.get()
    assert row.email == INVESTOR and row.verified
    assert cookie.value not in row.token_hash
    assert len(cookie.value) >= 43  # 256 bits


def test_verifying_again_rotates_the_cookie_and_keeps_other_links(document: Document) -> None:
    a = issue(document)
    b = issue(document, email=OTHER)
    first = verified_browser(a.link)
    second = verified_browser(b.link, request=first)
    assert second.COOKIES[sessions.COOKIE] != first.COOKIES[sessions.COOKIE]
    assert authorise(second, a.token).link == a.link
    assert authorise(second, b.token).link == b.link
    # The old cookie no longer opens anything.
    with pytest.raises(NotVerified):
        authorise(first, a.token)


def test_session_lifetime_follows_the_configuration(
    issued: service.Issued, settings: object
) -> None:
    from dataclasses import replace

    from django.conf import settings as django_settings

    config = replace(django_settings.CONFIG, viewer_session_days=2)
    setattr(settings, "CONFIG", config)  # noqa: B010 - pytest-django's settings fixture
    response = HttpResponse()
    sessions.begin(browser(), response, issued.link, verified=True)
    assert int(response.cookies[sessions.COOKIE]["max-age"]) == 2 * 24 * 60 * 60
    row = ViewerSession.objects.get()
    assert timedelta(days=2) - (row.expires_at - timezone.now()) < timedelta(minutes=1)


def test_a_cookie_that_is_too_long_is_ignored(issued: service.Issued) -> None:
    assert sessions.lookup(browser(cookie="x" * 500), issued.link) is None


# ---------------------------------------------------------------- codes


def test_a_code_works_once(issued: service.Issued) -> None:
    code = codes.issue(issued.link)
    assert len(code) == 6 and code.isascii() and code.isdigit()
    assert codes.check(issued.link, code).ok
    assert not codes.check(issued.link, code).ok


def test_codes_are_not_stored(issued: service.Issued) -> None:
    code = codes.issue(issued.link)
    for value in VerificationCode.objects.values().get().values():
        assert code not in str(value)


def test_a_new_code_replaces_the_old_one(issued: service.Issued) -> None:
    old = codes.issue(issued.link)
    new = codes.issue(issued.link)
    if old != new:  # one in a million they match
        assert not codes.check(issued.link, old).ok
    assert codes.check(issued.link, new).ok


def test_five_wrong_codes_end_the_code(issued: service.Issued) -> None:
    code = codes.issue(issued.link)
    wrong = f"{(int(code) + 1) % 1_000_000:06d}"
    left = [codes.check(issued.link, wrong).tries_left for _ in range(5)]
    assert left == [4, 3, 2, 1, 0]
    result = codes.check(issued.link, code)
    assert not result.ok and result.tries_left == 0
    assert not VerificationCode.objects.exists()


def test_an_expired_code_is_refused(issued: service.Issued) -> None:
    code = codes.issue(issued.link)
    VerificationCode.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    result = codes.check(issued.link, code)
    assert not result.ok and result.tries_left == 0


def test_codes_expire_after_ten_minutes(issued: service.Issued) -> None:
    codes.issue(issued.link)
    row = VerificationCode.objects.get()
    assert timedelta(minutes=10) - (row.expires_at - timezone.now()) < timedelta(seconds=30)


def test_one_links_code_does_not_open_another(document: Document) -> None:
    a = issue(document)
    b = issue(document)
    code = codes.issue(a.link)
    assert not codes.check(b.link, code).ok
    assert codes.check(a.link, code).ok


def test_reissuing_a_link_ends_its_code(issued: service.Issued) -> None:
    code = codes.issue(issued.link)
    service.reissue(issued.link)
    assert not codes.check(issued.link, code).ok


@pytest.mark.parametrize("typed", ["", "12345", "1234567", "abcdef", "١٢٣٤٥٦", "12 34 56x"])
def test_malformed_codes_are_wrong(issued: service.Issued, typed: str) -> None:
    codes.issue(issued.link)
    result = codes.check(issued.link, typed)
    assert not result.ok and result.tries_left == 4


def test_spaces_and_dashes_in_a_code_are_forgiven(issued: service.Issued) -> None:
    code = codes.issue(issued.link)
    assert codes.check(issued.link, f" {code[:3]} {code[3:]} ").ok


def test_without_a_code_nothing_matches(issued: service.Issued) -> None:
    result = codes.check(issued.link, "123456")
    assert not result.ok and result.tries_left == 0


def test_cookies_parse(issued: service.Issued) -> None:
    # The cookie survives a real Set-Cookie round trip.
    response = HttpResponse()
    sessions.begin(browser(), response, issued.link, verified=True)
    parsed = SimpleCookie(response.cookies.output(header="").strip())
    assert parsed[sessions.COOKIE].value == response.cookies[sessions.COOKIE].value
