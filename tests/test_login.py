"""Signing in: the two steps, every refusal, fixation, timeouts and the audit log."""

import time
from collections.abc import Iterator
from typing import Any

import pyotp
import pytest
from django.test import Client
from django.utils import timezone

from snug.accounts import limits, recovery, sessions, totp
from snug.accounts.models import Admin
from snug.audit.models import AuditEvent
from snug.core import ratelimit

pytestmark = pytest.mark.django_db

EMAIL = "ruth@fernlea.example"
PASSWORD = "correct horse battery staple"
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
def client() -> Iterator[Client]:
    yield Client(REMOTE_ADDR=IP)


def post(client: Client, path: str, data: dict[str, str], **extra: Any) -> Any:
    return client.post(path, data, secure=True, **extra)


def password_step(
    client: Client, email: str = EMAIL, password: str = PASSWORD, **extra: Any
) -> Any:
    return post(client, "/admin/login", {"email": email, "password": password}, **extra)


def code_for(admin: Admin) -> str:
    return pyotp.TOTP(admin.totp_secret).now()


def sign_in(client: Client, admin: Admin) -> None:
    assert password_step(client).status_code == 302
    response = post(client, "/admin/login/code", {"code": code_for(admin)})
    assert response.status_code == 302


def signed_in(client: Client) -> bool:
    return client.get("/admin/", secure=True).status_code == 200


def events(action: str) -> list[AuditEvent]:
    return list(AuditEvent.objects.filter(action=action).order_by("id"))


# ---------------------------------------------------------------- password step


def test_sign_in_page(client: Client) -> None:
    response = client.get("/admin/login", secure=True)
    assert response.status_code == 200
    assert b'name="csrfmiddlewaretoken"' in response.content
    assert b'autocomplete="current-password"' in response.content


def test_admin_pages_need_a_signed_in_admin(client: Client) -> None:
    response = client.get("/admin/", secure=True)
    assert response.status_code == 302
    assert response["Location"] == "/admin/login?next=/admin/"


def test_root_goes_to_the_admin(client: Client) -> None:
    response = client.get("/", secure=True)
    assert response.status_code == 302
    assert response["Location"] == "/admin/"


@pytest.mark.parametrize(
    ("email", "password"),
    [(EMAIL, "wrong password"), ("nobody@fernlea.example", PASSWORD), ("", ""), (EMAIL, "")],
    ids=["wrong-password", "unknown-email", "empty", "no-password"],
)
def test_wrong_details_are_refused_alike(
    client: Client, admin: Admin, email: str, password: str
) -> None:
    response = password_step(client, email, password)
    assert response.status_code == 200
    assert b"do not match an admin" in response.content
    assert sessions.PENDING not in client.session
    assert not signed_in(client)
    event = events("admin.login_failed")[-1]
    assert event.details == {"step": "password", "reason": "wrong"}
    assert event.ip_address == IP


def test_an_admin_without_a_confirmed_authenticator_cannot_sign_in(
    client: Client, admin: Admin
) -> None:
    admin.totp_confirmed_at = None
    admin.save()
    assert b"do not match an admin" in password_step(client).content


def test_an_inactive_admin_cannot_sign_in(client: Client, admin: Admin) -> None:
    admin.is_active = False
    admin.save()
    assert b"do not match an admin" in password_step(client).content


def test_email_is_case_insensitive(client: Client, admin: Admin) -> None:
    assert password_step(client, email="RUTH@Fernlea.example").status_code == 302


def test_the_password_alone_does_not_sign_in(client: Client, admin: Admin) -> None:
    response = password_step(client)
    assert response.status_code == 302
    assert response["Location"] == "/admin/login/code"
    assert not signed_in(client)


def test_each_step_starts_a_new_session(client: Client, admin: Admin) -> None:
    client.get("/admin/login", secure=True)
    client.session.save()
    planted = client.session.session_key
    password_step(client)
    after_password = client.session.session_key
    post(client, "/admin/login/code", {"code": code_for(admin)})
    after_code = client.session.session_key
    assert len({planted, after_password, after_code}) == 3


# ---------------------------------------------------------------- code step


def test_code_page_needs_the_password_first(client: Client) -> None:
    assert client.get("/admin/login/code", secure=True)["Location"] == "/admin/login"
    assert post(client, "/admin/login/code", {"code": "123456"})["Location"] == "/admin/login"


