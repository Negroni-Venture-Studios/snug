"""A forgotten password: an emailed link for one hour, and the second factor still needed.

Asked for 5 November 2026 ("humans do"). Decided 6 November 2026: the page
that sets the new password also asks for a code from the authenticator, or a
recovery code, so someone in the admin's mailbox cannot change it at all.
"""

import re
from collections.abc import Callable
from datetime import timedelta
from io import StringIO
from typing import Any

import pyotp
import pytest
from django.contrib.auth.hashers import check_password
from django.core import mail as django_mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client
from django.utils import timezone

from snug.accounts import limits, recovery, totp
from snug.accounts.models import Admin, AdminSetup
from snug.audit.models import AuditEvent
from snug.core import mail

pytestmark = pytest.mark.django_db

EMAIL = "ruth@fernlea.example"
PASSWORD = "correct horse battery staple"
NEW = "a brand new long passphrase"
IP = "198.51.100.7"


@pytest.fixture
def admin() -> Admin:
    account = Admin(
        email=EMAIL,
        is_active=True,
        totp_secret=totp.new_secret(),
        totp_confirmed_at=timezone.now(),
    )
    account.set_password(PASSWORD)
    account.save()
    return account


@pytest.fixture
def client() -> Client:
    return Client(REMOTE_ADDR=IP)


@pytest.fixture
def sent_now(monkeypatch: pytest.MonkeyPatch) -> list[Callable[[], object]]:
    """Run background sends at once, and note that they were backgrounded."""
    started: list[Callable[[], object]] = []

    def run(task: Callable[[], object]) -> None:
        started.append(task)
        task()

    monkeypatch.setattr(mail, "in_background", run)
    return started


def post(client: Client, path: str, data: dict[str, str]) -> Any:
    return client.post(path, data, secure=True)


def ask(client: Client, email: str = EMAIL) -> Any:
    return post(client, "/admin/login/forgot", {"email": email})


def link_in(message: Any) -> str:
    found = re.search(r"https://snug\.example\.com(/admin/setup/[A-Za-z0-9_-]+)", message.body)
    assert found, message.body
    return found.group(1)


def code_for(admin: Admin) -> str:
    return pyotp.TOTP(admin.totp_secret).now()


def actions() -> list[str]:
    return list(AuditEvent.objects.order_by("id").values_list("action", flat=True))


# ---------------------------------------------------------------- asking for a link


def test_the_sign_in_page_offers_it(client: Client) -> None:
    page = client.get("/admin/login", secure=True).content.decode()
    assert '<a href="/admin/login/forgot">Forgotten your password?</a>' in page


@pytest.mark.usefixtures("email_on")
def test_a_link_is_emailed_for_one_hour(
    client: Client, admin: Admin, sent_now: list[object]
) -> None:
    response = ask(client)
    assert response.status_code == 200
    assert "works for one hour" in response.content.decode()
    [message] = django_mail.outbox
    assert message.to == [EMAIL]
    assert message.subject == "Choose a new password for Snug"
    assert "works once, for one hour" in message.body
    assert "your authenticator" in message.body
    assert message.body.rstrip().endswith("you can ignore this email.")  # no reassurance after it
    row = AdminSetup.objects.get(admin=admin)
    assert row.purpose == AdminSetup.Purpose.PASSWORD
    left = row.expires_at - timezone.now()
    assert timedelta(minutes=59) < left <= timedelta(hours=1)
    assert len(sent_now) == 1  # sent without making the reply wait
    assert actions() == ["admin.password_reset_requested"]


@pytest.mark.usefixtures("email_on")
@pytest.mark.parametrize("who", ["unknown", "inactive", "unconfirmed"])
def test_the_reply_never_says_who_is_an_admin(
    client: Client, admin: Admin, who: str, sent_now: list[object]
) -> None:
    other = "nobody@fernlea.example"
    if who == "inactive":
        Admin.objects.create(email=other, is_active=False)
    elif who == "unconfirmed":
        Admin.objects.create(email=other, is_active=True)  # never finished setting up
    real = ask(client, EMAIL).content.decode()
    probe = ask(client, other).content.decode()
    assert probe.replace(other, EMAIL) == real
    assert [m.to for m in django_mail.outbox] == [[EMAIL]]
    assert not AdminSetup.objects.filter(admin__email=other).exists()
    events = AuditEvent.objects.filter(action="admin.password_reset_requested").order_by("id")
    assert [e.details for e in events] == [{"admin": True}, {"admin": False}]


@pytest.mark.usefixtures("email_on", "sent_now")
def test_asking_is_rate_limited_per_email_and_per_ip(client: Client, admin: Admin) -> None:
    for _ in range(limits.FORGOT_PER_ACCOUNT.limit):
        assert ask(client).status_code == 200
    assert ask(client).status_code == 429
    # From another IP address, each email address has its own allowance, up to
    # the per-IP limit, whether or not it belongs to an admin.
    other = Client(REMOTE_ADDR="203.0.113.9")
    statuses = [ask(other, f"n{i}@fernlea.example").status_code for i in range(6)]
    assert statuses == [200] * limits.FORGOT_PER_IP.limit + [429] * (6 - limits.FORGOT_PER_IP.limit)


