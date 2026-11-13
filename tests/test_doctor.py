"""`snug doctor`: checks an operator runs on the server, each saying what is wrong.

It checks housekeeping, the backups, the document worker's confinement, the
domain's DNS and certificate, and email, by sending a code the operator types
back. Each check is a function returning a Finding, so the Overview can show
the same results.
"""

import dataclasses
import socket
import sys
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from typing import Any

import pytest
from django.conf import settings
from django.core import mail as django_mail
from django.core.management import call_command

from snug.analytics import retention
from snug.core import doctor
from snug.core.models import SystemCheck
from snug.rendering import sandbox

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 6, 15, 0, tzinfo=UTC)


def on(domain: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "CONFIG", dataclasses.replace(settings.CONFIG, domain=domain))


@pytest.fixture
def safe(monkeypatch: pytest.MonkeyPatch) -> None:
    """The test settings allow unconfined reading on macOS; a server never does."""
    monkeypatch.setattr(
        settings,
        "CONFIG",
        dataclasses.replace(settings.CONFIG, unsafe_unconfined_rendering=False),
    )


# ---------------------------------------------------------------- housekeeping


def test_housekeeping_records_when_it_last_succeeded() -> None:
    call_command("housekeeping", stdout=StringIO())
    row = SystemCheck.objects.get(name="housekeeping")
    assert row.ok
    assert abs((datetime.now(UTC) - row.checked_at).total_seconds()) < 60


def test_a_failed_housekeeping_records_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(**kwargs: Any) -> int:
        raise RuntimeError("disk full")

    monkeypatch.setattr(retention, "purge", broken)
    with pytest.raises(RuntimeError):
        call_command("housekeeping", stdout=StringIO())
    assert not SystemCheck.objects.filter(name="housekeeping").exists()


def housekept(ago: timedelta) -> None:
    SystemCheck.objects.create(name="housekeeping", ok=True, message="", checked_at=NOW - ago)


def test_housekeeping_that_has_never_run_is_a_problem() -> None:
    found = doctor.housekeeping(NOW)
    assert found.status == "problem"
    assert found.message == "Housekeeping has never completed."


def test_housekeeping_within_three_hours_is_fine() -> None:
    housekept(timedelta(hours=2, minutes=50))
    found = doctor.housekeeping(NOW)
    assert found.status == "ok"
    assert found.message == "Last completed 2\xa0hours, 50\xa0minutes ago."


def test_housekeeping_older_than_three_hours_is_a_problem() -> None:
    housekept(timedelta(hours=3, minutes=5))
    found = doctor.housekeeping(NOW)
    assert found.status == "problem"
    assert found.message == (
        "Housekeeping has not completed for 3\xa0hours, 5\xa0minutes. It should run every hour."
    )


# ---------------------------------------------------------------- backups


def backup_files(folder: Path, *, success: datetime | None = None, problem: str = "") -> None:
    (folder / "backup").mkdir(exist_ok=True)
    if success is not None:
        (folder / "backup" / "last-success").write_text(
            success.strftime("%Y-%m-%dT%H:%M:%SZ\n"), encoding="utf-8"
        )
    if problem:
        (folder / "backup" / "problem").write_text(problem + "\n", encoding="utf-8")


def test_no_backup_yet_is_a_problem(data_folder: Path) -> None:
    found = doctor.backups(NOW)
    assert found.status == "problem"
    assert found.message == "Nothing has been backed up yet."


def test_a_backup_this_minute(data_folder: Path) -> None:
    backup_files(data_folder, success=NOW - timedelta(seconds=20))
    assert doctor.backups(NOW).message == "Last copied to the bucket less than a minute ago."


def test_a_recent_backup_is_fine(data_folder: Path) -> None:
    backup_files(data_folder, success=NOW - timedelta(minutes=4))
    found = doctor.backups(NOW)
    assert found.status == "ok"
    assert found.message == "Last copied to the bucket 4\xa0minutes ago."


def test_an_old_backup_is_a_problem(data_folder: Path) -> None:
    backup_files(data_folder, success=NOW - timedelta(minutes=17))
    found = doctor.backups(NOW)
    assert found.status == "problem"
    assert found.message == "Nothing has been copied to the bucket for 17\xa0minutes."


def test_the_backup_container_says_what_went_wrong(data_folder: Path) -> None:
    backup_files(
        data_folder,
        success=NOW - timedelta(hours=2),
        problem="Copying files to the bucket failed at 2026-11-06T14:55:00Z.",
    )
    found = doctor.backups(NOW)
    assert found.status == "problem"
    assert found.message == (
        "Copying files to the bucket failed at 2026-11-06T14:55:00Z. "
        "The last complete copy was 2\xa0hours ago."
    )


def test_backups_not_set_up(data_folder: Path) -> None:
    not_set_up = "Backups are not set up. Run sudo snug setup to add a bucket."
    backup_files(data_folder, problem=not_set_up)
    assert doctor.backups(NOW).message == not_set_up


