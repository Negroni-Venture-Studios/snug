"""The checks behind `snug doctor` and the Overview's status line.

Each check returns a Finding: whether it passed, and a sentence an operator
can act on. Cheap checks read the database or the data folder; the network
checks (DNS and HTTPS) and the worker's confinement take a few seconds.
"""

import contextlib
import http.client
import socket
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from django.conf import settings
from django.utils.timesince import timesince

from snug.core import geoip, mail
from snug.core.models import SystemCheck
from snug.office import previews
from snug.rendering import sandbox
from snug.rendering.worker import MIN_LANDLOCK_ABI, fully_confined

Status = Literal["ok", "problem", "skipped"]

# Housekeeping runs every hour; three missed runs is a problem.
HOUSEKEEPING_LATE = timedelta(hours=3)
# The backup container copies every 5 minutes; three missed copies is a problem.
BACKUP_LATE = timedelta(minutes=15)
# Caddy renews 30 days before expiry, so 14 days left means renewal is failing.
CERTIFICATE_SOON = timedelta(days=14)
# Landlock ABI 4 arrived in Linux 6.7.
_LINUX_FOR_ABI = {4: "6.7"}


@dataclass(frozen=True)
class Finding:
    label: str
    status: Status
    message: str

    @property
    def problem(self) -> bool:
        return self.status == "problem"


def _ok(label: str, message: str) -> Finding:
    return Finding(label, "ok", message)


def _problem(label: str, message: str) -> Finding:
    return Finding(label, "problem", message)


def _ago(then: datetime, now: datetime) -> str:
    if now - then < timedelta(minutes=1):
        return "less than a minute"
    return timesince(then, now)


def _day(moment: datetime) -> str:
    return f"{moment.day} {moment:%B %Y}"


def housekeeping(now: datetime) -> Finding:
    label = "Housekeeping"
    row = SystemCheck.objects.filter(name="housekeeping").first()
    if row is None:
        return _problem(label, "Housekeeping has never completed.")
    if now - row.checked_at > HOUSEKEEPING_LATE:
        return _problem(
            label,
            f"Housekeeping has not completed for {_ago(row.checked_at, now)}. "
            "It should run every hour.",
        )
    return _ok(label, f"Last completed {_ago(row.checked_at, now)} ago.")


