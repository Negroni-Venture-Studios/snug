"""Email settings in Settings: one place for how Snug sends email.

What an admin saves there wins. Without it, Snug uses the SMTP settings in
snug.env, as before; on localhost without either, the development outbox.
Passwords can be replaced but are never shown again.
"""

import dataclasses
import re
import time
from html import escape
from typing import Any

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
from snug.core import doctor, mail
from snug.core.email_backend import EmailBackend
from snug.core.models import EmailSettings

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def public_servers(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    """Every SMTP server resolves to a public address, unless a test says otherwise."""
    answers: dict[str, list[str]] = {}
    monkeypatch.setattr(mail, "_addresses", lambda host, port: answers.get(host, ["93.184.216.34"]))
    return answers


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

    monkeypatch.setattr("snug.core.email_backend._PinnedSmtpBackend", FakeSmtp)
    message = EmailMessage("Hi", "Body", "rooms@fernlea.example", ["priya@northfield.example"])
    assert EmailBackend().send_messages([message]) == 1
    assert made["host"] == "smtp.postmark.example"
    assert made["address"] == "93.184.216.34"  # the address that was checked
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
def admin() -> Admin:
    config(domain="room.fernlea.example", smtp_host="")
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


DOTS = "\u2022" * 12


def step(text: str, name: str) -> str:
    """One numbered step of the Email section, from its id to the next step."""
    start = text.index(f'id="{name}"')
    end = text.find("<li ", start)
    return text[start : end if end != -1 else len(text)]


def save(client: Client, **fields: str) -> Any:
    form = {
        "code": fresh_code(Admin.objects.get(email="ruth@fernlea.example")),
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
    assert 'action="/admin/settings/email#email-setup"' in text
    assert "Email is off" in text


def test_the_test_step_waits_for_the_settings(client: Client) -> None:
    """The steps are all there from the start, the later ones waiting for the earlier."""
    test = step(panel(client), "email-test")
    assert "Save the SMTP settings first." in test
    assert "step-waiting" in test


def test_saving_smtp_settings(client: Client) -> None:
    response = save(client)
    # Saving lands on the next thing to do: sending a test, which says what was saved.
    assert response["Location"] == "/admin/settings/?saved=email#email-test"
    test = step(panel(client, "?saved=email"), "email-test")
    assert "Saved. Snug sends email through smtp.postmark.example as rooms@fernlea.example." in test
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
    # A saved password shows as dots, the same twelve whatever its length, and is never sent.
    box = re.search(r'<input [^>]*name="smtp_password"[^>]*>', text)
    assert box
    assert 'type="password"' in box.group(0) and 'value=""' in box.group(0)
    assert f'placeholder="{DOTS}"' in box.group(0)
    assert "Saved. Type a new one to replace it." in text


def test_an_empty_password_field_has_no_dots(client: Client) -> None:
    assert f'placeholder="{DOTS}"' not in panel(client)


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
        ("smtp_port", "70000", "Use port 25, 465, 587, 2465, 2525, or 2587."),
        ("smtp_port", "8025", "Use port 25, 465, 587, 2465, 2525, or 2587."),
        (
            "smtp_security",
            "none",
            "Choose STARTTLS or TLS.",
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
    assert response["Location"] == "/admin/settings/?saved=email-test#email-test"
    (message,) = [m for m in django_mail.outbox if m.subject == "Snug: a test email"]
    assert message.to == ["ruth@fernlea.example"]
    assert message.subject == "Snug: a test email"
    assert "Sent a test email to ruth@fernlea.example. Check that it arrived" in step(
        panel(client, "?saved=email-test"), "email-test"
    )


def test_a_test_that_fails_says_so(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    save(client)
    monkeypatch.setattr(mail, "send", lambda **kwargs: False)
    response = client.post("/admin/settings/email/test", secure=True)
    assert response.status_code == 502
    text = response.content.decode()
    assert "The test email could not be sent." in step(text, "email-test")
    assert 'action="/admin/settings/email/test#email-test"' in text


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


# ---------------------------------------------------------------- a stolen session


def test_saving_needs_a_fresh_code(client: Client) -> None:
    response = save(client, code="000000")
    assert response.status_code == 400
    page = response.content.decode()
    assert 'id="email-code-error"' in page
    assert EmailSettings.load().provider == ""
    response = save(client, code="")
    assert response.status_code == 400
    assert EmailSettings.load().provider == ""


def test_every_admin_is_told_through_the_old_settings(client: Client) -> None:
    Admin.objects.create(email="omar@fernlea.example", is_active=True)
    config(smtp_host="smtp.env.example", email_from="env@fernlea.example")
    save(client)
    alerts = [m for m in django_mail.outbox if "Email settings changed" in m.subject]
    assert sorted(m.to[0] for m in alerts) == ["omar@fernlea.example", "ruth@fernlea.example"]
    assert all(getattr(m, "snug_outgoing").host == "smtp.env.example" for m in alerts)  # noqa: B009
    assert "ruth@fernlea.example changed how Snug sends email" in alerts[0].body
    assert "smtp.postmark.example" in alerts[0].body


@pytest.mark.parametrize(
    "address",
    [
        "10.0.0.5",
        "127.0.0.1",
        "169.254.169.254",
        "fd00::1",
        "::1",
        "::ffff:10.0.0.1",  # IPv4-mapped
        "64:ff9b::a00:1",  # NAT64, which Python counts as global
        "64:ff9b:1::a00:1",  # local-use NAT64
        "2002:a00:1::",  # 6to4
        "2001:0:4136:e378:8000:63bf:f5ff:fffe",  # Teredo
    ],
)
def test_a_private_server_is_refused(
    client: Client, public_servers: dict[str, list[str]], address: str
) -> None:
    public_servers["smtp.postmark.example"] = [address]
    response = save(client)
    assert response.status_code == 400
    assert (
        "That server is on a private network. Snug sends only through public mail servers."
        in response.content.decode()
    )


def test_a_server_that_cannot_be_found_is_refused(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    def nowhere(host: str, port: int) -> list[str]:
        raise OSError("not found")

    monkeypatch.setattr(mail, "_addresses", nowhere)
    response = save(client)
    assert response.status_code == 400
    assert "That server cannot be found. Check its name." in response.content.decode()


def test_on_localhost_a_local_server_is_allowed(public_servers: dict[str, list[str]]) -> None:
    config(domain="localhost")
    public_servers["mailpit"] = ["127.0.0.1"]
    assert mail.public_server("mailpit") == ""


def test_a_server_that_turns_private_is_refused_at_send(
    public_servers: dict[str, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    from django.core.mail.backends.smtp import EmailBackend as RealSmtp

    config(domain="room.fernlea.example", smtp_host="")
    saved_smtp()
    public_servers["smtp.postmark.example"] = ["10.1.2.3"]  # its DNS changed since saving
    monkeypatch.setattr(RealSmtp, "send_messages", lambda self, messages: len(messages))
    message = EmailMessage("Hi", "Body", "rooms@fernlea.example", ["priya@northfield.example"])
    with pytest.raises(OSError, match="private"):
        EmailBackend(alias="default").send_messages([message])


def test_test_emails_are_limited(client: Client) -> None:
    save(client)
    statuses = [
        client.post("/admin/settings/email/test", secure=True).status_code for _ in range(6)
    ]
    assert statuses == [302] * 5 + [429]


def test_the_code_is_checked_before_any_dns_lookup(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Otherwise a stolen session could learn which internal names exist, codeless."""

    def lookup(host: str, port: int) -> list[str]:
        raise AssertionError("looked up before the code was checked")

    monkeypatch.setattr(mail, "_addresses", lookup)
    response = save(client, code="000000")
    assert response.status_code == 400
    assert 'id="email-code-error"' in response.content.decode()


def test_the_connection_goes_to_the_address_that_was_checked(
    public_servers: dict[str, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not to whatever the name resolves to a moment later (DNS rebinding)."""
    import socket

    from snug.core import email_backend

    config(domain="room.fernlea.example", smtp_host="")
    public_servers["smtp.postmark.example"] = ["93.184.216.34"]
    reached: list[tuple[str, int]] = []

    def connect(target: tuple[str, int], *args: Any, **kwargs: Any) -> Any:
        reached.append(target)
        raise ConnectionRefusedError

    monkeypatch.setattr(socket, "create_connection", connect)
    for security in ("starttls", "tls"):
        backend = email_backend._PinnedSmtpBackend(
            address="93.184.216.34",
            host="smtp.postmark.example",
            port=2525,
            username="",
            password="",
            use_tls=security == "starttls",
            use_ssl=security == "tls",
            timeout=5,
            alias="default",
        )
        with pytest.raises(ConnectionRefusedError):
            backend.connection_class("smtp.postmark.example", 2525, timeout=5)
    assert reached == [("93.184.216.34", 2525), ("93.184.216.34", 2525)]


def test_clearing_forgets_the_smtp_settings(client: Client) -> None:
    save(client)
    form = {"code": fresh_code(Admin.objects.get(email="ruth@fernlea.example"))}
    response = client.post("/admin/settings/email/clear", form, secure=True)
    assert response["Location"] == "/admin/settings/?saved=email-cleared#email"
    row = EmailSettings.load()
    assert (row.provider, row.smtp_host, row.smtp_password, row.from_address) == ("", "", "", "")
    assert row.smtp_port is None


def test_there_is_nothing_to_clear_until_something_is_saved(client: Client) -> None:
    assert 'id="email-clear"' not in panel(client)


def test_any_saved_setting_can_be_cleared_even_half_a_setup(client: Client) -> None:
    """A Google client saved without a mailbox is configuration too, though it sends nothing."""
    row = EmailSettings.load()
    row.google_client_id = "123456789-abcdef.apps.googleusercontent.com"
    row.save()
    assert 'id="email-clear"' in panel(client)


@pytest.mark.parametrize(("port", "security"), [("2587", "starttls"), ("2465", "tls")])
def test_amazon_ses_ports_work_from_digitalocean(client: Client, port: str, security: str) -> None:
    """DigitalOcean blocks 25, 465 and 587; Amazon SES listens on 2587 and 2465 as well."""
    response = save(client, smtp_port=port, smtp_security=security)
    assert response["Location"] == "/admin/settings/?saved=email#email-test"
    assert EmailSettings.load().smtp_port == int(port)


def test_the_port_hints_name_every_port(client: Client) -> None:
    text = panel(client)
    assert "25, 465, 587, 2465, 2525, or 2587. On DigitalOcean, use 2525" in text
    assert "STARTTLS, for ports 587, 2525, and 2587" in text
    assert "TLS, for ports 465 and 2465" in text