def test_a_correct_code_signs_in(client: Client, admin: Admin) -> None:
    sign_in(client, admin)
    assert signed_in(client)
    event = events("admin.login")[-1]
    assert (event.actor, event.target, event.ip_address) == (EMAIL, EMAIL, IP)
    assert event.details == {"method": "totp"}


def test_the_overview_marks_its_tab(client: Client, admin: Admin) -> None:
    sign_in(client, admin)
    page = client.get("/admin/", secure=True).content.decode()
    assert 'aria-current="page">Overview</a>' in page
    assert EMAIL in page


def test_a_code_works_only_once(client: Client, admin: Admin) -> None:
    code = code_for(admin)
    password_step(client)
    post(client, "/admin/login/code", {"code": code})
    post(client, "/admin/logout", {})
    other = Client(REMOTE_ADDR="203.0.113.9")
    password_step(other)
    response = post(other, "/admin/login/code", {"code": code})
    assert b"That code is not right." in response.content
    assert not signed_in(other)


def test_wrong_codes_then_start_again(client: Client, admin: Admin) -> None:
    password_step(client)
    for _ in range(limits.CODE_FAILURES_PER_PASSWORD - 1):
        response = post(client, "/admin/login/code", {"code": "000000"})
        assert b"That code is not right." in response.content
    response = post(client, "/admin/login/code", {"code": "000000"})
    assert response["Location"] == "/admin/login?again=1"
    assert sessions.PENDING not in client.session
    assert len(events("admin.login_failed")) == limits.CODE_FAILURES_PER_PASSWORD


def test_the_code_must_follow_within_five_minutes(
    client: Client, admin: Admin, monkeypatch: pytest.MonkeyPatch
) -> None:
    password_step(client)
    later = time.time() + sessions.PENDING_SECONDS + 1
    monkeypatch.setattr(time, "time", lambda: later)
    response = post(client, "/admin/login/code", {"code": code_for(admin)})
    assert response["Location"] == "/admin/login"
    assert not signed_in(client)


def test_a_deactivated_admin_cannot_finish_signing_in(client: Client, admin: Admin) -> None:
    password_step(client)
    admin.is_active = False
    admin.save()
    response = post(client, "/admin/login/code", {"code": code_for(admin)})
    assert response["Location"] == "/admin/login"


# ---------------------------------------------------------------- recovery codes


def test_a_recovery_code_signs_in_once(client: Client, admin: Admin) -> None:
    code = recovery.issue(admin)[0]
    password_step(client)
    response = post(client, "/admin/login/recovery", {"code": code})
    assert response.status_code == 302
    assert signed_in(client)
    assert events("admin.login")[-1].details == {"method": "recovery", "recovery_codes_left": 9}

    post(client, "/admin/logout", {})
    password_step(client)
    response = post(client, "/admin/login/recovery", {"code": code})
    assert b"That code is not right." in response.content


# ---------------------------------------------------------------- where you land


def test_next_is_followed_for_paths_on_this_site(client: Client, admin: Admin) -> None:
    post(client, "/admin/login", {"email": EMAIL, "password": PASSWORD, "next": "/admin/"})
    response = post(client, "/admin/login/code", {"code": code_for(admin)})
    assert response["Location"] == "/admin/"


@pytest.mark.parametrize(
    "target", ["https://evil.example/", "//evil.example/", "http://snug.example.com/admin/"]
)
def test_next_never_leaves_the_site(client: Client, admin: Admin, target: str) -> None:
    post(client, "/admin/login", {"email": EMAIL, "password": PASSWORD, "next": target})
    response = post(client, "/admin/login/code", {"code": code_for(admin)})
    assert response["Location"] == "/admin/"


def test_signed_in_admins_skip_the_sign_in_page(client: Client, admin: Admin) -> None:
    sign_in(client, admin)
    assert client.get("/admin/login", secure=True)["Location"] == "/admin/"


# ---------------------------------------------------------------- rate limits


def test_too_many_attempts_from_one_address(client: Client, admin: Admin) -> None:
    for _ in range(limits.PASSWORD_PER_IP.limit):
        password_step(client, email="nobody@fernlea.example", password="wrong")
    response = password_step(client)
    assert response.status_code == 429
    assert sessions.PENDING not in client.session
    assert events("admin.login_failed")[-1].details["reason"] == "rate_limited"
    assert password_step(Client(REMOTE_ADDR="203.0.113.9")).status_code == 302


