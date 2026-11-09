"""Sending through Google Workspace, over the Gmail API on port 443.

DigitalOcean blocks the ports Google's mail servers accept, so Snug sends
through the Gmail API instead. The founder makes an internal OAuth client in
their own Google Cloud project, pastes its ID and secret into Settings, and
connects the sending mailbox. Snug keeps only a refresh token, for the
gmail.send permission alone. Saving the client and finishing the connection
each need a fresh authenticator code, and every admin is told. Nothing about
Google is ever logged.
"""

import base64
import dataclasses
import hashlib
import json
import logging
import time
from email import message_from_bytes
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pyotp
import pytest
from django.conf import settings
from django.core import mail as django_mail
from django.core.mail import EmailMessage
from django.test import Client
from django.utils import timezone

from snug.accounts import sessions, totp
from snug.accounts.models import Admin
from snug.audit.models import AuditEvent
from snug.core import gmail, mail
from snug.core.email_backend import EmailBackend
from snug.core.models import EmailSettings
from snug.core.redaction import redact

pytestmark = pytest.mark.django_db

CLIENT_ID = "123456789-abcdef.apps.googleusercontent.com"
CALLBACK = "https://room.fernlea.example/admin/settings/email/google/callback"


@pytest.fixture(autouse=True)
def on_a_server() -> Any:
    saved = settings.CONFIG
    settings.CONFIG = dataclasses.replace(saved, domain="room.fernlea.example", smtp_host="")
    gmail.forget_tokens()
    yield
    settings.CONFIG = saved
    gmail.forget_tokens()


