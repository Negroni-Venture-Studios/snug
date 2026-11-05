"""Unexpected errors are logged with a traceback, and secrets never are.

Link tokens and setup tokens travel in URL paths, so any log line naming the
request path would otherwise carry them. AGENTS.md: never log tokens.
"""

import io
import logging
from collections.abc import Iterator

import pytest

from snug.core.redaction import RedactSecrets, redact

LINK = "Qm7xZ2pLk9_vR4tYw-8nB3cD"  # shaped like secrets.token_urlsafe output
SETUP = "s3Tup_TokEn-9988abcdXYZ"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"/v/{LINK}", "/v/[redacted]"),
        (f"/v/{LINK}/code", "/v/[redacted]/code"),
        (f"POST /v/{LINK}/d/abc/download", "POST /v/[redacted]/d/abc/download"),
        (f"https://room.example.com/v/{LINK}?x=1", "https://room.example.com/v/[redacted]?x=1"),
        (f"/admin/setup/{SETUP}", "/admin/setup/[redacted]"),
        ("/admin/documents/abc123", "/admin/documents/abc123"),  # needs a login, not a secret
        ("/v/", "/v/"),
    ],
)
def test_redact(text: str, expected: str) -> None:
    assert redact(text) == expected


@pytest.fixture
def captured() -> Iterator[io.StringIO]:
    """The console handler that settings.LOGGING installs, writing to a buffer."""
    handlers = [h for h in logging.getLogger().handlers if type(h) is logging.StreamHandler]
    assert len(handlers) == 1, "settings.LOGGING should install one console handler"
    handler = handlers[0]
    # Swapped directly: setStream would flush the old stream, which an earlier
    # test's output capture may already have closed.
    old, handler.stream = handler.stream, io.StringIO()
    yield handler.stream
    handler.stream = old


def test_a_server_error_is_logged_with_its_traceback_and_without_the_token(
    captured: io.StringIO,
) -> None:
    try:
        raise RuntimeError(f"could not finish /v/{LINK}/code")
    except RuntimeError:
        # What Django's handler writes for an unhandled exception in a view.
        logging.getLogger("django.request").error(
            "Internal Server Error: %s", f"/v/{LINK}/code", exc_info=True
        )
    out = captured.getvalue()
    assert "Internal Server Error: /v/[redacted]/code" in out
    assert "Traceback (most recent call last)" in out
    assert "RuntimeError: could not finish /v/[redacted]/code" in out
    assert LINK not in out


def test_gunicorn_errors_are_redacted_too(capsys: pytest.CaptureFixture[str]) -> None:
    from gunicorn.config import Config  # type: ignore[import-untyped]
    from gunicorn.glogging import Logger  # type: ignore[import-untyped]

    from snug import gunicorn_conf

    log = Logger(Config())  # writes to stderr, as in the container
    gunicorn_conf.on_starting(None)
    try:
        try:
            raise OSError("connection reset")
        except OSError:
            log.exception("Error handling request %s %s", "POST", f"/admin/setup/{SETUP}")
    finally:
        error_log = logging.getLogger("gunicorn.error")
        for f in [f for f in error_log.filters if isinstance(f, RedactSecrets)]:
            error_log.removeFilter(f)
    err = capsys.readouterr().err
    assert "Error handling request POST /admin/setup/[redacted]" in err
    assert "OSError: connection reset" in err
    assert SETUP not in err