def _read_time(text: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(text.strip())
    except ValueError:
        return None
    return moment if moment.tzinfo else None


def last_backup() -> datetime | None:
    """When the backup container last finished a complete copy to the bucket."""
    # A file that is not text is treated as missing (audit, 11 November 2026).
    with contextlib.suppress(OSError, UnicodeDecodeError):
        path = settings.CONFIG.data_dir / "backup" / "last-success"
        return _read_time(path.read_text(encoding="utf-8"))
    return None


def backups(now: datetime) -> Finding:
    """Reads what the backup container records in the data folder (deploy/backup.sh)."""
    label = "Backups"
    folder = settings.CONFIG.data_dir / "backup"
    success = last_backup()
    problem = ""
    with contextlib.suppress(OSError, UnicodeDecodeError):
        problem = (folder / "problem").read_text(encoding="utf-8").strip()[:200]
    if problem:
        if success is not None:
            problem += f" The last complete copy was {_ago(success, now)} ago."
        return _problem(label, problem)
    if success is None:
        return _problem(label, "Nothing has been backed up yet.")
    if now - success > BACKUP_LATE:
        return _problem(label, f"Nothing has been copied to the bucket for {_ago(success, now)}.")
    return _ok(label, f"Last copied to the bucket {_ago(success, now)} ago.")


def confinement() -> Finding:
    label = "Document worker"
    if settings.CONFIG.unsafe_unconfined_rendering:
        return _problem(
            label,
            "SNUG_UNSAFE_UNCONFINED_RENDERING is on, so documents are read unconfined. "
            "Never use it on a server.",
        )
    how = sandbox.confinement()
    if fully_confined(how):
        return _ok(label, f"Documents are read in a confined worker ({how}).")
    return _problem(
        label,
        f"The document worker cannot confine itself ({how}), so Snug will not read documents. "
        f"It needs Linux {_LINUX_FOR_ABI[MIN_LANDLOCK_ABI]} or later, with Landlock, seccomp "
        "and a memory limit.",
    )


def _on_localhost() -> bool:
    return bool(settings.CONFIG.domain == "localhost")


def _listed(items: list[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def dns() -> Finding:
    label = "DNS"
    domain = settings.CONFIG.domain
    if _on_localhost():
        return Finding(label, "skipped", "Not checked for localhost.")
    try:
        answers = socket.getaddrinfo(domain, 443, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        return _problem(
            label,
            f"{domain} does not resolve. Point its A record, and its AAAA record "
            "if the server has IPv6, at this server.",
        )
    families = {str(sockaddr[0]): family for family, *_, sockaddr in answers}
    addresses = sorted(families, key=lambda a: (families[a] == socket.AF_INET6, a))
    return _ok(label, f"{domain} points to {_listed(addresses)}.")


def _fetch_health(domain: str) -> tuple[int, datetime]:
    """GET https://domain/healthz, verifying the certificate: the status and its expiry."""
    connection = http.client.HTTPSConnection(
        domain, 443, timeout=10, context=ssl.create_default_context()
    )
    try:
        connection.connect()
        certificate = connection.sock.getpeercert()
        connection.request("GET", "/healthz", headers={"User-Agent": "snug-doctor"})
        response = connection.getresponse()
        response.read(1000)
    finally:
        connection.close()
    expires = ssl.cert_time_to_seconds(str(certificate["notAfter"]))  # type: ignore[index]
    return response.status, datetime.fromtimestamp(expires, UTC)


def https(now: datetime) -> Finding:
    label = "HTTPS"
    domain = settings.CONFIG.domain
    if _on_localhost():
        return Finding(label, "skipped", "Not checked for localhost.")
    try:
        status, expires = _fetch_health(domain)
    except ssl.SSLCertVerificationError as error:
        return _problem(
            label,
            f"The certificate for {domain} is not valid ({type(error).__name__}). "
            "Check Caddy's log.",
        )
    except (OSError, http.client.HTTPException) as error:
        return _problem(
            label,
            f"Snug cannot be reached at https://{domain} ({type(error).__name__}). "
            "Check its DNS records, that ports 80 and 443 are open, and that Caddy is running.",
        )
    if status != 200:
        return _problem(
            label,
            f"https://{domain}/healthz answered {status}, not 200. Check that Snug is running.",
        )
    if expires - now < CERTIFICATE_SOON:
        return _problem(
            label,
            f"The certificate for {domain} expires on {_day(expires)}, and Caddy has not "
            "renewed it. Check that ports 80 and 443 are open, and Caddy's log.",
        )
    return _ok(label, f"https://{domain} answers, with a certificate valid until {_day(expires)}.")


def email_setup() -> Finding:
    label = "Email"
    now = mail.outgoing()
    if now.kind == "off":
        return _problem(
            label,
            "Email is not set up, so investors cannot be sent codes and admins cannot be "
            "invited. Set it up in Settings, under Email; docs/email.md explains how.",
        )
    if now.kind == "outbox":
        return _ok(label, "Emails are written to the outbox folder, on localhost only.")
    if now.kind == "google":
        return _ok(label, f"Sending through Google Workspace as {now.from_address}.")
    return _ok(label, f"Sending through {now.host} as {now.from_address}.")


def geoip_data(now: datetime) -> Finding:
    """DB-IP's country data, which UK-only links need: this month's or last month's."""
    from snug.links.models import Link
    from snug.rooms.models import Room

    label = "GeoIP"
    in_use = (
        Room.objects.filter(uk_only=True).exists()
        or Link.objects.filter(uk_only=True, revoked_at__isnull=True).exists()
    )
    if not in_use:
        return Finding(label, "skipped", "Nothing is UK-only.")
    held = geoip.edition()
    if held is None:
        return _problem(
            label,
            "There is no country data yet, so UK-only links refuse everyone. Housekeeping "
            "downloads it from DB-IP; check that the server can reach download.db-ip.com.",
        )
    previous = f"{(now.replace(day=1) - timedelta(days=1)):%Y-%m}"
    if held < previous:
        return _problem(
            label,
            f"DB-IP's country data is from {held} and should be renewed monthly. Check that "
            "the server can reach download.db-ip.com.",
        )
    return _ok(label, f"DB-IP's country data from {held}, for UK-only links.")


def office(now: datetime) -> Finding:
    """The LibreOffice converter, if it is installed: running, and its image not out of date."""
    label = "Office converter"
    found = previews.state(now)
    if not found.installed:
        return Finding(label, "skipped", "Not installed. sudo snug update --with-office adds it.")
    if found.problem:
        return _problem(label, f"{found.problem} Check it with sudo docker compose logs office.")
    status = found.status
    assert status is not None  # noqa: S101 - a running converter has reported
    built = f"{status.built.day} {status.built:%B %Y}" if status.built else "an unknown date"
    if found.out_of_date:
        return _problem(
            label,
            f"LibreOffice {status.version}, in an image built on {built}. A newer image, with "
            "LibreOffice's latest security fixes, is waiting. Run sudo snug update.",
        )
    return _ok(label, f"LibreOffice {status.version}, in an image built on {built}.")


def everything(now: datetime) -> list[Finding]:
    """Every check except the email test, which needs someone to type a code."""
    return [
        housekeeping(now),
        backups(now),
        confinement(),
        dns(),
        https(now),
        email_setup(),
        geoip_data(now),
        office(now),
    ]


# The slower checks, run by housekeeping every hour and stored for the
# Overview, with the label each is shown under. "email" is recorded by
# snug.core.mail whenever an email fails, or succeeds.
_STORED = {"confinement": "Document worker", "dns": "DNS", "https": "HTTPS", "email": "Email"}


def record(name: str, found: Finding, now: datetime) -> None:
    SystemCheck.objects.update_or_create(
        name=name,
        defaults={"ok": not found.problem, "message": found.message[:300], "checked_at": now},
    )


def refresh(now: datetime) -> None:
    """Run the slower checks and store them. One that breaks never stops the others."""
    checks: dict[str, Callable[[], Finding]] = {
        "confinement": confinement,
        "dns": dns,
        "https": lambda: https(now),
    }
    for name, check in checks.items():
        try:
            found = check()
        except Exception:  # noqa: BLE001, S112 - a broken check must not stop housekeeping
            continue
        record(name, found, now)


def problems(now: datetime) -> list[Finding]:
    """What the Overview shows: live cheap checks, then the stored slower ones."""
    live = [housekeeping(now), backups(now), email_setup(), geoip_data(now), office(now)]
    found = [f for f in live if f.problem]
    stored = SystemCheck.objects.filter(name__in=_STORED, ok=False).order_by("name")
    found += [_problem(_STORED[row.name], row.message) for row in stored]
    return found
