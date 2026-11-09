"""The Overview's status line: any problem `snug doctor` would report, without a command.

Housekeeping, the backups and whether email is set up are read live. The
slower checks (the document worker, DNS and HTTPS) are run by housekeeping every
hour and stored, and a failed email is recorded when it fails.
"""

import dataclasses
import smtplib
import time
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from typing import Any

import pytest
from django.conf import settings
from django.core.management import call_command
from django.test import Client

from snug.accounts import sessions
from snug.accounts.models import Admin
from snug.core import doctor, mail
from snug.core.models import SystemCheck
from snug.rendering import sandbox

pytestmark = pytest.mark.django_db


@pytest.fixture
def client() -> Client:
    admin = Admin.objects.create(email="ruth@fernlea.example", is_active=True)
    browser = Client()
    browser.force_login(admin)
    session = browser.session
    session[sessions.LOGIN_AT] = session[sessions.LAST_ACTIVITY] = time.time()
    session.save()
    return browser


@pytest.fixture
def all_well(data_folder: Path, monkeypatch: pytest.MonkeyPatch, email_on: None) -> None:
    now = datetime.now(UTC)
    SystemCheck.objects.create(name="housekeeping", ok=True, message="", checked_at=now)
    (data_folder / "backup").mkdir()
    (data_folder / "backup" / "last-success").write_text(
        now.strftime("%Y-%m-%dT%H:%M:%SZ"), encoding="utf-8"
    )


def status_of(client: Client) -> str:
    page = client.get("/admin/", secure=True).content.decode()
    start = page.index('<div class="system-status')
    return page[start : page.index("</div>", start)]


@pytest.mark.usefixtures("all_well")
def test_when_everything_works_it_says_so(client: Client) -> None:
    line = status_of(client)
    assert "system-status-ok" in line
    assert "Everything is working." in line


@pytest.mark.usefixtures("all_well")
def test_a_late_backup_is_shown(client: Client, data_folder: Path) -> None:
    then = datetime.now(UTC) - timedelta(minutes=40)
    (data_folder / "backup" / "last-success").write_text(
        then.strftime("%Y-%m-%dT%H:%M:%SZ"), encoding="utf-8"
    )
    line = status_of(client)
    assert 'class="system-status notice notice-warn" role="alert"' in line
    assert "<strong>Backups.</strong> Nothing has been copied to the bucket for 40" in line
    assert "Run <code>snug doctor</code> on the server for more." in line


@pytest.mark.usefixtures("all_well")
def test_failing_housekeeping_is_shown(client: Client) -> None:
    SystemCheck.objects.filter(name="housekeeping").update(
        checked_at=datetime.now(UTC) - timedelta(hours=5)
    )
    assert "<strong>Housekeeping.</strong> Housekeeping has not completed" in status_of(client)


@pytest.mark.usefixtures("all_well")
def test_stored_problems_are_shown(client: Client) -> None:
    SystemCheck.objects.create(
        name="https",
        ok=False,
        message="The certificate for room.fernlea.example is not valid.",
        checked_at=datetime.now(UTC),
    )
    assert "<strong>HTTPS.</strong> The certificate for room.fernlea.example is not valid." in (
        status_of(client)
    )


@pytest.mark.usefixtures("all_well")
def test_it_shows_on_an_empty_instance_too(client: Client) -> None:
    page = client.get("/admin/", secure=True).content.decode()
    assert "Nothing to show yet" in page
    assert "Everything is working." in page


def test_email_not_set_up_is_shown(client: Client, data_folder: Path) -> None:
    assert "<strong>Email.</strong> Email is not set up" in status_of(client)


# ---------------------------------------------------------------- stored slow checks


def test_housekeeping_stores_the_slow_checks(
    monkeypatch: pytest.MonkeyPatch, data_folder: Path
) -> None:
    monkeypatch.setattr(
        settings,
        "CONFIG",
        dataclasses.replace(
            settings.CONFIG, domain="room.fernlea.example", unsafe_unconfined_rendering=False
        ),
    )
    monkeypatch.setattr(sandbox, "confinement", lambda: "landlock v6, no sockets")
    monkeypatch.setattr(
        doctor,
        "dns",
        lambda: doctor.Finding("DNS", "problem", "room.fernlea.example does not resolve."),
    )
    monkeypatch.setattr(
        doctor, "https", lambda now: doctor.Finding("HTTPS", "ok", "https answers.")
    )
    call_command("housekeeping", stdout=StringIO())
    rows = {row.name: row for row in SystemCheck.objects.all()}
    assert rows["confinement"].ok
    assert not rows["dns"].ok
    assert rows["dns"].message == "room.fernlea.example does not resolve."
    assert rows["https"].ok


def test_a_check_that_breaks_never_breaks_housekeeping(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args: Any) -> doctor.Finding:
        raise RuntimeError("surprise")

    monkeypatch.setattr(doctor, "https", broken)
    call_command("housekeeping", stdout=StringIO())
    assert SystemCheck.objects.get(name="housekeeping").ok
    assert not SystemCheck.objects.filter(name="https").exists()


# ---------------------------------------------------------------- failed email


@pytest.mark.usefixtures("email_on")
def test_a_failed_email_is_recorded_and_a_later_success_clears_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from django.core.mail import EmailMultiAlternatives

    def refuse(self: Any, fail_silently: bool = False) -> int:
        raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    with monkeypatch.context() as patch:
        patch.setattr(EmailMultiAlternatives, "send", refuse)
        assert not mail.send(to="priya@northfield.example", subject="Code", text="123456")
    row = SystemCheck.objects.get(name="email")
    assert not row.ok
    assert row.message == (
        "An email could not be sent (SMTPAuthenticationError). "
        "Run snug doctor --email you@example.com to test."
    )
    assert "priya" not in row.message and "123456" not in row.message

    assert mail.send(to="priya@northfield.example", subject="Code", text="123456")
    assert SystemCheck.objects.get(name="email").ok