class Google:
    """Google's token, revoke and Gmail endpoints, as Snug calls them."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.account = "rooms@fernlea.example"
        self.fail_send = False
        self.fail_refresh = ""
        self.refresh_tokens = True

    def request(self, url: str, *, form: Any = None, body: Any = None, token: str = "") -> Any:
        self.calls.append({"url": url, "form": form, "body": body, "token": token})
        if url == gmail.TOKEN_URL and form["grant_type"] == "authorization_code":
            claims = {"email": self.account, "email_verified": True}
            payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
            answer = {
                "access_token": "access-first",
                "expires_in": 3599,
                "id_token": f"header.{payload}.signature",
            }
            if self.refresh_tokens:
                answer["refresh_token"] = "refresh-xyz"
            return answer
        if url == gmail.TOKEN_URL:
            if self.fail_refresh:
                raise gmail.GoogleError(self.fail_refresh)
            return {"access_token": f"access-{len(self.calls)}", "expires_in": 3599}
        if url == gmail.REVOKE_URL:
            return {}
        if url == gmail.SEND_URL:
            if self.fail_send:
                raise gmail.GoogleError("HTTP 403")
            return {"id": "msg-1"}
        raise AssertionError(url)


@pytest.fixture
def google(monkeypatch: pytest.MonkeyPatch) -> Google:
    fake = Google()
    monkeypatch.setattr(gmail, "_request", fake.request)
    return fake


@pytest.fixture
def admin() -> Admin:
    return Admin.objects.create(
        email="ruth@fernlea.example",
        is_active=True,
        totp_secret=totp.new_secret(),
        totp_confirmed_at=timezone.now(),
    )


def fresh_code(account: Admin) -> str:
    Admin.objects.filter(pk=account.pk).update(totp_last_step=0)
    return pyotp.TOTP(account.totp_secret).now()


@pytest.fixture
def client(admin: Admin) -> Client:
    browser = Client()
    browser.force_login(admin)
    session = browser.session
    session[sessions.LOGIN_AT] = session[sessions.LAST_ACTIVITY] = time.time()
    session.save()
    return browser


def panel(client: Client, query: str = "") -> str:
    page = client.get(f"/admin/settings/{query}", secure=True).content.decode()
    start = page.index('id="email"')
    return page[start : page.index("</section>", start)]


def save_client(client: Client, admin: Admin, **fields: str) -> Any:
    form = {
        "provider": "google",
        "google_client_id": CLIENT_ID,
        "google_client_secret": "GOCSPX-s",
        "code": fresh_code(admin),
    }
    form.update(fields)
    return client.post("/admin/settings/email", form, secure=True)


def to_google(client: Client) -> dict[str, str]:
    response = client.get("/admin/settings/email/google/connect", secure=True)
    return {k: v[0] for k, v in parse_qs(urlsplit(response["Location"]).query).items()}


def back_from_google(client: Client, state: str, **extra: str) -> Any:
    query = {"state": state, "code": "4/abc", **extra}
    return client.get("/admin/settings/email/google/callback", query, secure=True)


def finish(client: Client, admin: Admin, code: str | None = None) -> Any:
    form = {"code": fresh_code(admin) if code is None else code}
    return client.post("/admin/settings/email/google/confirm", form, secure=True)


def connected(client: Client, admin: Admin) -> None:
    save_client(client, admin)
    back_from_google(client, to_google(client)["state"])
    finish(client, admin)


# ---------------------------------------------------------------- the panel


def test_the_panel_offers_both_ways_to_send(client: Client) -> None:
    text = panel(client)
    assert 'type="radio" name="provider" value="google"' in text
    assert 'type="radio" name="provider" value="smtp"' in text
    assert 'name="google_client_id"' in text
    assert f"<code>{CALLBACK}</code>" in text


def test_saving_the_client(client: Client, admin: Admin) -> None:
    response = save_client(client, admin)
    assert response["Location"] == "/admin/settings/?saved=email#email"
    row = EmailSettings.load()
    assert (row.provider, row.google_client_id) == ("google", CLIENT_ID)
    assert row.google_client_secret == "GOCSPX-s"
    event = AuditEvent.objects.get(action="settings.email_changed")
    assert event.details == {"provider": "google", "client": CLIENT_ID}
    text = panel(client, "?saved=email")
    assert "GOCSPX" not in text
    assert "Saved. Now connect the mailbox Snug sends from." in text
    assert 'href="/admin/settings/email/google/connect"' in text
    assert not mail.enabled()  # not until a mailbox is connected


def test_saving_the_client_needs_a_fresh_code(client: Client, admin: Admin) -> None:
    response = save_client(client, admin, code="000000")
    assert response.status_code == 400
    assert 'id="email-code-error"' in response.content.decode()
    assert EmailSettings.load().provider == ""


@pytest.mark.parametrize(
    ("fields", "field", "error"),
    [
        (
            {"google_client_id": "not-a-client"},
            "google-client-id",
            "Paste the client ID, which ends .apps.googleusercontent.com.",
        ),
        ({"google_client_secret": ""}, "google-client-secret", "Paste the client secret."),
    ],
)
def test_client_mistakes_are_shown_beside_the_field(
    client: Client, admin: Admin, fields: dict[str, str], field: str, error: str
) -> None:
    response = save_client(client, admin, **fields)
    assert response.status_code == 400
    page = response.content.decode()
    assert f'id="{field}-error"' in page and error in page


def test_an_empty_secret_keeps_the_saved_one(client: Client, admin: Admin) -> None:
    save_client(client, admin)
    save_client(client, admin, google_client_secret="")
    assert EmailSettings.load().google_client_secret == "GOCSPX-s"


# ---------------------------------------------------------------- connecting


def test_connect_sends_the_admin_to_google(client: Client, admin: Admin) -> None:
    save_client(client, admin)
    response = client.get("/admin/settings/email/google/connect", secure=True)
    assert response.status_code == 302
    target = urlsplit(response["Location"])
    assert f"{target.scheme}://{target.netloc}{target.path}" == gmail.AUTH_URL
    query = {k: v[0] for k, v in parse_qs(target.query).items()}
    assert query["client_id"] == CLIENT_ID
    assert query["redirect_uri"] == CALLBACK
    assert query["response_type"] == "code"
    assert query["scope"] == "openid email https://www.googleapis.com/auth/gmail.send"
    assert query["access_type"] == "offline" and query["prompt"] == "consent"
    assert query["code_challenge_method"] == "S256"
    assert len(query["state"]) >= 40


def test_connect_without_a_client_says_so(client: Client) -> None:
    response = client.get("/admin/settings/email/google/connect", secure=True)
    assert response["Location"] == "/admin/settings/?saved=google-no-client#email"
    assert "Save the client ID and secret first." in panel(client, "?saved=google-no-client")


def test_coming_back_from_google_asks_for_a_code(
    client: Client, admin: Admin, google: Google
) -> None:
    save_client(client, admin)
    query = to_google(client)
    response = back_from_google(client, query["state"])
    assert response["Location"] == "/admin/settings/?saved=google-pending#email"
    (exchange,) = google.calls
    assert exchange["form"]["code"] == "4/abc"
    assert exchange["form"]["redirect_uri"] == CALLBACK
    assert exchange["form"]["client_secret"] == "GOCSPX-s"
    verifier = exchange["form"]["code_verifier"]
    digest = hashlib.sha256(verifier.encode()).digest()
    assert base64.urlsafe_b64encode(digest).decode().rstrip("=") == query["code_challenge"]
    # Nothing is saved until a fresh code confirms it.
    assert EmailSettings.load().google_refresh_token == ""
    text = panel(client, "?saved=google-pending")
    assert "Enter a code from your authenticator app to finish connecting" in text
    assert "rooms@fernlea.example" in text
    assert 'action="/admin/settings/email/google/confirm#email"' in text


def test_finishing_with_a_code_connects_the_mailbox(
    client: Client, admin: Admin, google: Google
) -> None:
    Admin.objects.create(email="omar@fernlea.example", is_active=True)
    save_client(client, admin)
    back_from_google(client, to_google(client)["state"])
    django_mail.outbox.clear()
    response = finish(client, admin)
    assert response["Location"] == "/admin/settings/?saved=email-google#email"
    row = EmailSettings.load()
    assert row.google_refresh_token == "refresh-xyz"
    assert row.google_account == "rooms@fernlea.example"
    now = mail.outgoing()
    assert (now.kind, now.from_address) == ("google", "rooms@fernlea.example")
    event = AuditEvent.objects.get(action="settings.email_google_connected")
    assert event.details == {"account": "rooms@fernlea.example"}
    assert "Connected. Snug sends email as rooms@fernlea.example, through Google Workspace." in (
        panel(client, "?saved=email-google")
    )
    alerts = [m for m in django_mail.outbox if "Email settings changed" in m.subject]
    assert sorted(m.to[0] for m in alerts) == ["omar@fernlea.example", "ruth@fernlea.example"]


def test_a_wrong_code_does_not_connect(client: Client, admin: Admin, google: Google) -> None:
    save_client(client, admin)
    back_from_google(client, to_google(client)["state"])
    response = finish(client, admin, code="000000")
    assert response.status_code == 400
    assert EmailSettings.load().google_refresh_token == ""


def test_finishing_without_coming_from_google_does_nothing(
    client: Client, admin: Admin, google: Google
) -> None:
    save_client(client, admin)
    response = finish(client, admin)
    assert response["Location"] == "/admin/settings/?saved=google-expired#email"
    assert EmailSettings.load().google_refresh_token == ""


def test_a_wrong_state_is_refused(client: Client, admin: Admin, google: Google) -> None:
    save_client(client, admin)
    to_google(client)
    response = back_from_google(client, "forged")
    assert response.status_code == 400
    assert not google.calls


def test_a_state_works_once(client: Client, admin: Admin, google: Google) -> None:
    save_client(client, admin)
    state = to_google(client)["state"]
    back_from_google(client, state)
    assert back_from_google(client, state).status_code == 400
    assert len(google.calls) == 1


def test_declining_at_google_is_said_plainly(client: Client, admin: Admin, google: Google) -> None:
    save_client(client, admin)
    state = to_google(client)["state"]
    response = back_from_google(client, state, error="access_denied")
    assert response.status_code == 400
    assert "Google did not give Snug permission (access_denied)." in response.content.decode()
    assert not google.calls


def test_google_must_give_a_lasting_permission(
    client: Client, admin: Admin, google: Google
) -> None:
    google.refresh_tokens = False
    save_client(client, admin)
    response = back_from_google(client, to_google(client)["state"])
    assert response.status_code == 400
    assert "Google did not give Snug a lasting permission" in response.content.decode()


def test_the_callback_needs_a_signed_in_admin(google: Google) -> None:
    response = Client().get(
        "/admin/settings/email/google/callback", {"state": "x", "code": "y"}, secure=True
    )
    assert response.status_code == 302
    assert response["Location"].startswith("/admin/login")
    assert not google.calls


# ---------------------------------------------------------------- sending


def raw_of(call: dict[str, Any]) -> Any:
    raw = call["body"]["raw"]
    return message_from_bytes(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))


def test_mail_goes_through_the_gmail_api(client: Client, admin: Admin, google: Google) -> None:
    connected(client, admin)
    google.calls.clear()
    message = EmailMessage("Your code", "123456", "rooms@fernlea.example", ["priya@north.example"])
    message.snug_outgoing = mail.outgoing()  # type: ignore[attr-defined]
    assert EmailBackend(alias="default").send_messages([message]) == 1
    refresh, send = google.calls
    assert refresh["url"] == gmail.TOKEN_URL
    assert refresh["form"]["grant_type"] == "refresh_token"
    assert refresh["form"]["refresh_token"] == "refresh-xyz"
    assert send["url"] == gmail.SEND_URL
    assert send["token"].startswith("access-")
    sent = raw_of(send)
    assert sent["To"] == "priya@north.example"
    assert sent["Subject"] == "Your code"
    assert sent["From"] == "rooms@fernlea.example"


def test_the_access_token_is_reused_until_it_expires(
    client: Client, admin: Admin, google: Google
) -> None:
    connected(client, admin)
    google.calls.clear()
    now = mail.outgoing()
    for _ in range(3):
        gmail.send(EmailMessage("Hi", "Body", now.from_address, ["p@north.example"]), now)
    assert [c["url"] for c in google.calls] == [gmail.TOKEN_URL] + [gmail.SEND_URL] * 3


class _ThroughTransport(EmailMessage):
    """mail.send's message, sent through Snug's own transport, not the test mailer."""

    def __init__(self, **fields: Any) -> None:
        super().__init__(
            subject=fields["subject"],
            body=fields["body"],
            from_email=fields["from_email"],
            to=fields["to"],
            reply_to=fields.get("reply_to"),
        )

    def attach_alternative(self, content: str, mimetype: str) -> None:
        pass

    def send(self, fail_silently: bool = False, *, using: str | None = None) -> int:
        return EmailBackend(alias="default").send_messages([self])


@pytest.mark.usefixtures("google")
def test_a_refused_send_is_a_failed_email(
    client: Client, admin: Admin, google: Google, monkeypatch: pytest.MonkeyPatch
) -> None:
    connected(client, admin)
    google.fail_send = True
    monkeypatch.setattr(mail, "EmailMultiAlternatives", _ThroughTransport)
    assert not mail.send(to="priya@north.example", subject="Hi", text="Body")


def test_a_revoked_connection_is_a_failed_email(
    client: Client, admin: Admin, google: Google, monkeypatch: pytest.MonkeyPatch, caplog: Any
) -> None:
    connected(client, admin)
    google.fail_refresh = "invalid_grant"
    monkeypatch.setattr(mail, "EmailMultiAlternatives", _ThroughTransport)
    with caplog.at_level(logging.WARNING):
        assert not mail.send(to="priya@north.example", subject="Hi", text="Body")
    assert "GoogleError" in caplog.text
    assert "refresh-xyz" not in caplog.text and "GOCSPX" not in caplog.text


# ---------------------------------------------------------------- disconnecting


def disconnect(client: Client, admin: Admin, code: str | None = None) -> Any:
    form = {"code": fresh_code(admin) if code is None else code}
    return client.post("/admin/settings/email/google/disconnect", form, secure=True)


def test_disconnecting(client: Client, admin: Admin, google: Google) -> None:
    Admin.objects.create(email="omar@fernlea.example", is_active=True)
    connected(client, admin)
    google.calls.clear()
    django_mail.outbox.clear()
    response = disconnect(client, admin)
    assert response["Location"] == "/admin/settings/?saved=email-google-off#email"
    revoke = next(c for c in google.calls if c["url"] == gmail.REVOKE_URL)
    assert revoke["form"] == {"token": "refresh-xyz"}
    row = EmailSettings.load()
    assert row.google_refresh_token == "" and row.google_account == ""
    assert mail.outgoing().kind == "off"
    assert AuditEvent.objects.filter(action="settings.email_google_disconnected").exists()
    # Told through Google, which was in force until a moment ago.
    alerts = [m for m in django_mail.outbox if "Email settings changed" in m.subject]
    assert sorted(m.to[0] for m in alerts) == ["omar@fernlea.example", "ruth@fernlea.example"]
    assert all(getattr(m, "snug_outgoing").kind == "google" for m in alerts)  # noqa: B009
    assert "Email is now off." in alerts[0].body


def test_disconnecting_needs_a_fresh_code(client: Client, admin: Admin, google: Google) -> None:
    connected(client, admin)
    response = disconnect(client, admin, code="000000")
    assert response.status_code == 400
    assert 'id="google-disconnect-code-error"' in response.content.decode()
    assert EmailSettings.load().google_refresh_token == "refresh-xyz"


def test_saving_a_google_client_over_smtp_tells_every_admin(
    client: Client, admin: Admin, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The client alone sends nothing, so saved SMTP settings give way to nothing."""
    monkeypatch.setattr(mail, "_addresses", lambda host, port: ["93.184.216.34"])
    smtp = {
        "provider": "smtp",
        "from_address": "rooms@fernlea.example",
        "smtp_host": "smtp.postmark.example",
        "smtp_port": "2525",
        "smtp_username": "u",
        "smtp_password": "p",
        "smtp_security": "starttls",
        "code": fresh_code(admin),
    }
    client.post("/admin/settings/email", smtp, secure=True)
    django_mail.outbox.clear()
    save_client(client, admin)
    alerts = [m for m in django_mail.outbox if "Email settings changed" in m.subject]
    assert [m.to[0] for m in alerts] == ["ruth@fernlea.example"]
    assert getattr(alerts[0], "snug_outgoing").host == "smtp.postmark.example"  # noqa: B009
    assert "Email is now off." in alerts[0].body


