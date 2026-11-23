"""A second-factor reset link asks for the admin's current password.

Decided 20 November 2026. A reset link clears the admin's authenticator and
emails them a page to add a new one. Without the password on that page,
someone reading the admin's mailbox while the link was waiting could add
their own authenticator, then prove it on the forgotten-password page and
choose a new password: the whole account from the mailbox alone. With it,
the mailbox gets them neither factor.
"""

import re
import time
from collections.abc import Callable
from typing import Any

import pyotp
import pytest
from django.core import mail as django_mail
from django.test import Client
from django.utils import timezone

from snug.accounts import limits, recovery, sessions, totp
from snug.accounts.models import Admin, AdminSetup
from snug.audit.models import AuditEvent
from snug.core import mail

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("email_on")]

EMAIL = "ruth@fernlea.example"
OTHER = "omar@fernlea.example"
PASSWORD = "correct horse battery staple"
GUESS = "a guessed long passphrase"
NEW_PASSWORD = "a brand new long passphrase"


def make(email: str) -> Admin:
    account = Admin(
        email=email, is_active=True, totp_secret=totp.new_secret(), totp_confirmed_at=timezone.now()
    )
    account.set_password(PASSWORD)
    account.save()
    recovery.issue(account)
    return account


@pytest.fixture
def admin() -> Admin:
    return make(EMAIL)


@pytest.fixture
def other() -> Admin:
    return make(OTHER)