def test_without_email_the_page_says_what_to_do_instead(client: Client) -> None:
    page = client.get("/admin/login/forgot", secure=True).content.decode()
    assert "Email is not set up" in page
    assert "snug reset-password" in page
    assert 'name="email"' not in page
    assert ask(client).status_code == 404


# ---------------------------------------------------------------- choosing a new password


@pytest.fixture
def link(client: Client, admin: Admin, email_on: None, sent_now: list[object]) -> str:
    ask(client)
    path = link_in(django_mail.outbox[-1])
    django_mail.outbox.clear()
    return path


def choose(client: Client, link: str, code: str, password: str = NEW, again: str = "") -> Any:
    return post(client, link, {"password": password, "password2": again or password, "code": code})


def test_the_page_asks_for_a_password_and_a_code(client: Client, link: str) -> None:
    page = client.get(link, secure=True).content.decode()
    assert "Choose a new password" in page
    assert 'autocomplete="new-password"' in page
    assert 'name="code"' in page
    assert "qr-tile" not in page  # the authenticator is not being set up again


def test_a_new_password_with_a_code_from_the_app(client: Client, admin: Admin, link: str) -> None:
    elsewhere = Client(REMOTE_ADDR="203.0.113.9")
    elsewhere.force_login(admin)
    response = choose(client, link, code_for(admin))
    assert response.status_code == 200
    page = response.content.decode()
    assert "Password changed" in page and 'href="/admin/login"' in page
    admin.refresh_from_db()
    assert check_password(NEW, admin.password)
    assert not AdminSetup.objects.exists()  # the link worked once
    assert elsewhere.get("/admin/", secure=True).status_code == 302  # signed out everywhere
    [notice] = django_mail.outbox
    assert notice.to == [EMAIL] and notice.subject == "Your Snug password was changed"
    assert "/admin/setup/" not in notice.body
    assert actions()[-1] == "admin.password_reset"
    assert AuditEvent.objects.get(action="admin.password_reset").details == {"method": "totp"}
    assert client.get(link, secure=True).status_code == 404


def test_a_recovery_code_works_instead(client: Client, admin: Admin, link: str) -> None:
    codes = recovery.issue(admin)
    assert choose(client, link, codes[0]).status_code == 200
    admin.refresh_from_db()
    assert check_password(NEW, admin.password)
    assert recovery.remaining(admin) == len(codes) - 1
    event = AuditEvent.objects.get(action="admin.password_reset")
    assert event.details == {"method": "recovery", "recovery_codes_left": len(codes) - 1}


@pytest.mark.parametrize("code", ["000000", "", "aaaa-bbbb-cccc-dddd"])
def test_a_wrong_code_changes_nothing(client: Client, admin: Admin, link: str, code: str) -> None:
    response = choose(client, link, code)
    assert response.status_code == 400
    assert "That code is not right" in response.content.decode()
    admin.refresh_from_db()
    assert check_password(PASSWORD, admin.password)
    assert AdminSetup.objects.filter(admin=admin).exists()  # the link still works


@pytest.mark.parametrize(
    ("password", "again", "message"),
    [(NEW, "something else entirely", "do not match"), ("short", "short", "too short")],
)
def test_a_bad_password_spends_no_code(
    client: Client, admin: Admin, link: str, password: str, again: str, message: str
) -> None:
    codes = recovery.issue(admin)
    response = choose(client, link, codes[0], password=password, again=again)
    assert response.status_code == 400
    assert message in response.content.decode()
    assert recovery.remaining(admin) == len(codes)


def test_five_wrong_codes_spend_the_link(client: Client, admin: Admin, link: str) -> None:
    # Security review, 6 November 2026: the holder of a link must not be able to
    # keep guessing, so the link goes after five wrong codes.
    for _ in range(limits.RESET_CODE_FAILURES_PER_LINK - 1):
        assert choose(client, link, "000000").status_code == 400
    response = choose(client, link, "000000")
    assert response.status_code == 400
    assert "Too many wrong codes. Ask for a new link." in response.content.decode()
    assert not AdminSetup.objects.exists()
    assert choose(client, link, code_for(admin)).status_code == 404
    admin.refresh_from_db()
    assert check_password(PASSWORD, admin.password)


def test_the_wrong_code_count_lives_on_the_link(client: Client, admin: Admin, link: str) -> None:
    # Security review, 6 November 2026: a count in a clock-aligned window reset
    # partway through a link's hour. Kept on the link itself, it never resets.
    assert choose(client, link, "000000").status_code == 400
    assert AdminSetup.objects.get(admin=admin).failures == 1
    AdminSetup.objects.update(failures=limits.RESET_CODE_FAILURES_PER_LINK - 1)
    response = choose(client, link, "000000")
    assert "Too many wrong codes. Ask for a new link." in response.content.decode()
    assert not AdminSetup.objects.exists()