def test_an_unreadable_time_is_a_problem(data_folder: Path) -> None:
    backup_files(data_folder)
    (data_folder / "backup" / "last-success").write_text("yesterday\n", encoding="utf-8")
    assert doctor.backups(NOW).status == "problem"


# ---------------------------------------------------------------- confinement


def test_the_worker_reports_how_it_is_confined() -> None:
    how = sandbox.confinement()
    if sys.platform == "linux":
        assert how.startswith("landlock v"), how
    else:
        assert how == "unconfined: not Linux"


@pytest.mark.usefixtures("safe")
def test_a_fully_confined_worker_is_fine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sandbox, "confinement", lambda: "landlock v6, no sockets")
    on("room.fernlea.example", monkeypatch)
    found = doctor.confinement()
    assert found.status == "ok"
    assert found.message == "Documents are read in a confined worker (landlock v6, no sockets)."


@pytest.mark.usefixtures("safe")
@pytest.mark.parametrize(
    "how",
    ["landlock v3, no sockets", "landlock v6, socket filter refused", "unconfined: not Linux"],
)
def test_anything_less_is_a_problem(how: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sandbox, "confinement", lambda: how)
    found = doctor.confinement()
    assert found.status == "problem"
    assert found.message == (
        f"The document worker cannot confine itself ({how}), so Snug will not read documents. "
        "It needs Linux 6.7 or later, with Landlock, seccomp and a memory limit."
    )


def test_unconfined_rendering_is_a_problem(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sandbox, "confinement", lambda: "unconfined: not Linux")
    monkeypatch.setattr(
        settings,
        "CONFIG",
        dataclasses.replace(settings.CONFIG, unsafe_unconfined_rendering=True),
    )
    found = doctor.confinement()
    assert found.status == "problem"
    assert found.message == (
        "SNUG_UNSAFE_UNCONFINED_RENDERING is on, so documents are read unconfined. "
        "Never use it on a server."
    )


# ---------------------------------------------------------------- DNS and HTTPS


def test_dns_and_https_are_not_checked_on_localhost(monkeypatch: pytest.MonkeyPatch) -> None:
    on("localhost", monkeypatch)
    for found in (doctor.dns(), doctor.https(NOW)):
        assert found.status == "skipped"
        assert found.message == "Not checked for localhost."


def test_a_domain_that_does_not_resolve(monkeypatch: pytest.MonkeyPatch) -> None:
    on("room.fernlea.example", monkeypatch)

    def nowhere(*args: Any, **kwargs: Any) -> Any:
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", nowhere)
    found = doctor.dns()
    assert found.status == "problem"
    assert found.message == (
        "room.fernlea.example does not resolve. Point its A record, and its AAAA record "
        "if the server has IPv6, at this server."
    )


def test_a_domain_that_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    on("room.fernlea.example", monkeypatch)
    answers = [
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:db8::7", 443, 0, 0)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.7", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.7", 443)),
    ]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: answers)
    found = doctor.dns()
    assert found.status == "ok"
    assert found.message == "room.fernlea.example points to 203.0.113.7 and 2001:db8::7."


def fetched(monkeypatch: pytest.MonkeyPatch, result: Any) -> None:
    on("room.fernlea.example", monkeypatch)

    def fetch(domain: str) -> tuple[int, datetime]:
        assert domain == "room.fernlea.example"
        if isinstance(result, Exception):
            raise result
        return result  # type: ignore[no-any-return]

    monkeypatch.setattr(doctor, "_fetch_health", fetch)


def test_https_with_a_good_certificate(monkeypatch: pytest.MonkeyPatch) -> None:
    fetched(monkeypatch, (200, NOW + timedelta(days=60)))
    found = doctor.https(NOW)
    assert found.status == "ok"
    assert found.message == (
        "https://room.fernlea.example answers, with a certificate valid until 5 January 2027."
    )


def test_a_certificate_close_to_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    fetched(monkeypatch, (200, NOW + timedelta(days=9)))
    found = doctor.https(NOW)
    assert found.status == "problem"
    assert found.message == (
        "The certificate for room.fernlea.example expires on 15 November 2026, and Caddy "
        "has not renewed it. Check that ports 80 and 443 are open, and Caddy's log."
    )


def test_an_invalid_certificate(monkeypatch: pytest.MonkeyPatch) -> None:
    import ssl

    fetched(monkeypatch, ssl.SSLCertVerificationError("certificate has expired"))
    found = doctor.https(NOW)
    assert found.status == "problem"
    assert found.message == (
        "The certificate for room.fernlea.example is not valid (SSLCertVerificationError). "
        "Check Caddy's log."
    )


