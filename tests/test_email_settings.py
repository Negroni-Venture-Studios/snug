"""Email settings in Settings: one place for how Snug sends email.

What an admin saves there wins. Without it, Snug uses the SMTP settings in
snug.env, as before; on localhost without either, the development outbox.
Passwords can be replaced but are never shown again.
"""

import dataclasses
import time
from html import escape
from typing import Any

import pytest
from django.conf import settings
from django.core import mail as django_mail
from django.core.mail import EmailMessage
from django.test import Client

from snug.accounts import sessions
from snug.accounts.models import Admin
from snug.audit.models import AuditEvent
from snug.core import doctor, mail
from snug.core.email_backend import EmailBackend
from snug.core.models import EmailSettings

pytestmark = pytest.mark.django_db


def config(**changes: Any) -> None:
    settings.CONFIG = dataclasses.replace(settings.CONFIG, **changes)


@pytest.fixture(autouse=True)
def restore_config() -> Any:
    saved = settings.CONFIG
    yield
    settings.CONFIG = saved


def saved_smtp(**changes: Any) -> EmailSettings:
    fields: dict[str, Any] = {
        "provider": "smtp",
        "from_address": "rooms@fernlea.example",
        "smtp_host": "smtp.postmark.example",
        "smtp_port": 2525,
        "smtp_username": "token-user",
        "smtp_password": "s3cret-pa$$word",
        "smtp_security": "starttls",
    }
    fields.update(changes)
    row = EmailSettings.load()
    for name, value in fields.items():
        setattr(row, name, value)
    row.save()
    return row


# ---------------------------------------------------------------- which settings are in force


def test_saved_settings_win_over_snug_env() -> None:
    config(domain="room.fernlea.example", smtp_host="smtp.env.example", email_from="env@x.example")
    saved_smtp()
    now = mail.outgoing()
    assert now.kind == "smtp"
    assert now.source == "settings"
    assert now.host == "smtp.postmark.example"
    assert now.from_address == "rooms@fernlea.example"


def test_without_saved_settings_snug_env_is_used() -> None:
    config(
        domain="room.fernlea.example",
        smtp_host="smtp.env.example",
        smtp_port=2525,
        email_from="env@fernlea.example",
    )
    now = mail.outgoing()
    assert (now.kind, now.source, now.host, now.port) == (
        "smtp",
        "snug.env",
        "smtp.env.example",
        2525,
    )
    assert now.from_address == "env@fernlea.example"


def test_on_localhost_without_either_it_is_the_outbox() -> None:
    config(domain="localhost", smtp_host="")
    assert mail.outgoing().kind == "outbox"
    assert mail.enabled()


def test_otherwise_email_is_off() -> None:
    config(domain="room.fernlea.example", smtp_host="")
    assert mail.outgoing().kind == "off"
    assert not mail.enabled()


def test_saved_settings_turn_email_on() -> None:
    config(domain="room.fernlea.example", smtp_host="")
    saved_smtp()
    assert mail.enabled()


def test_messages_come_from_the_address_in_force() -> None:
    config(domain="room.fernlea.example", smtp_host="")
    saved_smtp(from_address="deals@fernlea.example")
    assert mail.send(to="priya@northfield.example", subject="Hello", text="Body")
    (message,) = django_mail.outbox
    assert message.from_email == "deals@fernlea.example"


# ---------------------------------------------------------------- the transport


def test_the_transport_sends_through_the_saved_smtp_server(monkeypatch: pytest.MonkeyPatch) -> None:
    config(domain="room.fernlea.example", smtp_host="")
    saved_smtp(smtp_security="tls", smtp_port=465)
    made: dict[str, Any] = {}

    class FakeSmtp:
        def __init__(self, **options: Any) -> None:
            made.update(options)

        def send_messages(self, messages: list[EmailMessage]) -> int:
            made["sent"] = len(messages)
            return len(messages)

    monkeypatch.setattr("snug.core.email_backend.SmtpBackend", FakeSmtp)
    message = EmailMessage("Hi", "Body", "rooms@fernlea.example", ["priya@northfield.example"])
    assert EmailBackend().send_messages([message]) == 1
    assert made["host"] == "smtp.postmark.example"
    assert made["port"] == 465
    assert made["username"] == "token-user"
    assert made["password"] == "s3cret-pa$$word"
    assert made["use_ssl"] is True and made["use_tls"] is False
    assert made["timeout"] == 15
    assert made["sent"] == 1


