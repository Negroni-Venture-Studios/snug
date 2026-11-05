"""gunicorn settings, loaded by `snug serve` with --config python:snug.gunicorn_conf."""

import logging
import subprocess
import sys
import threading
import time
from typing import Any

from snug.core.hardening import make_undumpable
from snug.core.redaction import RedactSecrets

log = logging.getLogger("snug.housekeeping")

# How often the master runs `snug housekeeping`, in seconds.
HOUSEKEEPING_EVERY = 60 * 60


def on_starting(server: Any) -> None:
    # The master process holds the secret key in its environment. Workers are
    # forked from it, so they inherit the flag as well.
    make_undumpable()
    # gunicorn logs the URI of a request it cannot handle, and an investor's
    # URI carries their link token. Workers inherit this filter when forked.
    logging.getLogger("gunicorn.error").addFilter(RedactSecrets())


def _housekeeping() -> None:
    # A fresh process each time: the master stays free of Django, and a
    # failure here never touches the processes serving requests. Nothing that
    # goes wrong in one run may stop the next: retention depends on it.
    while True:
        try:
            done = subprocess.run(
                [sys.executable, "-m", "snug", "housekeeping"], check=False, timeout=600
            )
            if done.returncode != 0:
                log.error("Housekeeping failed: exit status %s", done.returncode)
        except Exception as error:  # noqa: BLE001 - logged, and the loop goes on
            log.error("Housekeeping failed: %s", type(error).__name__)
        time.sleep(HOUSEKEEPING_EVERY)


def when_ready(server: Any) -> None:
    """The container has no cron, so the master runs housekeeping every hour."""
    threading.Thread(target=_housekeeping, name="housekeeping", daemon=True).start()
