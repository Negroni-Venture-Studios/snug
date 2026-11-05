"""`snug serve`: apply migrations, then hand the process over to gunicorn.

Migrations run on every start, so an operator who pulls a new image never has
to remember a separate step. gunicorn then replaces this process, so signals
from Docker reach it directly.
"""

import os
import sys
from argparse import ArgumentParser
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Apply migrations, then serve Snug with gunicorn."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("--bind", default="0.0.0.0:8000", help="Address to listen on.")
        parser.add_argument("--workers", type=int, default=2, help="gunicorn worker processes.")

    def handle(self, *args: Any, **options: Any) -> None:
        data_dir = settings.CONFIG.data_dir
        data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)

        call_command("migrate", interactive=False, verbosity=1)

        # No --access-logfile: viewer URLs carry link tokens, never to be logged.
        command = [
            sys.executable,
            "-m",
            "gunicorn",
            "snug.wsgi:application",
            "--bind",
            options["bind"],
            "--workers",
            str(options["workers"]),
            "--worker-tmp-dir",
            # Heartbeat files in memory, not on the container's disk.
            "/dev/shm" if Path("/dev/shm").is_dir() else "/tmp",  # noqa: S108
        ]
        self.stdout.write(f"Starting gunicorn on {options['bind']}.")
        self.stdout.flush()
        os.execv(sys.executable, command)  # noqa: S606 - fixed argv, no shell