def test_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    fetched(monkeypatch, ConnectionRefusedError("refused"))
    found = doctor.https(NOW)
    assert found.status == "problem"
    assert found.message == (
        "Snug cannot be reached at https://room.fernlea.example (ConnectionRefusedError). "
        "Check its DNS records, that ports 80 and 443 are open, and that Caddy is running."
    )


def test_a_wrong_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    fetched(monkeypatch, (502, NOW + timedelta(days=60)))
    found = doctor.https(NOW)
    assert found.status == "problem"
    assert found.message == (
        "https://room.fernlea.example/healthz answered 502, not 200. Check that Snug is running."
    )


# ---------------------------------------------------------------- email


def test_email_that_is_not_set_up_is_a_problem() -> None:
    found = doctor.email_setup()
    assert found.status == "problem"
    assert found.message == (
        "Email is not set up, so investors cannot be sent codes and admins cannot be "
        "invited. Set it up in Settings, under Email; docs/email.md explains how."
    )


@pytest.mark.usefixtures("email_on")
def test_email_that_is_set_up() -> None:
    found = doctor.email_setup()
    assert found.status == "ok"
    assert found.message == "Sending through smtp.example.com as rooms@fernlea.example."


# ---------------------------------------------------------------- the command


@pytest.fixture
def healthy(monkeypatch: pytest.MonkeyPatch, data_folder: Path, safe: None) -> None:
    """Everything passes but email, which each test sets up as it needs."""
    on("room.fernlea.example", monkeypatch)
    now = datetime.now(UTC)
    SystemCheck.objects.create(name="housekeeping", ok=True, message="", checked_at=now)
    backup_files(data_folder, success=now)
    monkeypatch.setattr(sandbox, "confinement", lambda: "landlock v6, no sockets")
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.7", 443))],
    )
    monkeypatch.setattr(doctor, "_fetch_health", lambda domain: (200, now + timedelta(days=60)))


def run_doctor(*args: str) -> tuple[str, int]:
    out = StringIO()
    try:
        call_command("doctor", *args, stdout=out)
    except SystemExit as exit_:
        return out.getvalue(), int(exit_.code or 0)
    return out.getvalue(), 0


@pytest.mark.usefixtures("healthy", "email_on")
def test_the_email_test_sends_a_code_to_type_back(monkeypatch: pytest.MonkeyPatch) -> None:
    def type_it_back(prompt: str) -> str:
        assert prompt == "Type the code from the email: "
        (message,) = django_mail.outbox
        return next(word for word in message.body.split() if word.isdigit() and len(word) == 6)

    monkeypatch.setattr("builtins.input", type_it_back)
    report, status = run_doctor("--email", "ruth@fernlea.example")
    (message,) = django_mail.outbox
    assert message.to == ["ruth@fernlea.example"]
    assert message.subject == "Snug: a test email from snug doctor"
    assert "ok        Email" in report
    assert "The code came back: email reaches ruth@fernlea.example." in report
    assert status == 0
    assert "Everything is working." in report


@pytest.mark.usefixtures("healthy", "email_on")
def test_a_wrong_code_is_a_problem(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("builtins.input", lambda prompt: "000000")
    report, status = run_doctor("--email", "ruth@fernlea.example")
    assert "problem   Email" in report
    assert "That is not the code that was sent." in report
    assert status == 1


@pytest.mark.usefixtures("healthy", "email_on")
def test_without_an_address_the_send_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    report, status = run_doctor()
    assert not django_mail.outbox
    assert "skipped   Email test" in report
    assert "Run sudo snug doctor --email you@example.com to send a test email." in report
    assert status == 0


@pytest.mark.usefixtures("healthy")
def test_the_report_lists_every_check_and_fails_on_a_problem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setenv("SNUG_BUILD_VERSION", "1.4.2")
    report, status = run_doctor()
    labels = [line.split("  ")[0:2] for line in report.splitlines()]
    # The version leads, so a report pasted into an issue includes it (13 November 2026).
    assert report.splitlines()[0] == "Snug doctor, Snug 1.4.2"
    for name in ("Housekeeping", "Backups", "Document worker", "DNS", "HTTPS", "Email"):
        assert any(name in line for line in report.splitlines()), (name, labels)
    assert "problem   Email" in report  # not set up in this test
    assert report.rstrip().endswith("1 problem.")
    assert status == 1


@pytest.mark.usefixtures("healthy")
def test_every_message_starts_in_one_column_clear_of_the_longest_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    report, _status = run_doctor()
    lines = [
        line for line in report.splitlines() if line[:10].strip() in {"ok", "problem", "skipped"}
    ]
    assert any(line[10:].startswith("Document worker  ") for line in lines), report
    labels = {line[10:].split("  ")[0] for line in lines}
    starts = {len(line) - len(line[10:].split("  ", 1)[1].lstrip()) for line in lines}
    assert len(starts) == 1, report
    assert starts.pop() == 10 + max(len(label) for label in labels) + 2, report
