"""Keep secrets out of the logs.

Investor link tokens (/v/<token>) and admin setup tokens (/admin/setup/<token>)
travel in URL paths. Django logs the path of a request that fails, and gunicorn
logs the URI of one it cannot handle, so both pass through this filter before
anything is written. AGENTS.md: never log tokens.
"""

import logging
import re


def _spelled_any_way(path: str) -> str:
    """A pattern for PATH with each character as itself or percent-encoded.

    gunicorn logs the raw request line of a request it refuses, so a path
    such as /%76/<token>, which Django would decode to /v/<token>, reaches
    the log as it was sent (follow-up audit of 1.2.0).
    """
    return "".join(f"(?:{re.escape(char)}|%{ord(char):02x})" for char in path)


_SECRET_PATH = re.compile(
    f"({_spelled_any_way('/v/')}|{_spelled_any_way('/admin/setup/')})" + r"[^/\s?#\"']+",
    re.IGNORECASE,  # for %2f as well as %2F; that /V/ is redacted too does no harm
)
_formatter = logging.Formatter()


# Google's one-time code and state, on the way back from connecting a mailbox.
_SECRET_QUERY = re.compile(r"([?&](?:code|state)=)[^&\s#\"']+")


def redact(text: str) -> str:
    return _SECRET_QUERY.sub(r"\1[redacted]", _SECRET_PATH.sub(r"\1[redacted]", text))


class RedactSecrets(logging.Filter):
    """Rewrites a record's message and traceback with the secrets blanked out.

    Install it on a handler (it then sees every record that handler writes) or
    on the logger where records start, such as gunicorn.error.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None
        if record.exc_info and not record.exc_text:
            # Formatters reuse exc_text when it is set, so the traceback they
            # print is this redacted one. The exception message may name a path.
            record.exc_text = _formatter.formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return True