def test_the_transport_writes_to_the_outbox_on_localhost(
    data_folder: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    config(domain="localhost", smtp_host="", data_dir=data_folder)
    message = EmailMessage("Hi", "Body", "rooms@x.example", ["priya@northfield.example"])
    assert EmailBackend().send_messages([message]) == 1
    assert list((data_folder / "outbox").iterdir())


def test_the_transport_sends_nothing_when_email_is_off() -> None:
    config(domain="room.fernlea.example", smtp_host="")
    message = EmailMessage("Hi", "Body", "rooms@x.example", ["priya@northfield.example"])
    assert EmailBackend().send_messages([message]) == 0


# ---------------------------------------------------------------- the panel


@pytest.fixture
def client() -> Client:
    config(domain="room.fernlea.example", smtp_host="")
    admin = Admin.objects.create(email="ruth@fernlea.example", is_active=True)
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


def save(client: Client, **fields: str) -> Any:
    form = {
        "provider": "smtp",
        "from_address": "rooms@fernlea.example",
        "smtp_host": "smtp.postmark.example",
        "smtp_port": "2525",
        "smtp_username": "token-user",
        "smtp_password": "s3cret-pa$$word",
        "smtp_security": "starttls",
    }
    form.update(fields)
    return client.post("/admin/settings/email", form, secure=True)


def test_the_panel_says_email_is_off(client: Client) -> None:
    text = panel(client)
    assert 'action="/admin/settings/email#email"' in text
    assert "Email is off" in text


def test_saving_smtp_settings(client: Client) -> None:
    response = save(client)
    assert response["Location"] == "/admin/settings/?saved=email#email"
    row = EmailSettings.load()
    assert (row.provider, row.smtp_host, row.smtp_port) == ("smtp", "smtp.postmark.example", 2525)
    assert row.smtp_password == "s3cret-pa$$word"
    event = AuditEvent.objects.get(action="settings.email_changed")
    assert event.details == {
        "provider": "smtp",
        "host": "smtp.postmark.example",
        "from": "rooms@fernlea.example",
    }
    assert "s3cret" not in str(event.details)
    text = panel(client, "?saved=email")
    assert "Saved. Snug sends email through smtp.postmark.example as rooms@fernlea.example." in text
    assert "s3cret" not in text
    assert 'name="smtp_password" type="password" autocomplete="new-password" value=""' in text
    assert "A password is saved. Leave this empty to keep it." in text


def test_an_empty_password_keeps_the_saved_one(client: Client) -> None:
    save(client)
    save(client, smtp_password="", smtp_host="smtp.mailgun.example")
    row = EmailSettings.load()
    assert row.smtp_host == "smtp.mailgun.example"
    assert row.smtp_password == "s3cret-pa$$word"


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        (
            "from_address",
            "not-an-address",
            "Enter the address emails come from, like rooms@example.com.",
        ),
        ("smtp_host", "", "Enter your provider's SMTP server, like smtp.postmarkapp.com."),
        ("smtp_port", "70000", "Enter a port number from 1 to 65535."),
        (
            "smtp_security",
            "none",
            "Choose STARTTLS or TLS. Email must never cross the internet unencrypted.",
        ),
    ],
)
def test_mistakes_are_shown_beside_the_field(
    client: Client, field: str, value: str, error: str
) -> None:
    response = save(client, **{field: value})
    assert response.status_code == 400
    page = response.content.decode()
    assert f'id="{field.replace("_", "-")}-error"' in page
    assert escape(error) in page
    assert EmailSettings.load().provider == ""


def test_snug_env_settings_are_named_until_replaced(client: Client) -> None:
    config(smtp_host="smtp.env.example", email_from="env@fernlea.example")
    text = panel(client)
    assert "Using the SMTP settings in snug.env: smtp.env.example, as env@fernlea.example." in text


# ---------------------------------------------------------------- a test email


def test_sending_a_test_to_yourself(client: Client) -> None:
    save(client)
    response = client.post("/admin/settings/email/test", secure=True)
    assert response["Location"] == "/admin/settings/?saved=email-test#email"
    (message,) = django_mail.outbox
    assert message.to == ["ruth@fernlea.example"]
    assert message.subject == "Snug: a test email"
    assert "Sent a test email to ruth@fernlea.example. Check that it arrived" in panel(
        client, "?saved=email-test"
    )


def test_a_test_that_fails_says_so(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    save(client)
    monkeypatch.setattr(mail, "send", lambda **kwargs: False)
    response = client.post("/admin/settings/email/test", secure=True)
    assert response.status_code == 502
    assert "The test email could not be sent." in response.content.decode()


# ---------------------------------------------------------------- snug doctor


def test_doctor_names_what_is_in_force() -> None:
    config(domain="room.fernlea.example", smtp_host="")
    saved_smtp()
    found = doctor.email_setup()
    assert found.status == "ok"
    assert found.message == "Sending through smtp.postmark.example as rooms@fernlea.example."


def test_the_real_smtp_transport_is_built_without_old_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Django 6.1 only reads its old EMAIL_* settings when no alias is given,
    and they do not exist once MAILERS is used: a real send failed with a 500."""
    from django.core.mail.backends.smtp import EmailBackend as RealSmtp

    config(domain="room.fernlea.example", smtp_host="")
    saved_smtp()
    built: list[RealSmtp] = []

    def no_connection(self: RealSmtp, messages: list[EmailMessage]) -> int:
        built.append(self)
        return len(messages)

    monkeypatch.setattr(RealSmtp, "send_messages", no_connection)
    message = EmailMessage("Hi", "Body", "rooms@fernlea.example", ["priya@northfield.example"])
    assert EmailBackend(alias="default").send_messages([message]) == 1
    assert built[0].host == "smtp.postmark.example"
    assert built[0].use_tls is True
