"""`snug doctor`: check that this Snug is working, and say what to fix if not.

Exits with status 1 if anything is a problem, so it can be scripted.
"""

import hmac
import secrets
import sys
from argparse import ArgumentParser
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from snug.core import doctor, mail
from snug.core.doctor import Finding
from snug.core.version import running


def _email_test(address: str) -> Finding:
    """Send a code, and ask the operator to type it back: Snug cannot read their inbox."""
    label = "Email test"
    code = f"{secrets.randbelow(10**6):06d}"
    sent = mail.send(
        to=address,
        subject="Snug: a test email from snug doctor",
        text=(
            f"This is a test from snug doctor on {settings.CONFIG.domain}. The code is:\n\n"
            f"{code}\n\n"
            "Type it into the terminal where snug doctor is running. If you did not run "
            "snug doctor, someone with access to your server did.\n"
        ),
    )
    if not sent:
        return Finding(
            label,
            "problem",
            f"The email to {address} could not be sent. The reason is in Snug's log: "
            "sudo docker compose logs snug, in /opt/snug.",
        )
    typed = input("Type the code from the email: ").strip()
    if not hmac.compare_digest(typed.encode(), code.encode()):
        return Finding(label, "problem", "That is not the code that was sent.")
    return Finding(label, "ok", f"The code came back: email reaches {address}.")


class Command(BaseCommand):
    help = "Check housekeeping, backups, the document worker, DNS, HTTPS, email, and GeoIP data."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument(
            "--email",
            metavar="ADDRESS",
            default="",
            help="Send a test email to this address, then type back the code in it.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        findings = doctor.everything(timezone.now())
        if not any(f.label == "Email" and f.problem for f in findings):
            address = options["email"].strip()
            if not address and sys.stdin.isatty():
                address = input("Send a test email to (leave empty to skip): ").strip()
            if address:
                findings.append(_email_test(address))
            else:
                findings.append(
                    Finding(
                        "Email test",
                        "skipped",
                        "Run sudo snug doctor --email you@example.com to send a test email.",
                    )
                )

        self.stdout.write(f"Snug doctor, {running().name}\n")
        width = max(len(f.label) for f in findings) + 2
        for found in findings:
            self.stdout.write(f"{found.status:<10}{found.label:<{width}}{found.message}")
        problems = sum(f.problem for f in findings)
        if problems:
            self.stdout.write(f"\n{problems} problem{'s' if problems != 1 else ''}.")
            sys.exit(1)
        self.stdout.write("\nEverything is working.")
