"""Replaces Django's `changepassword`, which writes nothing to the audit log."""

from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Use `sudo snug reset-password EMAIL` instead, which is audited."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("args", nargs="*")

    def handle(self, *args: Any, **options: Any) -> None:
        raise CommandError(
            "Use sudo snug reset-password EMAIL instead: it records the change in the "
            "audit log and emails the admin."
        )