def test_too_many_failures_against_one_account(admin: Admin) -> None:
    for n in range(limits.PASSWORD_PER_ACCOUNT.limit):
        password_step(Client(REMOTE_ADDR=f"203.0.113.{n + 1}"), password="wrong")
    assert limits.PASSWORD_PER_ACCOUNT.limit > limits.PASSWORD_PER_IP.limit
    response = password_step(Client(REMOTE_ADDR="192.0.2.50"))
    assert response.status_code == 429


def test_too_many_code_attempts_from_one_address(client: Client, admin: Admin) -> None:
    for _ in range(limits.CODE_PER_IP.limit):
        ratelimit.hit(limits.CODE_PER_IP, IP)
    password_step(client)
    response = post(client, "/admin/login/code", {"code": code_for(admin)})
    assert response.status_code == 429
    assert not signed_in(client)


# ---------------------------------------------------------------- signing out and timeouts


def test_sign_out_needs_a_post(client: Client, admin: Admin) -> None:
    sign_in(client, admin)
    assert client.get("/admin/logout", secure=True).status_code == 405
    assert signed_in(client)
    response = post(client, "/admin/logout", {})
    assert response["Location"] == "/admin/login"
    assert not signed_in(client)
    assert events("admin.logout")[-1].actor == EMAIL


@pytest.mark.parametrize(
    ("key", "age"),
    [
        (sessions.LAST_ACTIVITY, sessions.IDLE_SECONDS + 1),
        (sessions.LOGIN_AT, sessions.ABSOLUTE_SECONDS + 1),
    ],
    ids=["idle", "absolute"],
)
def test_sessions_expire(client: Client, admin: Admin, key: str, age: int) -> None:
    sign_in(client, admin)
    session = client.session
    session[key] = time.time() - age
    session.save()
    assert not signed_in(client)


def test_activity_keeps_a_session_alive(client: Client, admin: Admin) -> None:
    sign_in(client, admin)
    session = client.session
    session[sessions.LAST_ACTIVITY] = time.time() - sessions.IDLE_SECONDS + 120
    session.save()
    assert signed_in(client)
    assert time.time() - client.session[sessions.LAST_ACTIVITY] < 5


def test_a_new_authenticator_signs_out_everywhere(client: Client, admin: Admin) -> None:
    sign_in(client, admin)
    admin.totp_secret = totp.new_secret()
    admin.save()
    assert not signed_in(client)


def test_a_new_password_signs_out_everywhere(client: Client, admin: Admin) -> None:
    sign_in(client, admin)
    admin.set_password("another long passphrase entirely")
    admin.save()
    assert not signed_in(client)


# ---------------------------------------------------------------- CSRF


def test_forms_need_a_csrf_token(admin: Admin) -> None:
    strict = Client(enforce_csrf_checks=True, REMOTE_ADDR=IP)
    response = strict.post("/admin/login", {"email": EMAIL, "password": PASSWORD}, secure=True)
    assert response.status_code == 403
    assert b"This form has expired" in response.content
    assert "Content-Security-Policy" in response.headers
    assert sessions.PENDING not in strict.session


def test_a_browser_form_post_with_our_origin_passes_csrf(admin: Admin) -> None:
    # Browsers send Origin on form posts; the test client does not. A real
    # browser with "Referrer-Policy: no-referrer" sent "Origin: null" and every
    # sign-in failed CSRF, so post the way a browser does.
    browser = Client(enforce_csrf_checks=True, REMOTE_ADDR=IP)
    page = browser.get("/admin/login", secure=True)
    token = page.context["csrf_token"]
    response = browser.post(
        "/admin/login",
        {"email": EMAIL, "password": PASSWORD, "csrfmiddlewaretoken": str(token)},
        secure=True,
        headers={"Origin": "https://snug.example.com"},
    )
    assert response.status_code == 302


def test_a_null_origin_is_refused(admin: Admin) -> None:
    browser = Client(enforce_csrf_checks=True, REMOTE_ADDR=IP)
    token = browser.get("/admin/login", secure=True).context["csrf_token"]
    response = browser.post(
        "/admin/login",
        {"email": EMAIL, "password": PASSWORD, "csrfmiddlewaretoken": str(token)},
        secure=True,
        headers={"Origin": "null"},
    )
    assert response.status_code == 403
