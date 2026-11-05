"""gunicorn settings, loaded by `snug serve` with --config python:snug.gunicorn_conf."""

from typing import Any

from snug.core.hardening import make_undumpable


def on_starting(server: Any) -> None:
    # The master process holds the secret key in its environment. Workers are
    # forked from it, so they inherit the flag as well.
    make_undumpable()
