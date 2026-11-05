"""gunicorn settings, loaded by `snug serve` with --config python:snug.gunicorn_conf."""

import subprocess
import sys
import threading
import time
from typing import Any

from snug.core.hardening import make_undumpable

# How often the master runs `snug housekeeping`, in seconds.
HOUSEKEEPING_EVERY = 60 * 60


def on_starting(server: Any) -> None:
    # The master process holds the secret key in its environment. Workers are
    # forked from it, so they inherit the flag as well.
    make_undumpable()


def _housekeeping() -> None:
    # A fresh process each time: the master stays free of Django, and a
    # failure here never touches the processes serving requests.
    while True:
        subprocess.run([sys.executable, "-m", "snug", "housekeeping"], check=False, timeout=600)
        time.sleep(HOUSEKEEPING_EVERY)


def when_ready(server: Any) -> None:
    """The container has no cron, so the master runs housekeeping every hour."""
    threading.Thread(target=_housekeeping, name="housekeeping", daemon=True).start()
