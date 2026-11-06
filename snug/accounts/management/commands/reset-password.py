"""`snug reset-password EMAIL`: set a new password for an admin, on the server.

For an instance without email, or an admin who cannot use the emailed link.
It runs on the server, so it needs the same access as creating the first
admin. Unlike Django's `changepassword`, it is written to the audit log and
the admin is emailed. Their authenticator is unchanged.
"""

import getpass
from argparse import ArgumentParser
from typing import Any

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from snug.accounts import passwords
from snug.accounts.models import Admin
from snug.audit.models import AuditEvent, record


class Command(BaseCommand):
    help = "Set a new password for an admin. Their authenticator is unchanged."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("email", help="The admin's email address.")

    def handle(self, *args: Any, **options: Any) -> None:
        email = Admin.objects.normalize_email(options["email"])
        admin = Admin.objects.filter(email=email).first()
        if admin is None:
            raise CommandError(f"There is no admin with the address {email}.")

        password = getpass.getpass("New password: ")
        if password != getpass.getpass("New password again: "):
            raise CommandError("The passwords do not match. Nothing has been changed.")
        try:
            validate_password(password, user=admin)
        except ValidationError as problem:
            raise CommandError(" ".join(problem.messages)) from problem

        with transaction.atomic():
            admin.set_password(password)
            admin.save(update_fields=["password"])
            record(
                "admin.password_reset",
                actor_type=AuditEvent.ActorType.CLI,
                target=email,
                details={"method": "cli"},
            )
        passwords.send_changed(admin, how="on the server")
        self.stdout.write(
            self.style.SUCCESS(f"New password set for {email}. They are signed out everywhere.")
        )
