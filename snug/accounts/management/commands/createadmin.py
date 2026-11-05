"""`snug createadmin`: create an admin, with a password and an authenticator.

The first admin of an instance is always created this way, on the server.
Nothing is saved until the authenticator is confirmed with a working code.
"""

import getpass
from argparse import ArgumentParser
from typing import Any

from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from snug.accounts import recovery
from snug.accounts.enrolment import EnrolmentFailedError, enrol, recovery_codes_message
from snug.accounts.models import Admin
from snug.audit.models import AuditEvent, record

PASSWORD_ATTEMPTS = 3


class Command(BaseCommand):
    help = "Create an admin, with a password and an authenticator app."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("--email", help="The new admin's email address.")

    def say(self, text: str) -> None:
        self.stdout.write(text)

    def handle(self, *args: Any, **options: Any) -> None:
        email = Admin.objects.normalize_email(options["email"] or input("Email address: "))
        try:
            validate_email(email)
        except ValidationError as error:
            raise CommandError(f"{email!r} is not a valid email address.") from error
        if Admin.objects.filter(email=email).exists():
            raise CommandError(f"An admin with the address {email} already exists.")

        password = self.ask_password(email)
        try:
            secret, step = enrol(
                email=email, domain=settings.CONFIG.domain, ask=input, say=self.say
            )
        except EnrolmentFailedError as error:
            raise CommandError(str(error)) from error

        with transaction.atomic():
            admin = Admin(
                email=email,
                is_active=True,
                totp_secret=secret,
                totp_last_step=step,
                totp_confirmed_at=timezone.now(),
            )
            admin.set_password(password)
            admin.save()
            codes = recovery.issue(admin)
            record("admin.created", actor_type=AuditEvent.ActorType.CLI, target=email)

        self.say(recovery_codes_message(email, codes))
        self.say(self.style.SUCCESS(f"Admin {email} created."))

    def ask_password(self, email: str) -> str:
        for _ in range(PASSWORD_ATTEMPTS):
            password = getpass.getpass("Password: ")
            if getpass.getpass("Password again: ") != password:
                self.say("The passwords do not match.")
                continue
            try:
                validate_password(password, user=Admin(email=email))
            except ValidationError as error:
                for message in error.messages:
                    self.say(message)
                continue
            return password
        raise CommandError("No acceptable password was entered, so nothing has been saved.")