def test_nothing_changes_while_snug_env_stays_in_force(client: Client, admin: Admin) -> None:
    settings.CONFIG = dataclasses.replace(
        settings.CONFIG, smtp_host="smtp.env.example", email_from="env@fernlea.example"
    )
    save_client(client, admin)
    assert not [m for m in django_mail.outbox if "Email settings changed" in m.subject]
    assert mail.outgoing().host == "smtp.env.example"


def test_saving_smtp_lets_go_of_google(
    client: Client, admin: Admin, google: Google, monkeypatch: pytest.MonkeyPatch
) -> None:
    connected(client, admin)
    google.calls.clear()
    monkeypatch.setattr(mail, "_addresses", lambda host, port: ["93.184.216.34"])
    form = {
        "provider": "smtp",
        "from_address": "rooms@fernlea.example",
        "smtp_host": "smtp.postmark.example",
        "smtp_port": "2525",
        "smtp_username": "u",
        "smtp_password": "p",
        "smtp_security": "starttls",
        "code": fresh_code(admin),
    }
    client.post("/admin/settings/email", form, secure=True)
    assert [c["url"] for c in google.calls if c["url"] == gmail.REVOKE_URL] == [gmail.REVOKE_URL]
    row = EmailSettings.load()
    assert row.google_refresh_token == "" and row.provider == "smtp"


# ---------------------------------------------------------------- never in a log


def test_googles_code_and_state_are_redacted_from_logs() -> None:
    line = "GET /admin/settings/email/google/callback?state=abc123&code=4/0Ab_secret&scope=x"
    assert redact(line) == (
        "GET /admin/settings/email/google/callback?state=[redacted]&code=[redacted]&scope=x"
    )
