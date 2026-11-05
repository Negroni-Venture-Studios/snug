"""`snug reset-2fa EMAIL`: give an admin a new authenticator and recovery codes.

For an admin who has lost their phone and their recovery codes. It runs on the
server, so it needs the same access as creating the first admin.
"""

from argparse import ArgumentParser
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from snug.accounts import recovery
from snug.accounts.enrolment import EnrolmentFailedError, enrol, recovery_codes_message
from snug.accounts.models import Admin
from snug.audit.models import AuditEvent, record


class Command(BaseCommand):
    help = "Replace an admin's authenticator and recovery codes."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("email", help="The admin's email address.")

    def say(self, text: str) -> None:
        self.stdout.write(text)

    def handle(self, *args: Any, **options: Any) -> None:
        email = Admin.objects.normalize_email(options["email"])
        admin = Admin.objects.filter(email=email).first()
        if admin is None:
            raise CommandError(f"There is no admin with the address {email}.")

        answer = input(
            f"This replaces the authenticator and recovery codes for {email}. "
            "Their current ones stop working. Continue? [y/N] "
        )
        if answer.strip().lower() not in ("y", "yes"):
            self.say("Nothing has been changed.")
            return

        try:
            secret, step = enrol(
                email=email, domain=settings.CONFIG.domain, ask=input, say=self.say
            )
        except EnrolmentFailedError as error:
            raise CommandError(str(error)) from error

        with transaction.atomic():
            admin.totp_secret = secret
            admin.totp_last_step = step
            admin.totp_confirmed_at = timezone.now()
            admin.save(update_fields=["totp_secret", "totp_last_step", "totp_confirmed_at"])
            codes = recovery.issue(admin)
            record("admin.2fa_reset", actor_type=AuditEvent.ActorType.CLI, target=email)

        self.say(recovery_codes_message(email, codes))
        self.say(self.style.SUCCESS(f"New authenticator and recovery codes set for {email}."))
