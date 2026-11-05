"""Keep secrets out of the logs.

Investor link tokens (/v/<token>) and admin setup tokens (/admin/setup/<token>)
travel in URL paths. Django logs the path of a request that fails, and gunicorn
logs the URI of one it cannot handle, so both pass through this filter before
anything is written. AGENTS.md: never log tokens.
"""

import logging
import re

_SECRET_PATH = re.compile(r"(/v/|/admin/setup/)[^/\s?#\"']+")
_formatter = logging.Formatter()


def redact(text: str) -> str:
    return _SECRET_PATH.sub(r"\1[redacted]", text)


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