def test_wrong_codes_on_a_link_never_stop_the_admin_signing_in(
    client: Client, admin: Admin, link: str
) -> None:
    # Security review, 6 November 2026: the reset page has its own limit, so
    # someone holding a link cannot use up the admin's sign-in code attempts.
    for _ in range(limits.RESET_CODE_FAILURES_PER_LINK):
        choose(client, link, "000000")
    owner = Client(REMOTE_ADDR="203.0.113.9")
    post(owner, "/admin/login", {"email": EMAIL, "password": PASSWORD})
    response = post(owner, "/admin/login/code", {"code": code_for(admin)})
    assert response.status_code == 302 and response["Location"] == "/admin/"


@pytest.mark.usefixtures("email_on", "sent_now")
def test_reset_codes_are_limited_per_admin_across_links(admin: Admin) -> None:
    attempts = 0
    statuses: list[int] = []
    for n in range(3):  # three links, each spent by wrong codes
        asker = Client(REMOTE_ADDR=f"203.0.113.{n + 10}")
        ask(asker)
        path = link_in(django_mail.outbox[-1])
        for _ in range(limits.RESET_CODE_FAILURES_PER_LINK):
            attempts += 1
            statuses.append(choose(asker, path, "000000").status_code)
    allowed = limits.RESET_CODE_PER_ADMIN.limit
    assert statuses[:allowed] == [400] * allowed
    assert set(statuses[allowed:]) == {429}


@pytest.mark.usefixtures("email_on")
def test_the_reply_does_the_same_work_for_any_address(
    client: Client, admin: Admin, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Security review, 6 November 2026: the link is created in the background
    # too, so the reply writes nothing more for a real admin than for anyone.
    waiting: list[Callable[[], object]] = []
    monkeypatch.setattr(mail, "in_background", waiting.append)
    ask(client)
    assert not AdminSetup.objects.exists()
    assert django_mail.outbox == []
    [task] = waiting
    task()
    assert AdminSetup.objects.filter(admin=admin).exists()
    assert len(django_mail.outbox) == 1


def test_the_link_lasts_one_hour(client: Client, admin: Admin, link: str) -> None:
    AdminSetup.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    assert client.get(link, secure=True).status_code == 404
    assert choose(client, link, code_for(admin)).status_code == 404


def test_signing_in_with_the_new_password(client: Client, admin: Admin, link: str) -> None:
    choose(client, link, code_for(admin))
    response = post(client, "/admin/login", {"email": EMAIL, "password": NEW})
    assert response.status_code == 302 and response["Location"] == "/admin/login/code"


def test_a_reset_stops_a_sign_in_already_past_the_password(
    client: Client, admin: Admin, link: str
) -> None:
    other = Client(REMOTE_ADDR="203.0.113.9")
    assert post(other, "/admin/login", {"email": EMAIL, "password": PASSWORD}).status_code == 302
    choose(client, link, code_for(admin))
    # A code the account would otherwise accept: the reset spent this step.
    Admin.objects.filter(pk=admin.pk).update(totp_last_step=0)
    response = post(other, "/admin/login/code", {"code": code_for(admin)})
    assert response["Location"] == "/admin/login"
    assert other.get("/admin/", secure=True).status_code == 302


# ---------------------------------------------------------------- on the server


def test_snug_reset_password(admin: Admin, monkeypatch: pytest.MonkeyPatch, email_on: None) -> None:
    typed = iter([NEW, NEW])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(typed))
    out = StringIO()
    call_command("reset-password", EMAIL, stdout=out)
    admin.refresh_from_db()
    assert check_password(NEW, admin.password)
    assert f"New password set for {EMAIL}" in out.getvalue()
    event = AuditEvent.objects.get(action="admin.password_reset")
    assert event.actor_type == AuditEvent.ActorType.CLI and event.details == {"method": "cli"}
    [notice] = django_mail.outbox
    assert notice.subject == "Your Snug password was changed"


def test_snug_reset_password_refuses_a_mismatch(
    admin: Admin, monkeypatch: pytest.MonkeyPatch
) -> None:
    typed = iter([NEW, "not the same at all"])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(typed))
    with pytest.raises(CommandError, match="do not match"):
        call_command("reset-password", EMAIL)
    admin.refresh_from_db()
    assert check_password(PASSWORD, admin.password)
    assert not AuditEvent.objects.filter(action="admin.password_reset").exists()


def test_snug_reset_password_needs_a_real_admin() -> None:
    with pytest.raises(CommandError, match="no admin"):
        call_command("reset-password", "nobody@fernlea.example")


def test_django_changepassword_points_to_the_audited_command() -> None:
    with pytest.raises(CommandError, match="snug reset-password"):
        call_command("changepassword", EMAIL)


def test_background_tasks_run_on_their_own_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    done = threading.Event()
    seen: list[str] = []
    closed: list[bool] = []
    monkeypatch.setattr("django.db.connections.close_all", lambda: closed.append(True))

    def task() -> None:
        seen.append(threading.current_thread().name)
        done.set()

    mail.in_background(task)
    assert done.wait(timeout=5)
    for _ in range(50):  # close_all runs just after the task returns
        if closed:
            break
        threading.Event().wait(0.01)
    assert seen and seen[0] != threading.current_thread().name
    assert closed == [True]