@pytest.fixture(autouse=True)
def sent_now(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run background sends at once."""

    def run(task: Callable[[], object]) -> None:
        task()

    monkeypatch.setattr(mail, "in_background", run)


def signed_in(account: Admin) -> Client:
    browser = Client()
    browser.force_login(account)
    session = browser.session
    session[sessions.LOGIN_AT] = session[sessions.LAST_ACTIVITY] = time.time()
    session.save()
    return browser


def code_for(account: Admin) -> str:
    return pyotp.TOTP(Admin.objects.get(pk=account.pk).totp_secret).now()


def reset_by_another_admin(admin: Admin, other: Admin) -> str:
    """Another admin resets this admin's second factor. Returns the emailed link."""
    data = {"email": OTHER, "code": code_for(admin)}
    response = signed_in(admin).post("/admin/settings/admins/reset", data, secure=True)
    assert response.status_code == 302
    return emailed_link(OTHER)


def replaced_by_themselves(admin: Admin, other: Admin) -> str:
    """The admin starts replacing their own authenticator. Returns the emailed link."""
    data = {"current": PASSWORD, "code": code_for(other)}
    response = signed_in(other).post("/admin/settings/authenticator", data, secure=True)
    assert response.status_code == 302
    return emailed_link(OTHER)


def emailed_link(to: str) -> str:
    links = [
        found.group(1)
        for message in django_mail.outbox
        if message.to == [to]
        and (found := re.search(r"https://snug\.example\.com(/admin/setup/\S+)", str(message.body)))
    ]
    assert links
    return links[-1]


def scan(browser: Client, link: str) -> tuple[str, str]:
    """Open the setup page. Returns the page and the key it shows."""
    page = browser.get(link, secure=True).content.decode()
    key = re.search(r'data-key="([A-Z2-7]+)"', page)
    assert key
    return page, key.group(1)


def finish(browser: Client, link: str, key: str, **fields: str) -> Any:
    return browser.post(link, {"code": pyotp.TOTP(key).now(), **fields}, secure=True)


@pytest.mark.parametrize("how", [reset_by_another_admin, replaced_by_themselves])
def test_the_mailbox_alone_cannot_take_the_account(
    admin: Admin, other: Admin, how: Callable[[Admin, Admin], str]
) -> None:
    link = how(admin, other)
    reader = Client(REMOTE_ADDR="203.0.113.9")  # someone reading the mailbox
    page, key = scan(reader, link)
    assert 'name="current"' in page

    for fields in ({}, {"current": GUESS}):
        response = finish(reader, link, key, **fields)
        assert response.status_code == 400
        assert "not your current password" in response.content.decode()
    account = Admin.objects.get(email=OTHER)
    assert account.totp_secret == "" and account.totp_confirmed_at is None

    # So the forgotten-password page has no second factor to accept, and
    # sends nothing.
    django_mail.outbox.clear()
    reader.post("/admin/login/forgot", {"email": OTHER}, secure=True)
    assert not [m for m in django_mail.outbox if "/admin/setup/" in m.body]
    account.refresh_from_db()
    assert account.check_password(PASSWORD)


@pytest.mark.parametrize("how", [reset_by_another_admin, replaced_by_themselves])
def test_the_admin_finishes_with_their_password(
    admin: Admin, other: Admin, how: Callable[[Admin, Admin], str]
) -> None:
    link = how(admin, other)
    browser = Client()
    _, key = scan(browser, link)
    response = finish(browser, link, key, current=PASSWORD)
    assert response.status_code == 200
    assert len(re.findall(r'<li class="code">([^<]+)</li>', response.content.decode())) == 10
    account = Admin.objects.get(email=OTHER)
    assert account.totp_secret == key and account.check_password(PASSWORD)
    assert not AdminSetup.objects.filter(admin=account).exists()


def test_a_wrong_password_is_audited_and_spends_no_code(admin: Admin, other: Admin) -> None:
    link = reset_by_another_admin(admin, other)
    browser = Client()
    _, key = scan(browser, link)
    now = pyotp.TOTP(key).now()
    browser.post(link, {"code": now, "current": GUESS}, secure=True)
    event = AuditEvent.objects.filter(action="admin.setup_failed").get()
    assert (event.target, event.details) == (OTHER, {"reason": "password"})
    assert AdminSetup.objects.get(admin=other).failures == 1
    # The same code still finishes it, once the password is right.
    response = browser.post(link, {"code": now, "current": PASSWORD}, secure=True)
    assert response.status_code == 200


def test_wrong_passwords_spend_the_link(admin: Admin, other: Admin) -> None:
    link = reset_by_another_admin(admin, other)
    browser = Client()
    _, key = scan(browser, link)
    for _ in range(limits.RESET_CODE_FAILURES_PER_LINK - 1):
        assert finish(browser, link, key, current=GUESS).status_code == 400
    last = finish(browser, link, key, current=GUESS)
    assert last.status_code == 400
    assert "Ask for a new link" in last.content.decode()
    assert not AdminSetup.objects.filter(admin=other).exists()
    assert browser.get(link, secure=True).status_code == 404


def test_an_invite_still_chooses_a_password(admin: Admin) -> None:
    data = {"email": "jo@fernlea.example", "code": code_for(admin)}
    signed_in(admin).post("/admin/settings/admins/invite", data, secure=True)
    page, _ = scan(Client(), emailed_link("jo@fernlea.example"))
    assert 'name="password"' in page and 'name="current"' not in page


# ---------------------------------------------------------------- overlapping requests
#
# Follow-up audit of 1.2.0, F8. Each attempt is counted, under the link's row
# lock, before its password is checked, and none is counted past the fifth.
# Together those hold the limit of five however requests arrive.


def counted_when_checked(monkeypatch: pytest.MonkeyPatch, account: Admin) -> list[int]:
    """The link's count of attempts at the moment each password is checked."""
    seen: list[int] = []
    real = Admin.check_password

    def check(self: Admin, raw: str) -> bool:
        seen.append(AdminSetup.objects.get(admin=account).failures)
        return real(self, raw)

    monkeypatch.setattr(Admin, "check_password", check)
    return seen


def test_each_attempt_is_counted_before_its_password_is_checked(
    admin: Admin, other: Admin, monkeypatch: pytest.MonkeyPatch
) -> None:
    link = reset_by_another_admin(admin, other)
    browser = Client()
    _, key = scan(browser, link)
    seen = counted_when_checked(monkeypatch, other)
    for _ in range(limits.RESET_CODE_FAILURES_PER_LINK):
        finish(browser, link, key, current=GUESS)
    assert seen == list(range(1, limits.RESET_CODE_FAILURES_PER_LINK + 1))


def test_no_password_is_checked_once_five_attempts_are_counted(
    admin: Admin, other: Admin, monkeypatch: pytest.MonkeyPatch
) -> None:
    # As when the fifth attempt is still in flight: counted, not yet answered.
    link = reset_by_another_admin(admin, other)
    browser = Client()
    _, key = scan(browser, link)
    AdminSetup.objects.filter(admin=other).update(failures=limits.RESET_CODE_FAILURES_PER_LINK)
    seen = counted_when_checked(monkeypatch, other)
    response = finish(browser, link, key, current=PASSWORD)
    assert response.status_code == 400
    assert "Ask for a new link" in response.content.decode()
    assert seen == []
    assert Admin.objects.get(email=OTHER).totp_confirmed_at is None


def test_a_right_password_with_a_wrong_code_spends_no_attempt(admin: Admin, other: Admin) -> None:
    # An admin who mistypes their authenticator's code keeps their link: the
    # attempt counted for the password is given back once it proves right.
    link = reset_by_another_admin(admin, other)
    browser = Client()
    _, key = scan(browser, link)
    wrong_code = f"{(int(pyotp.TOTP(key).now()) + 1) % 1_000_000:06d}"
    for _ in range(limits.RESET_CODE_FAILURES_PER_LINK + 1):
        data = {"code": wrong_code, "current": PASSWORD}
        assert browser.post(link, data, secure=True).status_code == 400
    assert AdminSetup.objects.get(admin=other).failures == 0
    assert finish(browser, link, key, current=PASSWORD).status_code == 200
